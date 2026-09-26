"""Erzeugt Testdokumente (Word, PDF, Handyfoto) ohne externe Programme."""

import io
import zipfile
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def make_odt(paragraphs: list[str]) -> bytes:
    ns = 'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    body = "".join(f"<text:p>{escape(p)}</text:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", f'<?xml version="1.0"?><office:document-content {ns}><office:body><office:text>{body}</office:text></office:body></office:document-content>')
    return buf.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    """Minimales PDF mit echter Textebene (Helvetica, WinAnsi für Umlaute)."""
    def esc(s):
        return s.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    content = b"BT /F1 11 Tf 60 780 Td 14 TL " + b" ".join(b"(" + esc(l) + b") '" for l in lines) + b" ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def make_photo(lines: list[str], rotate: int = 0, fmt: str = "JPEG") -> bytes:
    """Simuliert ein Handyfoto einer Seite: grauer Rand, leicht getöntes Papier."""
    w, h = 1240, 1754
    page = Image.new("RGB", (w, h), (245, 242, 235))
    d = ImageDraw.Draw(page)
    font = _font(34)
    y = 120
    for line in lines:
        d.text((100, y), line, fill=(30, 30, 30), font=font)
        y += 56
    photo = Image.new("RGB", (w + 160, h + 160), (90, 90, 95))
    photo.paste(page, (80, 80))
    if rotate:
        photo = photo.rotate(rotate, expand=True)
    buf = io.BytesIO()
    photo.save(buf, fmt, quality=85)
    return buf.getvalue()


INVOICE = [
    "Stadtwerke Musterstadt GmbH · Energieweg 1 · 12345 Musterstadt",
    "",
    "Max Mustermann",
    "Hauptstraße 5",
    "12345 Musterstadt",
    "",
    "Datum: 14.03.2026",
    "Jahresabrechnung Strom 2025",
    "Kundennummer: 4711-0815",
    "Zählerstand alt: 12.345 kWh, Zählerstand neu: 15.210 kWh",
    "Verbrauch: 2.865 kWh",
    "Ihr neuer monatlicher Abschlag beträgt 78,00 EUR.",
    "Rechnungsbetrag: 1.234,56 EUR",
    "Bitte überweisen Sie auf IBAN DE89 3704 0044 0532 0130 00",
]

INSURANCE = [
    "Musterversicherung AG",
    "Versicherungsschein Nr. 998877",
    "Datum: 02.01.2026",
    "Privathaftpflichtversicherung",
    "Versicherungsnehmer: Max Mustermann",
    "Jahresbeitrag: 65,40 EUR",
    "Ihr Versicherungsschutz beginnt am 01.02.2026.",
]

DOCTOR = [
    "Praxis Dr. med. Erika Beispiel",
    "Befundbericht vom 21. Februar 2026",
    "Patient: Max Mustermann",
    "Diagnose: leichte Zerrung, Behandlung mit Physiotherapie.",
    "Rezept für Krankengymnastik anbei.",
]
