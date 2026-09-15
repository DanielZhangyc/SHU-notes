import copy
import json
import unittest

from scripts.publish_notes import CONFIG, CONTENT, POSTS, ROOT, META_BLOCK, discover_notes, parse_metadata, strip_metadata, render_note, sync


class FakeHalo:
    def __init__(self, post=None):
        self.post = post
        self.content = "old"
        self.writes = []

    def list_all(self, path):
        if path == POSTS:
            return [{"post": copy.deepcopy(self.post)}] if self.post else []
        name = "学习笔记" if path.endswith("categories") else "数学"
        return [{"metadata": {"name": name}, "spec": {"displayName": name}}]

    def request(self, method, path, data=None):
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(data)))
        if method == "POST":
            self.post = copy.deepcopy(data)
            self.post["metadata"]["version"] = 1
            self.post["spec"]["headSnapshot"] = "v1"
            self.content = json.loads(data["metadata"]["annotations"][CONTENT])["content"]
        elif method == "GET" and "/draft" in path:
            return {"metadata": {"name": "v1", "version": 7, "annotations": {"content.halo.run/patched-content": self.content}}}
        elif method == "PUT" and path.endswith("/draft"):
            assert data["metadata"]["version"] == 7
            self.content = json.loads(data["metadata"]["annotations"][CONTENT])["content"]
            self.post["spec"]["headSnapshot"] = "v2"
        elif method == "PUT" and path.endswith("/publish"):
            self.post["spec"]["publish"] = True
            self.post["spec"]["releaseSnapshot"] = self.post["spec"]["headSnapshot"]
        elif method == "PUT":
            self.post = copy.deepcopy(data)
        return copy.deepcopy(self.post)


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG.read_text())
        self.note = next(
            note for note in discover_notes(self.config)
            if note["source"] == "mathematics/mathematical-analysis-i/notes/01-02-sets-and-suprema.tex"
        )

    def test_real_note_conversion(self):
        page = render_note(self.note, self.config)
        for text in ("定义 1.1", "命题 2.2", "例题 3.2", "定理 4.3", "例题 5.1", "证明."):
            self.assertIn(text, page)
        self.assertNotIn("halo:begin", page)
        self.assertNotIn("study-notes", page)
        self.assertNotIn("Proof.", page)
        self.assertNotIn("<h1", page)
        self.assertIn("<math", page)
        self.assertIn("<mfrac>", page)
        self.assertIn("github.com/DanielZhangyc/SHU-notes/blob/main/", page)

    def test_create_repeat_and_update(self):
        halo = FakeHalo()
        sync(halo, [(self.note, "first")])
        self.assertTrue(halo.post["spec"]["publish"])
        self.assertEqual(halo.post["spec"]["categories"], ["学习笔记"])
        halo.writes.clear()
        sync(halo, [(self.note, "first")])
        self.assertEqual(halo.writes, [])
        sync(halo, [(self.note, "updated")])
        self.assertEqual(halo.content, "updated")
        self.assertFalse(any(method == "POST" for method, _, _ in halo.writes))
        self.assertEqual(halo.post["spec"]["releaseSnapshot"], "v2")

    def test_check_is_read_only(self):
        halo = FakeHalo()
        sync(halo, [(self.note, "content")], check_only=True)
        self.assertEqual(halo.writes, [])

    def test_missing_taxonomy_and_collision_fail_before_writes(self):
        halo = FakeHalo()
        note = {**self.note, "tags": ["missing"]}
        with self.assertRaises(ValueError):
            sync(halo, [(note, "content")])
        self.assertEqual(halo.writes, [])
        halo.post = {"metadata": {"name": "unrelated", "annotations": {}}, "spec": {"slug": self.note["slug"]}}
        with self.assertRaises(ValueError):
            sync(halo, [(self.note, "content")])
        self.assertEqual(halo.writes, [])


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG.read_text())
        self.source = "mathematics/mathematical-analysis-i/notes/01-02-sets-and-suprema.tex"
        self.tex = (ROOT / self.source).read_text()
        block = META_BLOCK.search(self.tex)[1]
        self.metadata = json.loads("\n".join(line[2:] for line in block.splitlines()))

    def document(self, metadata):
        block = "% halo:begin\n" + "\n".join("% " + line for line in json.dumps(metadata, ensure_ascii=False, indent=2).splitlines()) + "\n% halo:end\n"
        return block + strip_metadata(self.tex)

    def test_valid_metadata_and_template(self):
        note = parse_metadata(self.tex, self.source, self.config)
        self.assertEqual(note["title"], "数学分析（一）1.2：数集与确界原理")
        self.assertEqual(note["categories"], ["学习笔记"])
        self.assertEqual(note["tags"], ["数学"])
        self.assertEqual(note["slug"], "mathematical-analysis-i-01-02-sets-and-suprema")
        self.assertIsNone(parse_metadata((ROOT / "template.tex").read_text(), "template.tex", self.config))
        self.assertIsNone(parse_metadata(strip_metadata(self.tex), self.source, self.config))
        self.assertIsNone(parse_metadata(self.document({**self.metadata, "publish": False}), self.source, self.config))

    def test_format_drift_is_rejected(self):
        variants = [
            {"title": "数学分析 I 1.2: 数集与确界原理"},
            {"title": self.metadata["title"] + " "},
            {"categories": ["学习笔记"]}, {"tags": ["math"]},
            {"tags": ["mathematics", "mathematics"]},
            {"section": "01.02"}, {"section": "1.3", "title": "数学分析（一）1.3：数集与确界原理"},
            {"course": "analysis"}, {"publish": "true"}, {"version": True},
            {"slug": "custom"}, {"excerpt": ""},
        ]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_metadata(self.document({**self.metadata, **changes}), self.source, self.config)
        incomplete = dict(self.metadata)
        del incomplete["tags"]
        with self.assertRaises(ValueError):
            parse_metadata(self.document(incomplete), self.source, self.config)

    def test_broken_blocks_and_duplicate_keys_are_rejected(self):
        for tex in [self.tex + self.tex, self.tex.replace("% halo:end", ""),
                    self.tex.replace('%   "version": 1,', '%   "version": 1,\n%   "version": 1,'),
                    self.tex.replace('%   "version":', '    "version":')]:
            with self.subTest(tex=tex[:80]), self.assertRaises(ValueError):
                parse_metadata(tex, self.source, self.config)


if __name__ == "__main__":
    unittest.main()
