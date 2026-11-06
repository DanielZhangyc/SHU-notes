#!/usr/bin/env python3
"""Convert enabled repository notes with Pandoc and export an Astro notes collection."""

import argparse
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "publish/notes.json"
THEOREMS = {"definition": "定义", "proposition": "命题", "theorem": "定理", "example": "例题"}
TIKZ_PICTURE = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)
SGN_SIN_PICTURE_HASH = "158865b788a1c053bae31009ba64489f407a0d6325ca97ea357e22f11a6792d9"
SGN_SIN_SVG = ROOT / "publish/assets/sgn-sin.svg"
FIGURE_MARKER = "HALOFIGURESGNSIN"

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
        if note and note["slug"] not in config["excluded_slugs"]:
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


def normalize_document(document, tex):
    """Apply the section and theorem counters declared by these notes."""
    document = copy.deepcopy(document)
    section_match = re.search(r"\\setcounter\{section\}\{(\d+)\}", tex)
    section = int(section_match[1]) if section_match else 0
    section_prefix_match = re.search(
        r"\\renewcommand\{\\thesection\}\{(\d+)\.\\arabic\{section\}\}", tex
    )
    section_prefix = section_prefix_match[1] + "." if section_prefix_match else ""
    declarations = {}
    unnumbered = dict(re.findall(r"\\newtheorem\*\{(\w+)\}\{([^{}]*)\}", tex))
    for kind, shared, reset in re.findall(
        r"\\newtheorem\{(\w+)\}(?:\[(\w+)\])?\{[^{}]*\}(?:\[(section)\])?", tex
    ):
        if kind in THEOREMS:
            declarations[kind] = (shared or kind, bool(reset))
    counters = {kind: 0 for kind in declarations}
    for kind in counters:
        match = re.search(r"\\setcounter\{" + kind + r"\}\{(\d+)\}", tex)
        if match:
            counters[kind] = int(match[1])
    proof_labels = re.findall(r"\\begin\{proof\}(?:\[([^\]]+)\])?", tex)
    proof_index = 0
    for block in document["blocks"]:
        if block["t"] == "Header":
            if block["c"][0] == 1:
                section += 1
                for kind, (_, reset) in declarations.items():
                    if reset:
                        counters[kind] = 0
                block["c"][2][0:0] = [{"t": "Str", "c": f"{section_prefix}{section}"}, {"t": "Space"}]
            # Astro's layout already provides the article h1.
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
                    if kind in unnumbered:
                        first["c"] = [{"t": "Str", "c": unnumbered[kind]}]
                        continue
                    if kind not in declarations:
                        raise ValueError(f"Missing LaTeX theorem declaration: {kind}")
                    root, reset = declarations[kind]
                    counters[root] += 1
                    custom = re.search(
                        r"\\renewcommand\{\\the" + kind + r"\}\{(\d+)\.\\arabic\{" + root + r"\}\}", tex
                    )
                    prefix = custom[1] + "." if custom else f"{section_prefix}{section}." if reset or declarations[root][1] else ""
                    label = f"{THEOREMS[kind]} {prefix}{counters[root]}"
                else:
                    if proof_index >= len(proof_labels):
                        raise ValueError("Pandoc proof count differs from LaTeX source")
                    label = (proof_labels[proof_index] or "证明") + "."
                    proof_index += 1
                first["c"] = [{"t": "Str", "c": label}]
    if proof_index != len(proof_labels):
        raise ValueError("Pandoc proof count differs from LaTeX source")
    return document


