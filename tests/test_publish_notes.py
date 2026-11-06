import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.publish_notes import CONFIG, ROOT, discover_notes, parse_metadata, render_note


class PublishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG.read_text())
        cls.notes = {note["source"]: note for note in discover_notes(cls.config)}
        cls.temporary = tempfile.TemporaryDirectory(prefix="shu-notes-astro-test-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)
        cls.command = [sys.executable, str(ROOT / "scripts/publish_notes.py"),
                       "--export-astro", str(cls.output)]
        cls.run_export()
        cls.manifest = json.loads((cls.output / "manifest.json").read_text())

    @classmethod
    def run_export(cls):
        subprocess.run(cls.command, cwd=ROOT, text=True, capture_output=True, check=True)

    def render(self, source):
        return render_note(self.notes[source], self.config)

    def test_real_note_conversion(self):
        page = self.render("mathematics/mathematical-analysis-i/notes/01-02-sets-and-suprema.tex")
        for text in ("定义 1.1", "命题 2.2", "例题 3.2", "定理 4.3", "例题 5.1", "证明."):
            self.assertIn(text, page)
        self.assertNotIn("halo:begin", page)
        self.assertNotIn("study-notes", page)
        self.assertNotIn("Proof.", page)
        self.assertNotIn("<h1", page)
        self.assertIn("<math", page)
        self.assertIn("<mfrac>", page)
        self.assertIn("github.com/DanielZhangyc/SHU-notes/blob/main/", page)

    def test_real_figures_numbering_and_solution_labels(self):
        function = self.render("mathematics/mathematical-analysis-i/notes/01-03-functions.tex")
        self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', function)
        self.assertIn("在各相邻整π点之间交替取一和负一", function)
        self.assertNotIn("HALOFIGURESGNSIN", function)
        sequence = self.render("mathematics/mathematical-analysis-i/notes/02-02-properties-of-convergent-sequences.tex")
        for label in ("定理 2.2", "定理 2.3", "定理 2.4"):
            self.assertIn(label, sequence)
        self.assertNotIn("定理 1.1", sequence)
        algebra = self.render("mathematics/higher-algebra-i/notes/01-03-determinant-properties-and-expansion.tex")
        self.assertIn("<em>解.</em>", algebra)

    def test_convergence_criteria_preserves_unnumbered_examples(self):
        page = self.render("mathematics/mathematical-analysis-i/notes/02-04-convergence-criteria.tex")
        self.assertEqual(page.count("<strong>例题</strong>"), 8)
        self.assertNotRegex(page, r"例题 \d")
        for label in ("定理 2.9", "定理 2.10", "定理 2.11"):
            self.assertIn(f"<strong>{label}</strong>", page)
        for title in ("单调有界定理", "致密性定理", "闭区间套定理",
                      "柯西收敛准则", "无限小数的截断值", "平方倒数部分和", "调和部分和"):
            self.assertIn(title, page)

    def test_real_metadata_and_exclusions(self):
        source = "mathematics/mathematical-analysis-i/notes/01-02-sets-and-suprema.tex"
        note = parse_metadata((ROOT / source).read_text(), source, self.config)
        self.assertEqual(note["title"], "数学分析（一）1.2：数集与确界原理")
        self.assertEqual(note["categories"], ["学习笔记"])
        self.assertEqual(note["tags"], ["数学"])
        self.assertEqual(note["slug"], "mathematical-analysis-i-01-02-sets-and-suprema")
        unpublished = "general-education/data-and-optimization/notes/01-01-data-and-modeling.tex"
        self.assertIsNone(parse_metadata((ROOT / unpublished).read_text(), unpublished, self.config))
        self.assertIsNone(parse_metadata((ROOT / "template.tex").read_text(), "template.tex", self.config))
        self.assertEqual(len(self.notes), 19)
        self.assertFalse(any(note["slug"] in self.config["excluded_slugs"] for note in self.notes.values()))

    def test_export_contract_and_owned_paths(self):
        self.assertEqual(set(self.manifest), {"version", "repository", "revision", "notes"})
        self.assertEqual(self.manifest["version"], 1)
        self.assertEqual(self.manifest["repository"], "DanielZhangyc/SHU-notes")
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                  capture_output=True, check=True).stdout.strip()
        self.assertEqual(self.manifest["revision"], revision)
        self.assertEqual(len(self.manifest["notes"]), 19)
        self.assertEqual({entry["source"] for entry in self.manifest["notes"]}, set(self.notes))
        paths = {entry["path"] for entry in self.manifest["notes"]}
        self.assertEqual(paths, {path.relative_to(self.output).as_posix() for path in self.output.rglob("*.md")})
        self.assertEqual(sum(path.startswith("notes/higher-algebra/") for path in paths), 6)
        self.assertEqual(sum(path.startswith("notes/mathematical-analysis/") for path in paths), 13)
        self.assertFalse((self.output / "blog").exists())
        for slug in self.config["excluded_slugs"]:
            self.assertNotIn(slug, json.dumps(self.manifest))

    def test_exported_frontmatter_body_and_hashes(self):
        for entry in self.manifest["notes"]:
            with self.subTest(source=entry["source"]):
                markdown = (self.output / entry["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(markdown).hexdigest(), entry["sha256"])
                header, body = markdown.decode().split("\n---\n\n", 1)
                data = {key: json.loads(value) for key, value in
                        (line.split(": ", 1) for line in header.splitlines()[1:])}
                note = self.notes[entry["source"]]
                self.assertEqual(data["title"], note["title"])
                self.assertEqual(data["description"], note["excerpt"])
                self.assertEqual(data["legacySlug"], entry["slug"])
                self.assertEqual(data["course"], note["course"])
                self.assertEqual(data["section"], note["section"])
                self.assertEqual(data["tags"], note["tags"])
                self.assertEqual(data["categories"], note["categories"])
                self.assertIs(data["draft"], False)
                self.assertIs(data["comment"], False)
                self.assertEqual(body, render_note(note, self.config))
                self.assertIn("<math", body)
                self.assertNotIn("<h1", body)
                pdf = Path(entry["source"]).with_suffix(".pdf").as_posix()
                self.assertIn(f"github.com/DanielZhangyc/SHU-notes/blob/main/{pdf}", body)
                self.assertRegex(data["publishDate"], r"^\d{4}-\d\d-\d\dT")
                self.assertRegex(data["updatedDate"], r"^\d{4}-\d\d-\d\dT")
                if entry["source"].endswith("01-02-sets-and-suprema.tex"):
                    self.assertEqual(data["publishDate"], "2026-09-15T16:17:52+08:00")
                    self.assertEqual(data["updatedDate"], "2026-09-15T23:38:13+08:00")

    def test_repeated_real_export_is_byte_identical(self):
        before = {path.relative_to(self.output).as_posix(): path.read_bytes()
                  for path in self.output.rglob("*") if path.is_file()}
        self.run_export()
        after = {path.relative_to(self.output).as_posix(): path.read_bytes()
                 for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
