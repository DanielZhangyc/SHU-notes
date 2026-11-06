#!/usr/bin/env python3
"""Restricted SSH receiver for source snapshots and validated Astro releases."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import sys
import tarfile
import tempfile
import uuid


MAX_BYTES = 512 * 1024 * 1024
IGNORED = {"node_modules", "dist", ".git", ".beads", ".astro", ".vercel", "__pycache__",
           ".publisher-base.json", ".DS_Store"}
REVISION = re.compile(r"[0-9a-f]{40}")
NOTE_PATH = re.compile(r"notes/[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*\.md")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def relative_path(name):
    if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 for c in name):
        raise ValueError("Invalid archive path")
    parts = name.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Absolute or traversal archive path")
    return PurePosixPath(*parts)


def ignored(name):
    return name in IGNORED or name.startswith(".env") or name.endswith(".pyc")


def source_files(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not ignored(name))
        for name in dirs + sorted(name for name in files if not ignored(name)):
            path = Path(directory) / name
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError("Source snapshot contains a non-regular entry")
        for name in sorted(name for name in files if not ignored(name)):
            yield Path(directory) / name


def fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted(source_files(root)):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(struct.pack(">Q", len(relative)))
        digest.update(relative)
        digest.update(struct.pack(">Q", path.stat().st_size))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def legacy_slug(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("Note requires frontmatter")
    header = text[4:].split("\n---\n", 1)[0]
    values = re.findall(r"^legacySlug:\s*(.+)$", header, re.M)
    if len(values) != 1:
        raise ValueError("Note requires exactly one legacySlug")
    value = json.loads(values[0])
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise ValueError("Invalid note legacySlug")
    return value


class Receiver:
    def __init__(self, root=Path("/opt/xy0v0-blog"),
                 releases=Path("/opt/1panel/www/astro-releases"),
                 live=Path("/opt/1panel/www/sites/xy0v0-blog/index"), max_bytes=MAX_BYTES):
        self.root = Path(root).resolve()
        self.state = self.root / "state"
        self.sources = self.root / "sources"
        self.source = self.state / "source"
        self.releases = Path(releases).resolve()
        self.live = Path(live).parent.resolve() / Path(live).name
        self.max_bytes = max_bytes

    @contextmanager
    def locked(self):
        with (self.state / "publisher.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def current_source(self):
        if not self.source.is_symlink():
            raise ValueError("Source must be an initialized symlink")
        source = self.source.resolve(strict=True)
        if not source.is_dir() or source.parent != self.sources.resolve(strict=True):
            raise ValueError("Source symlink must target one version inside sources")
        return source

    def export_source(self, output):
        with self.locked():
            source = self.current_source()
            base = {"version": 1, "source_sha256": fingerprint(source)}
            with tarfile.open(fileobj=output, mode="w|gz") as archive:
                for path in sorted(source_files(source)):
                    archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
                content = (json.dumps(base, sort_keys=True) + "\n").encode()
                member = tarfile.TarInfo(".publisher-base.json")
                member.size = len(content)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(content))

    def extract(self, stream, destination):
        # Bound both transport bytes and expanded tar bytes, including tar headers.
        compressed = destination / "payload.tar.gz"
        total = 0
        with compressed.open("wb") as output:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > self.max_bytes:
                    raise ValueError("Compressed payload exceeds size limit")
                output.write(chunk)
        expanded = destination / "payload.tar"
        total = 0
        with gzip.open(compressed, "rb") as source, expanded.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > self.max_bytes:
                    raise ValueError("Expanded payload exceeds size limit")
                output.write(chunk)
        content = destination / "content"
        content.mkdir()
        seen = set()
        with tarfile.open(expanded, mode="r:") as archive:
            for member in archive:
                path = relative_path(member.name)
                if path.as_posix() in seen:
                    raise ValueError("Duplicate archive path")
                seen.add(path.as_posix())
                if path.parts[0] not in {"site", "notes", "manifest.json", "deployment.json"}:
                    raise ValueError("Unknown top-level archive path")
                if path.parts[0] in {"manifest.json", "deployment.json"} and (len(path.parts) != 1 or not member.isreg()):
                    raise ValueError("Manifest and deployment must be regular JSON files")
                if not (member.isdir() or member.isreg()) or member.sparse is not None:
                    raise ValueError("Archive permits only regular files and directories")
                if member.isreg() and (len(path.parts) == 1 and path.parts[0] in {"site", "notes"}):
                    raise ValueError("Site and notes must be directories")
                target = content.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    if path.parts[0] == "notes" and not NOTE_PATH.fullmatch(path.as_posix()):
                        raise ValueError("Only course Markdown files may be managed")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o644)
        return content

    def validate(self, content):
        manifest = read_json(content / "manifest.json")
        deployment = read_json(content / "deployment.json")
        if (not isinstance(manifest, dict) or set(manifest) != {"version", "repository", "revision", "notes"}
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["repository"] != "DanielZhangyc/SHU-notes"
                or not isinstance(manifest["revision"], str) or not REVISION.fullmatch(manifest["revision"])
                or not isinstance(manifest["notes"], list)):
            raise ValueError("Invalid publication manifest")
        if (not isinstance(deployment, dict) or set(deployment) != {"version", "base_source_sha256", "revision"}
                or type(deployment["version"]) is not int or deployment["version"] != 1
                or deployment["revision"] != manifest["revision"]
                or not isinstance(deployment["base_source_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", deployment["base_source_sha256"])):
            raise ValueError("Invalid deployment metadata")
        paths, slugs = set(), set()
        for entry in manifest["notes"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "slug", "source", "sha256"}:
                raise ValueError("Invalid manifest note entry")
            path, slug = entry["path"], entry["slug"]
            if not isinstance(path, str) or not NOTE_PATH.fullmatch(path) or path in paths:
                raise ValueError("Invalid or duplicate manifest note path")
            if not isinstance(slug, str) or not SLUG.fullmatch(slug) or slug in slugs:
                raise ValueError("Invalid or duplicate manifest slug")
            if relative_path(entry["source"]).suffix != ".tex":
                raise ValueError("Manifest source must be a repository TeX path")
            note = content / path
            if not note.is_file() or legacy_slug(note) != slug:
                raise ValueError("Manifest note missing or legacySlug differs")
            if hashlib.sha256(note.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("Note SHA256 differs from manifest")
            paths.add(path)
            slugs.add(slug)
        actual = {path.relative_to(content).as_posix() for path in (content / "notes").rglob("*") if path.is_file()}
        if actual != paths:
            raise ValueError("Archive notes must exactly match manifest")
        for path in ["index.html", "pagefind/pagefind.js", "rss.xml", *[path[:-3] + "/index.html" for path in paths]]:
            if not (content / "site" / path).is_file():
                raise ValueError(f"Missing built site page: {path}")
        return manifest, deployment

    @staticmethod
    def replace_link(link, target):
        temporary = link.with_name("." + link.name + "." + uuid.uuid4().hex)
        try:
            temporary.symlink_to(target)
            os.replace(temporary, link)
        finally:
            temporary.unlink(missing_ok=True)

    def publish(self, stream):
        with tempfile.TemporaryDirectory(prefix="incoming-", dir=self.state) as temporary:
            content = self.extract(stream, Path(temporary))
            manifest, deployment = self.validate(content)
            with self.locked():
                current = self.current_source()
                if fingerprint(current) != deployment["base_source_sha256"]:
                    raise ValueError("Stale source snapshot; fetch source and rebuild before publishing")
                excluded = read_json(current / "src/data/halo/excluded-posts.json")
                if not isinstance(excluded, list) or any(not isinstance(slug, str) for slug in excluded):
                    raise ValueError("Invalid excluded-posts list")
                for entry in manifest["notes"]:
                    if entry["slug"] in excluded:
                        raise ValueError("Excluded note may not be published")
                    previous = current / "src/content" / entry["path"]
                    if previous.exists() and legacy_slug(previous) != entry["slug"]:
                        raise ValueError("Note path collides with a different existing note")
                # Keep every existing blog (including ABC482F) and unmanaged note reachable.
                for collection in ("blog", "notes"):
                    directory = current / "src/content" / collection
                    for path in directory.rglob("*"):
                        if path.suffix not in {".md", ".mdx"} or not path.is_file():
                            continue
                        relative = path.relative_to(directory).with_suffix("")
                        if relative.name == "index":
                            relative = relative.parent
                        page = content / "site" / collection / relative / "index.html"
                        if not page.is_file():
                            raise ValueError(f"Existing {collection} page missing from built site: {relative}")
                if not self.live.is_symlink() or not self.live.resolve(strict=True).is_dir():
                    raise ValueError("Live website must be an initialized directory symlink")
                previous_live = os.readlink(self.live)
                previous_source = os.readlink(self.source)
                release = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + manifest["revision"][:8] + "-" + uuid.uuid4().hex[:8]
                next_source = self.sources / release
                next_release = self.releases / release
                try:
                    shutil.copytree(current, next_source, ignore=lambda directory, names: [name for name in names if ignored(name)])
                    for entry in manifest["notes"]:
                        target = next_source / "src/content" / entry["path"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.unlink(missing_ok=True)
                        shutil.copyfile(content / entry["path"], target)
                        target.chmod(0o644)
                    shutil.copytree(content / "site", next_release)
                    try:
                        self.replace_link(self.source, next_source)
                        self.replace_link(self.live, os.path.relpath(next_release, self.live.parent))
                    except BaseException:
                        if os.readlink(self.source) != previous_source:
                            self.replace_link(self.source, previous_source)
                        if os.readlink(self.live) != previous_live:
                            self.replace_link(self.live, previous_live)
                        raise
                except BaseException:
                    # Remove only this attempt's unpublished versions; retain prior releases.
                    if self.source.resolve() != next_source and next_source.exists():
                        shutil.rmtree(next_source)
                    if self.live.resolve() != next_release and next_release.exists():
                        shutil.rmtree(next_release)
                    raise
                return {"activeRelease": release, "previousRelease": Path(previous_live).name,
                        "revision": manifest["revision"], "notePaths": [entry["path"] for entry in manifest["notes"]]}


def main():
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if command not in {"source", "publish"}:
        raise ValueError("Only source and publish commands are allowed")
    os.umask(0o022)
    receiver = Receiver()
    if command == "source":
        receiver.export_source(sys.stdout.buffer)
    else:
        print(json.dumps(receiver.publish(sys.stdin.buffer), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, EOFError, tarfile.TarError) as error:
        print(f"Publication rejected: {error}", file=sys.stderr)
        sys.exit(1)
