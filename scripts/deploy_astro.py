#!/usr/bin/env python3
"""Build against the private blog source and publish through its restricted SSH entry point."""

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def connection():
    host = os.environ["ASTRO_SSH_HOST"]
    user = os.environ["ASTRO_SSH_USER"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
        raise ValueError("Invalid ASTRO_SSH_HOST")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Invalid ASTRO_SSH_USER")
    with tempfile.TemporaryDirectory(prefix="notes-ssh-") as temporary:
        directory = Path(temporary)
        key = directory / "identity"
        key.write_text(os.environ["ASTRO_SSH_PRIVATE_KEY"].rstrip() + "\n")
        key.chmod(0o600)
        known_hosts = directory / "known_hosts"
        known_hosts.write_text(os.environ["ASTRO_SSH_KNOWN_HOSTS"].rstrip() + "\n")
        yield ["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o",
               "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o",
               f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=15", f"{user}@{host}"]


def manifest_for(export):
    manifest = json.loads((export / "manifest.json").read_text())
    if (manifest["version"] != 1 or manifest["repository"] != "DanielZhangyc/SHU-notes"
            or not re.fullmatch(r"[0-9a-f]{40}", manifest["revision"])):
        raise ValueError("Invalid notes manifest")
    paths = set()
    for note in manifest["notes"]:
        path = PurePosixPath(note["path"])
        if (len(path.parts) != 3 or path.parts[0] != "notes" or
                not re.fullmatch(r"[a-z0-9-]+", path.parts[1]) or
                not re.fullmatch(r"[a-z0-9-]+\.md", path.name) or path in paths):
            raise ValueError(f"Invalid or duplicate note path: {path}")
        paths.add(path)
        source = export / path
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != note["sha256"]:
            raise ValueError(f"Export checksum mismatch: {path}")
    return manifest


def prepare(export, site):
    manifest = manifest_for(export)
    if site.exists():
        raise FileExistsError(f"Build directory already exists: {site}")
    site.mkdir(parents=True)
    with tempfile.TemporaryFile() as snapshot:
        with connection() as ssh:
            subprocess.run([*ssh, "source"], stdout=snapshot, check=True, timeout=300)
        snapshot.seek(0)
        with tarfile.open(fileobj=snapshot, mode="r:gz") as archive:
            archive.extractall(site, filter="data")
    base = json.loads((site / ".publisher-base.json").read_text())
    if base["version"] != 1 or not re.fullmatch(r"[0-9a-f]{64}", base["source_sha256"]):
        raise ValueError("Invalid blog source fingerprint")
    for note in manifest["notes"]:
        destination = site / "src/content" / note["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(export / note["path"], destination)
    print(json.dumps({"preparedNotes": len(manifest["notes"]), "revision": manifest["revision"]}))


def publish(export, site):
    manifest = manifest_for(export)
    base = json.loads((site / ".publisher-base.json").read_text())
    dist = site / "dist"
    required = ["index.html", "rss.xml", "pagefind/pagefind.js"]
    required += [str(PurePosixPath(note["path"]).with_suffix("")) + "/index.html"
                 for note in manifest["notes"]]
    for path in required:
        if not (dist / path).is_file():
            raise ValueError(f"Missing built page or resource: {path}")
    deployment = {"version": 1, "base_source_sha256": base["source_sha256"],
                  "revision": manifest["revision"]}
    with tempfile.TemporaryFile() as payload:
        with tarfile.open(fileobj=payload, mode="w:gz", compresslevel=3) as archive:
            for name, document in (("deployment.json", deployment), ("manifest.json", manifest)):
                data = json.dumps(document).encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            for note in manifest["notes"]:
                archive.add(export / note["path"], arcname=note["path"], recursive=False)
            for path in sorted(dist.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"Built site contains a symlink: {path}")
                if path.is_file():
                    relative = path.relative_to(dist)
                    if any(part.startswith(".") for part in relative.parts):
                        raise ValueError(f"Unexpected hidden build file: {relative}")
                    archive.add(path, arcname="site/" + relative.as_posix(), recursive=False)
        payload.seek(0)
        with connection() as ssh:
            result = subprocess.run([*ssh, "publish"], stdin=payload,
                                    stdout=subprocess.PIPE, check=True, timeout=600)
    deployment_result = json.loads(result.stdout)
    if deployment_result["revision"] != manifest["revision"]:
        raise ValueError("Deployed revision differs from the exported notes")
    (export / "deployment-result.json").write_text(json.dumps(deployment_result, indent=2) + "\n")
    print(json.dumps(deployment_result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "publish"])
    parser.add_argument("--export-dir", type=Path, default=ROOT / "build/astro")
    parser.add_argument("--site-dir", type=Path, default=ROOT / "build/astro-site")
    args = parser.parse_args()
    {"prepare": prepare, "publish": publish}[args.action](args.export_dir, args.site_dir)


if __name__ == "__main__":
    main()
