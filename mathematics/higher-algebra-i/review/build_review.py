"""Build the review PDF from the same native math Markdown used by the Page."""

from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent
STEM = 'midterm-calculation-review'
SOURCE = ROOT / (STEM + '.md')

with tempfile.TemporaryDirectory(prefix='shu-midterm-build-') as directory:
    build = Path(directory)
    tex = build / (STEM + '.tex')
    subprocess.run([
        'pandoc', str(SOURCE), '-f', 'gfm', '-t', 'latex', '--standalone',
        '--lua-filter', str(ROOT / 'math-blocks.lua'),
        '-H', str(ROOT / 'review-header.tex'),
        '-V', 'documentclass=ctexart', '-V', 'classoption=fontset=fandol',
        '-V', 'fontsize=11pt', '-V', 'papersize=a4', '-V', 'geometry=margin=2cm',
        '-V', 'linestretch=1.08', '-M', 'title=高等代数期中计算复习',
        '-M', 'date=2026年11月10日', '-o', str(tex),
    ], check=True)
    for _ in range(2):
        result = subprocess.run([
            'lualatex', '-interaction=nonstopmode', '-halt-on-error', tex.name,
        ], cwd=build, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
    log = tex.with_suffix('.log').read_text()
    for token in ('Overfull', 'Missing character', 'undefined references'):
        if token in log:
            raise RuntimeError('PDF requires correction: ' + token + '\n' + log)
    shutil.copyfile(tex.with_suffix('.pdf'), ROOT / (STEM + '.pdf'))
    shutil.copyfile(tex, ROOT / (STEM + '.tex'))

print(ROOT / (STEM + '.pdf'))
