"""Validate the actual compiled PDF and the math blocks in its source."""

import hashlib
from pathlib import Path
import re

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent
STEM = 'midterm-calculation-review'
markdown = (ROOT / (STEM + '.md')).read_text()
tex = (ROOT / (STEM + '.tex')).read_text()
pdf = ROOT / (STEM + '.pdf')
reader = PdfReader(pdf)
assert len(reader.pages) == 9
assert reader.metadata.title == '高等代数期中计算复习'
math_count = len(re.findall(r'^```math$', markdown, re.M))
assert math_count == 46 == tex.count('\\[') == tex.count('\\]')
text = ''.join(page.extract_text() for page in reader.pages)
normalized = ''.join(text.split())
assert all('短检查' + str(number) in normalized for number in range(1, 7))
assert '\ufffd' not in text
assert '{{FILL' not in markdown
fonts = set()
links = []
for page in reader.pages:
    assert abs(float(page.mediabox.width) - 595.276) < 1
    assert abs(float(page.mediabox.height) - 841.89) < 1
    for reference in page['/Resources'].get('/Font', {}).values():
        font = reference.get_object()
        fonts.add(str(font['/BaseFont']))
        for item in font.get('/DescendantFonts', [font]):
            descriptor = item.get_object()['/FontDescriptor'].get_object()
            assert any(key in descriptor for key in ('/FontFile', '/FontFile2', '/FontFile3'))
    for reference in page.get('/Annots', []):
        action = reference.get_object().get('/A', {})
        if action.get('/S') == '/URI':
            links.append(str(action['/URI']))
assert len(links) == 3
print({
    'pages': len(reader.pages), 'format': 'A4', 'math_blocks': math_count,
    'embedded_fonts': len(fonts), 'pdf_links': len(links),
    'pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
    'markdown_sha256': hashlib.sha256((ROOT / (STEM + '.md')).read_bytes()).hexdigest(),
})
