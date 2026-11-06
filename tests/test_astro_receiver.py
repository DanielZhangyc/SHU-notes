import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from scripts.astro_receiver import Receiver, fingerprint


REVISION = "abc123ef" * 5
PATH = "notes/mathematical-analysis/01-01-real-numbers.md"
SLUG = "mathematical-analysis-i-01-01-real-numbers"
MARKDOWN = ('---\ntitle: "数学分析（一）1.1：实数"\n'
            f'legacySlug: "{SLUG}"\ndraft: false\n---\n\n<h2 id="real">实数</h2>\n').encode()


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, value in entries:
            if isinstance(value, tarfile.TarInfo):
                value.name = name
                tar.addfile(value)
            else:
                member = tarfile.TarInfo(name)
                member.size = len(value)
                tar.addfile(member, io.BytesIO(value))
    output.seek(0)
    return output


def concurrent_publish(root, releases, live, payload, start, results):
    start.wait()
    try:
        result = Receiver(root=root, releases=releases, live=live).publish(io.BytesIO(payload))
        results.put(("published", result["activeRelease"]))
    except ValueError as error:
        results.put(("rejected", str(error)))


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "private"
        self.current = self.root / "sources/initial"
        self.releases = self.base / "releases"
        self.initial_live = self.releases / "initial"
        self.live = self.base / "website/index"
        for directory in (self.current, self.root / "state", self.initial_live, self.live.parent):
            directory.mkdir(parents=True, exist_ok=True)
        self.receiver = Receiver(root=self.root, releases=self.releases, live=self.live)
        self.receiver.source.symlink_to(self.current)
        self.live.symlink_to(self.initial_live)
        self.write("package.json", '{"name":"notes-site"}')
        self.write("src/data/halo/excluded-posts.json", '["physics-experiment-01-02-measurement-basics"]')
        self.write("src/content/blog/abc482f.md", "---\ntitle: ABC482F\n---\nABC482F\n")
        self.write("src/content/notes/personal/keep.md", '---\nlegacySlug: "personal-keep"\n---\n保留\n')

    def write(self, relative, text):
        path = self.current / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def entries(self, base=None):
        manifest = {"version": 1, "repository": "DanielZhangyc/SHU-notes", "revision": REVISION,
                    "notes": [{"path": PATH, "slug": SLUG,
                               "source": "mathematics/mathematical-analysis-i/notes/01-01-real-numbers.tex",
                               "sha256": hashlib.sha256(MARKDOWN).hexdigest()}]}
        deployment = {"version": 1, "base_source_sha256": base or fingerprint(self.receiver.current_source()),
                      "revision": REVISION}
        pages = ("index.html", "pagefind/pagefind.js", "rss.xml", "blog/abc482f/index.html",
                 "notes/personal/keep/index.html", PATH[:-3] + "/index.html")
        return [("manifest.json", json.dumps(manifest).encode()),
                ("deployment.json", json.dumps(deployment).encode()), (PATH, MARKDOWN),
                *[("site/" + page, b"<!doctype html><html></html>") for page in pages]]

    def assert_unchanged(self):
        self.assertEqual(self.receiver.source.resolve(), self.current)
        self.assertEqual(self.live.resolve(), self.initial_live)
        self.assertEqual(list(self.releases.iterdir()), [self.initial_live])

    def test_source_tar_is_stable_and_excludes_private_and_generated_files(self):
        for path in (".env", ".env.production", "node_modules/pkg/a.js", "dist/index.html",
                     ".git/config", ".beads/data", ".astro/content.json", "__pycache__/test.pyc"):
            self.write(path, "not exported")
        before = fingerprint(self.current)
        output = io.BytesIO()
        self.receiver.export_source(output)
        with tarfile.open(fileobj=io.BytesIO(output.getvalue()), mode="r:gz") as tar:
            names = tar.getnames()
            self.assertEqual(set(names), {"package.json", "src/data/halo/excluded-posts.json",
                                         "src/content/blog/abc482f.md", "src/content/notes/personal/keep.md",
                                         ".publisher-base.json"})
            metadata = json.load(tar.extractfile(".publisher-base.json"))
            self.assertEqual(metadata, {"version": 1, "source_sha256": before})
        self.write(".env", "changed secret")
        self.assertEqual(before, fingerprint(self.current))
        self.assert_unchanged()

    def test_publish_keeps_unmanaged_source_and_previous_releases(self):
        first = self.receiver.publish(archive(self.entries()))
        active = self.receiver.current_source()
        self.assertEqual((active / "src/content" / PATH).read_bytes(), MARKDOWN)
        self.assertEqual((active / "src/content/notes/personal/keep.md").read_bytes(),
                         (self.current / "src/content/notes/personal/keep.md").read_bytes())
        self.assertEqual((active / "src/content/blog/abc482f.md").read_bytes(),
                         (self.current / "src/content/blog/abc482f.md").read_bytes())
        self.assertEqual(first["previousRelease"], "initial")
        self.assertEqual(first["notePaths"], [PATH])
        self.assertTrue(self.initial_live.is_dir())
        second = self.receiver.publish(archive(self.entries()))
        self.assertNotEqual(second["activeRelease"], first["activeRelease"])
        self.assertEqual(second["previousRelease"], first["activeRelease"])
        self.assertTrue((self.releases / first["activeRelease"]).is_dir())
        self.assertTrue(active.is_dir())

    def test_stale_snapshot_fails_without_overwriting_newer_source(self):
        entries = self.entries()
        self.write("package.json", '{"name":"newer-site"}')
        with self.assertRaisesRegex(ValueError, "Stale source snapshot"):
            self.receiver.publish(archive(entries))
        self.assert_unchanged()
        self.assertEqual(json.loads((self.current / "package.json").read_text())["name"], "newer-site")

    def test_rejects_excluded_slug_and_differing_frontmatter(self):
        self.write("src/data/halo/excluded-posts.json", json.dumps([SLUG]))
        with self.assertRaisesRegex(ValueError, "Excluded note"):
            self.receiver.publish(archive(self.entries()))
        self.assert_unchanged()
        entries = [(path, value.replace(SLUG.encode(), b"other-note") if path == PATH else value)
                   for path, value in self.entries()]
        with self.assertRaisesRegex(ValueError, "legacySlug differs"):
            self.receiver.publish(archive(entries))
        self.assert_unchanged()

    def test_manifest_and_built_pages_must_be_complete(self):
        variants = [
            [(path, MARKDOWN + b"changed body" if path == PATH else value) for path, value in self.entries()],
            [(path, value) for path, value in self.entries() if path != PATH],
            self.entries() + [("notes/personal/unlisted.md", MARKDOWN)],
            [(path, value) for path, value in self.entries() if path != "site/blog/abc482f/index.html"],
            [(path, value) for path, value in self.entries() if path != "site/notes/personal/keep/index.html"],
            [(path, value) for path, value in self.entries() if path != "site/pagefind/pagefind.js"],
        ]
        for entries in variants:
            with self.subTest(paths=[path for path, _ in entries]), self.assertRaises(ValueError):
                self.receiver.publish(archive(entries))
            self.assert_unchanged()

    def test_rejects_traversal_links_duplicates_and_unknown_paths(self):
        symlink = tarfile.TarInfo("unused")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "/etc/passwd"
        hardlink = tarfile.TarInfo("unused")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = PATH
        fifo = tarfile.TarInfo("unused")
        fifo.type = tarfile.FIFOTYPE
        variants = [("/absolute", b"bad"), ("../outside", b"bad"), ("site/../outside", b"bad"),
                    ("site//double", b"bad"), ("site/link", symlink), ("site/link", hardlink),
                    ("site/fifo", fifo), ("package.json", b"bad"), ("site/index.html", b"duplicate"),
                    ("notes/course/config.ts", b"bad")]
        for entry in variants:
            with self.subTest(path=entry[0]), self.assertRaises(ValueError):
                self.receiver.publish(archive(self.entries() + [entry]))
            self.assert_unchanged()

    def test_enforces_compressed_and_expanded_limits(self):
        entries = self.entries()
        self.receiver.max_bytes = 128
        with self.assertRaisesRegex(ValueError, "Compressed payload"):
            self.receiver.publish(archive(entries))
        self.receiver.max_bytes = 16384
        with self.assertRaisesRegex(ValueError, "Expanded payload"):
            self.receiver.publish(archive(entries + [("site/large.txt", b"x" * 32768)]))
        self.assert_unchanged()

    def test_real_filesystem_failure_restores_source_link(self):
        entries = self.entries()
        # A dangling live symlink is rejected before any cutover, even after staging validation.
        self.initial_live.rmdir()
        with self.assertRaises(OSError):
            self.receiver.publish(archive(entries))
        self.assertEqual(self.receiver.source.resolve(), self.current)
        self.initial_live.mkdir()
        self.live.parent.chmod(0o555)
        self.addCleanup(self.live.parent.chmod, 0o755)
        with self.assertRaises(PermissionError):
            self.receiver.publish(archive(entries))
        self.assert_unchanged()

    def test_two_actual_publish_processes_cannot_overwrite_one_snapshot(self):
        context = multiprocessing.get_context("fork")
        start = context.Event()
        results = context.Queue()
        payload = archive(self.entries()).getvalue()
        processes = [context.Process(target=concurrent_publish,
                                    args=(self.root, self.releases, self.live, payload, start, results))
                     for _ in range(2)]
        for process in processes:
            process.start()
            self.addCleanup(process.close)
        start.set()
        received = [results.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(kind for kind, _ in received), ["published", "rejected"])
        self.assertIn("Stale source snapshot", next(value for kind, value in received if kind == "rejected"))
        self.assertEqual(len(list(self.releases.iterdir())), 2)
        results.close()
        results.join_thread()

    def test_cli_refuses_shell_commands_before_accessing_production(self):
        script = Path(__file__).resolve().parents[1] / "scripts/astro_receiver.py"
        for command in ("", "publish; id", "source extra", " source", "sh", "publish\n"):
            result = subprocess.run([sys.executable, str(script)], env={**os.environ, "SSH_ORIGINAL_COMMAND": command},
                                    input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"Only source and publish", result.stderr)


if __name__ == "__main__":
    unittest.main()