def replace_supported_figures(tex, source):
    pictures = TIKZ_PICTURE.findall(tex)
    if not pictures:
        return tex, None
    if (source.name != "01-03-functions.tex" or len(pictures) != 1 or
            hashlib.sha256(pictures[0].encode()).hexdigest() != SGN_SIN_PICTURE_HASH):
        raise ValueError(f"Unsupported TikZ picture in {source}; add a verified HTML figure")
    return tex.replace(pictures[0], FIGURE_MARKER), SGN_SIN_SVG.read_text()


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
    tex, figure = replace_supported_figures(tex, source)
    document = json.loads(run_pandoc(tex, "-f", "latex", "-t", "json"))
    content = run_pandoc(json.dumps(normalize_document(document, tex)), "-f", "json", "-t", "html5", "--mathml")
    if figure:
        marker = f"<p>{FIGURE_MARKER}</p>"
        if content.count(marker) != 1:
            raise ValueError(f"Figure marker missing or duplicated in {source}")
        content = content.replace(marker, figure)
    # Make long display formulas horizontally scrollable on small screens.
    content = content.replace('<math display="block"', '<math style="display:block;overflow-x:auto;padding:0.5em 0" display="block"')
    pdf_path = source.with_suffix(".pdf").relative_to(ROOT).as_posix()
    pdf_url = f"https://github.com/{config['repository']}/blob/{quote(config['pdf_branch'], safe='')}/{quote(pdf_path)}"
    return content + f'\n<p><a href="{html.escape(pdf_url, quote=True)}">阅读 / 下载 PDF</a></p>\n'


def git_output(*args):
    result = subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True, check=True)
    return result.stdout.strip()


def export_astro(rendered, config, output):
    output = Path(output)
    if git_output("rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("Astro export requires complete Git history for publication dates; fetch full history")
    routes = [course["route"] for course in config["courses"].values()]
    if len(routes) != len(set(routes)) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", route) for route in routes):
        raise ValueError("Courses need unique lowercase ASCII route values")
    manifest = {"version": 1, "repository": config["repository"],
                "revision": git_output("rev-parse", "HEAD"), "notes": []}
    files = []
    for note, content in rendered:
        if note["slug"] in config["excluded_slugs"]:
            raise ValueError(f"Excluded note passed to export: {note['slug']}")
        source = Path(note["source"])
        history = git_output("log", "--follow", "--format=%cI", "--", source.as_posix()).splitlines()
        updated = git_output("log", "-1", "--format=%cI", "--", source.as_posix(), source.with_suffix(".pdf").as_posix())
        if not history or not updated:
            raise ValueError(f"Missing Git history for note: {source}")
        metadata = {
            "title": note["title"], "description": note["excerpt"],
            "publishDate": history[-1], "updatedDate": updated,
            "tags": note["tags"], "categories": note["categories"],
            "legacySlug": note["slug"], "course": note["course"], "section": note["section"],
            "draft": False, "comment": False,
        }
        header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
        markdown = f"---\n{header}\n---\n\n{content}"
        path = f"notes/{config['courses'][note['course']]['route']}/{source.stem}.md"
        files.append((path, markdown.encode("utf-8")))
        manifest["notes"].append({"path": path, "slug": note["slug"], "source": note["source"],
                                  "sha256": hashlib.sha256(markdown.encode()).hexdigest()})
    if len({path for path, _ in files}) != len(files):
        raise ValueError("Duplicate Astro note export path")
    for path, contents in files:
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Render local HTML previews")
    mode.add_argument("--export-astro", type=Path, metavar="DIRECTORY", help="Export notes Markdown and manifest.json")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    notes = discover_notes(config)
    slugs = [note["slug"] for note in notes]
    if len(slugs) != len(set(slugs)) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) for slug in slugs):
        raise ValueError("Each note needs a unique lowercase ASCII slug")
    rendered = [(note, render_note(note, config)) for note in notes]
    if args.preview:
        output = ROOT / "build/astro-preview"
        output.mkdir(parents=True, exist_ok=True)
        for note, content in rendered:
            page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            page += '<style>body{max-width:800px;margin:40px auto;padding:0 20px;line-height:1.8}math{font-size:1.05em}</style>'
            page += f'<title>{html.escape(note["title"])}</title><h1>{html.escape(note["title"])}</h1>{content}</html>'
            (output / (note["slug"] + ".html")).write_text(page)
            print(f"Preview: {note['title']} -> build/astro-preview/{note['slug']}.html")
        return
    manifest = export_astro(rendered, config, args.export_astro)
    print(json.dumps({"notes": len(manifest["notes"]), "revision": manifest["revision"],
                      "manifest": str(args.export_astro / "manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
