#!/usr/bin/env python3
"""Convert repository notes with Pandoc and synchronize them through Halo's UC API."""

import argparse
import copy
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "publish/notes.json"
THEOREMS = {"definition": "定义", "proposition": "命题", "theorem": "定理", "example": "例题"}

META_BLOCK = re.compile(r"^% halo:begin\n(.*?)^% halo:end(?:\n|$)", re.M | re.S)
META_KEYS = {"version", "publish", "course", "section", "title", "categories", "tags", "excerpt"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate metadata key: {key}")
        result[key] = value
    return result


def strip_metadata(tex):
    return META_BLOCK.sub("", tex)


def parse_metadata(tex, source, config):
    blocks = list(META_BLOCK.finditer(tex))
    if not blocks and "halo:" not in tex:
        return None
    if len(blocks) != 1 or tex.count("% halo:begin") != 1 or tex.count("% halo:end") != 1:
        raise ValueError(f"{source}: expected exactly one complete halo metadata block")
    block = blocks[0]
    if r"\begin{document}" in tex[:block.start()]:
        raise ValueError(f"{source}: metadata belongs in the preamble")
    lines = block[1].splitlines()
    if any(not line.startswith("% ") for line in lines):
        raise ValueError(f"{source}: each metadata line must start with '% '")
    metadata = json.loads("\n".join(line[2:] for line in lines), object_pairs_hook=unique_object)
    if not isinstance(metadata, dict) or set(metadata) != META_KEYS:
        raise ValueError(f"{source}: metadata fields must be {sorted(META_KEYS)}")
    if type(metadata["version"]) is not int or metadata["version"] != 1 or type(metadata["publish"]) is not bool:
        raise ValueError(f"{source}: version must be 1 and publish must be a JSON boolean")
    for key in ("course", "section", "title", "excerpt"):
        value = metadata[key]
        if not isinstance(value, str) or not value or value != value.strip() or any(char in value for char in "\r\n"):
            raise ValueError(f"{source}: {key} must be nonempty single-line text without outer whitespace")
    course = config["courses"].get(metadata["course"])
    if not course:
        raise ValueError(f"{source}: unknown course key {metadata['course']}")
    if not re.fullmatch(r"[1-9][0-9]*\.[1-9][0-9]*", metadata["section"]):
        raise ValueError(f"{source}: section must look like 1.2 (no leading zeroes)")
    title = re.findall(r"\\title\{([^{}\n]+)\}", strip_metadata(tex))
    if len(title) != 1:
        raise ValueError(f"{source}: use one plain-text LaTeX title")
    expected = f"{course['name']}{metadata['section']}：{title[0]}"
    if metadata["title"] != expected:
        raise ValueError(f"{source}: title must be exactly {expected!r}")
    resolved = {}
    for key in ("categories", "tags"):
        values = metadata[key]
        if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
            raise ValueError(f"{source}: {key} must be a nonempty list of registered keys")
        if len(set(values)) != len(values) or any(value not in config[key] for value in values):
            raise ValueError(f"{source}: duplicate or unregistered {key}; allowed: {list(config[key])}")
        if key == "categories" and len(values) != 1:
            raise ValueError(f"{source}: select exactly one category")
        resolved[key] = [config[key][value] for value in sorted(values)]
    if not metadata["publish"]:
        return None
    path = Path(source)
    chapter, section = map(int, metadata["section"].split("."))
    prefix = f"{chapter:02d}-{section:02d}-"
    if path.parent.as_posix() != course["directory"] or not re.fullmatch(re.escape(prefix) + r"[a-z0-9]+(?:-[a-z0-9]+)*", path.stem):
        raise ValueError(f"{source}: path must match course directory and {prefix}<english-topic>.tex")
    return {**metadata, **resolved, "source": source, "slug": f"{metadata['course']}-{path.stem}"}


def discover_notes(config, root=ROOT):
    notes = []
    for path in sorted(root.glob("*/*/notes/*.tex")):
        note = parse_metadata(path.read_text(), path.relative_to(root).as_posix(), config)
        if note:
            notes.append(note)
    return notes


def run_pandoc(text, *args):
    result = subprocess.run(
        [os.environ.get("PANDOC", "pandoc"), "--fail-if-warnings", *args],
        input=text, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Pandoc conversion failed: {result.stderr.strip()}")
    return result.stdout


def normalize_document(document):
    """Match the template's shared theorem counter, reset at each section."""
    document = copy.deepcopy(document)
    section = counter = 0
    for block in document["blocks"]:
        if block["t"] == "Header":
            if block["c"][0] == 1:
                section += 1
                counter = 0
                block["c"][2][0:0] = [{"t": "Str", "c": str(section)}, {"t": "Space"}]
            # Halo's theme already provides the article h1.
            block["c"][0] += 1
        elif block["t"] == "Div":
            classes = block["c"][0][1]
            kind = next((name for name in THEOREMS if name in classes), None)
            if kind or "proof" in classes:
                paragraphs = block["c"][1]
                if not paragraphs or paragraphs[0]["t"] != "Para":
                    raise ValueError("Unexpected Pandoc theorem structure")
                first = paragraphs[0]["c"][0]
                if first["t"] not in {"Strong", "Emph"}:
                    raise ValueError("Missing theorem or proof label")
                if kind:
                    counter += 1
                    label = f"{THEOREMS[kind]} {section}.{counter}"
                else:
                    label = "证明."
                first["c"] = [{"t": "Str", "c": label}]
    return document


def render_note(note, config):
    source = (ROOT / note["source"]).resolve()
    if not source.is_relative_to(ROOT) or source.suffix != ".tex":
        raise ValueError("source must be a .tex file inside the repository")
    if not source.with_suffix(".pdf").is_file():
        raise ValueError(f"Missing companion PDF: {source.with_suffix('.pdf')}")
    tex = strip_metadata(source.read_text())
    preamble, separator, body = tex.partition(r"\begin{document}")
    if not separator:
        raise ValueError("Missing LaTeX document body")
    # The blog supplies its own title and metadata, while preamble macros remain available.
    tex = preamble + separator + body.replace(r"\maketitle", "")
    document = json.loads(run_pandoc(tex, "-f", "latex", "-t", "json"))
    content = run_pandoc(json.dumps(normalize_document(document)), "-f", "json", "-t", "html5", "--mathml")
    # Make long display formulas horizontally scrollable on small screens.
    content = content.replace('<math display="block"', '<math style="display:block;overflow-x:auto;padding:0.5em 0" display="block"')
    pdf_path = source.with_suffix(".pdf").relative_to(ROOT).as_posix()
    pdf_url = f"https://github.com/{config['repository']}/blob/{quote(config['pdf_branch'], safe='')}/{quote(pdf_path)}"
    return content + f'\n<p><a href="{html.escape(pdf_url, quote=True)}">阅读 / 下载 PDF</a></p>\n'


class NoRedirect(HTTPRedirectHandler):
    # A configured canonical URL avoids forwarding the bearer token to another host.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Halo:
    def __init__(self, url, token):
        if not url.startswith("https://"):
            raise ValueError("HALO URL must use HTTPS")
        self.url = url.rstrip("/")
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request(self.url + path, data=body, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json", "Content-Type": "application/json",
        })
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            # Do not print request headers or arbitrary server response bodies.
            raise RuntimeError(f"Halo {method} {path}: HTTP {error.code}") from None

    def list_all(self, path):
        items, page = [], 1
        while True:
            response = self.request("GET", f"{path}?page={page}&size=100")
            items.extend(response["items"])
            if not response.get("hasNext", len(items) < response.get("total", len(items))):
                return items
            page += 1


def resolve_names(resources, requested, kind):
    resolved = []
    for name in requested:
        matches = [item for item in resources if item["spec"]["displayName"] == name]
        if len(matches) != 1:
            raise ValueError(f"{kind} '{name}' must exist exactly once in Halo; create it in Console first")
        resolved.append(matches[0]["metadata"]["name"])
    return resolved


POSTS = "/apis/uc.api.content.halo.run/v1alpha1/posts"
CONTENT = "content.halo.run/content-json"
SOURCE = "notes.xy0v0.top/source"


def sync(halo, rendered, check_only=False):
    categories = halo.list_all("/apis/content.halo.run/v1alpha1/categories")
    tags = halo.list_all("/apis/content.halo.run/v1alpha1/tags")
    posts = [entry["post"] for entry in halo.list_all(POSTS)]
    plans = []
    # Resolve every reference and detect collisions before the first write.
    for note, content in rendered:
        name = "notes-" + note["slug"]
        matches = [post for post in posts if post["metadata"]["name"] == name or post["spec"]["slug"] == note["slug"]]
        if len(matches) > 1:
            raise ValueError(f"Multiple posts match {note['slug']}")
        existing = matches[0] if matches else None
        if existing and (existing["metadata"]["name"] != name or existing["metadata"].get("annotations", {}).get(SOURCE) != note["source"]):
            raise ValueError(f"Existing post is not managed by this workflow: {note['slug']}")
        spec = {
            "title": note["title"], "slug": note["slug"],
            "categories": resolve_names(categories, note["categories"], "Category"),
            "tags": resolve_names(tags, note["tags"], "Tag"),
            "excerpt": {"autoGenerate": False, "raw": note["excerpt"]},
        }
        plans.append((note, name, existing, spec, content))
    for note, name, existing, spec, content in plans:
        if check_only:
            print(f"Ready to {'update' if existing else 'create'}: {note['title']}")
            continue
        path = POSTS + "/" + quote(name, safe="")
        payload = json.dumps({"raw": content, "content": content, "rawType": "HTML"}, ensure_ascii=False)
        if existing:
            post = halo.request("GET", path)
            if post["spec"].get("deleted"):
                raise ValueError(f"Post is in recycle bin: {name}")
            if any(post["spec"].get(key) != value for key, value in spec.items()):
                post["spec"].update(spec)
                halo.request("PUT", path, post)
            snapshot = halo.request("GET", path + "/draft?patched=true")
            annotations = snapshot["metadata"].setdefault("annotations", {})
            previous = annotations.pop("content.halo.run/patched-content", None)
            annotations.pop("content.halo.run/patched-raw", None)
            if previous != content:
                annotations[CONTENT] = payload
                halo.request("PUT", path + "/draft", snapshot)
        else:
            halo.request("POST", POSTS, {
                "apiVersion": "content.halo.run/v1alpha1", "kind": "Post",
                "metadata": {"name": name, "annotations": {SOURCE: note["source"], CONTENT: payload}},
                "spec": {**spec, "deleted": False, "publish": False, "pinned": False,
                         "allowComment": True, "visible": "PUBLIC", "priority": 0},
            })
        current = halo.request("GET", path)
        if not current["spec"].get("publish") or current["spec"].get("releaseSnapshot") != current["spec"].get("headSnapshot"):
            halo.request("PUT", path + "/publish")
        for attempt in range(10):
            verified = halo.request("GET", path)
            spec_now = verified["spec"]
            if spec_now.get("publish") and spec_now.get("headSnapshot") and spec_now.get("releaseSnapshot") == spec_now["headSnapshot"]:
                print(f"Published: {note['title']}")
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Publication confirmation timed out: {name}; rerun to reconcile")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Render locally without credentials or API calls")
    mode.add_argument("--check", action="store_true", help="Read-only check of permissions, categories and tags")
    mode.add_argument("--publish", action="store_true", help="Create/update and publish configured articles")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    notes = discover_notes(config)
    slugs = [note["slug"] for note in notes]
    if len(slugs) != len(set(slugs)) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) for slug in slugs):
        raise ValueError("Each note needs a unique lowercase ASCII slug")
    rendered = [(note, render_note(note, config)) for note in notes]
    if args.preview:
        output = ROOT / "build/halo"
        output.mkdir(parents=True, exist_ok=True)
        for note, content in rendered:
            page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            page += '<style>body{max-width:800px;margin:40px auto;padding:0 20px;line-height:1.8}math{font-size:1.05em}</style>'
            page += f'<title>{html.escape(note["title"])}</title><h1>{html.escape(note["title"])}</h1>{content}</html>'
            (output / (note["slug"] + ".html")).write_text(page)
            print(f"Preview: {note['title']} -> build/halo/{note['slug']}.html")
        return
    if not rendered:
        print("No notes enabled for publication")
        return
    token = os.environ.get("HALO_TOKEN", "")
    if not token:
        raise ValueError("Set HALO_TOKEN (GitHub Actions repository secret or local environment)")
    halo = Halo(config["halo_url"], token)
    sync(halo, rendered, check_only=args.check)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
