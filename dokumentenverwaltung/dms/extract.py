"""Texterkennung: liest Text aus Office-Dateien und PDFs und erkennt ihn per
OCR (Tesseract) in Scans und Fotos."""

from __future__ import annotations

import email
import email.policy
import html
import io
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageOps

try:  # optional: HEIC-Fotos vom iPhone
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

# Eine PDF-Seite mit weniger Zeichen gilt als gescannt und wird per OCR gelesen.
MIN_TEXT_PER_PAGE = 25
OCR_DPI = 300

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif"}
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".log"}
OOXML_EXTS = {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".pptx", ".pptm"}
ODF_EXTS = {".odt", ".ods", ".odp", ".ott"}
SUPPORTED_EXTS = (
    IMAGE_EXTS | TEXT_EXTS | OOXML_EXTS | ODF_EXTS
    | {".pdf", ".html", ".htm", ".rtf", ".eml", ".doc"}
)


@dataclass
class ExtractResult:
    text: str
    pages: int = 1
    method: str = "text"  # text | ocr | mixed | none
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- OCR

_ocr_langs: str | None = None


def ocr_available() -> bool:
    if pytesseract is None:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _langs() -> str:
    global _ocr_langs
    if _ocr_langs is None:
        try:
            have = set(pytesseract.get_languages(config=""))
        except Exception:
            have = set()
        wanted = [lang for lang in ("deu", "eng") if lang in have]
        _ocr_langs = "+".join(wanted) or "eng"
    return _ocr_langs


def prepare_scan(img: Image.Image) -> Image.Image:
    """Richtet ein Handyfoto aus und verbessert den Kontrast für die OCR."""
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    gray = ImageOps.grayscale(img)
    # Kleine Bilder hochskalieren, Tesseract mag ~300 dpi.
    if max(gray.size) < 1800:
        factor = 1800 / max(gray.size)
        gray = gray.resize((int(gray.width * factor), int(gray.height * factor)), Image.LANCZOS)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    return _auto_rotate(gray)


def detect_rotation(img: Image.Image) -> int:
    """Grad, um die eine quer oder kopfüber fotografierte Seite gedreht werden muss."""
    if not ocr_available():
        return 0
    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        angle = int(osd.get("rotate", 0))
        if angle and float(osd.get("orientation_conf", 0)) > 1.5:
            return angle
    except Exception:
        pass  # zu wenig Text für OSD – Bild unverändert lassen
    return 0


def _auto_rotate(img: Image.Image) -> Image.Image:
    angle = detect_rotation(img)
    return img.rotate(-angle, expand=True) if angle else img


def upright(img: Image.Image) -> Image.Image:
    """Handyfoto nach EXIF und Textrichtung aufrecht drehen (für die Ablage)."""
    img = ImageOps.exif_transpose(img)
    small = ImageOps.grayscale(img)
    small.thumbnail((1800, 1800))
    angle = detect_rotation(ImageOps.autocontrast(small, cutoff=1))
    return img.rotate(-angle, expand=True) if angle else img


def ocr_image(img: Image.Image) -> str:
    if not ocr_available():
        raise RuntimeError("Tesseract ist nicht installiert – keine Texterkennung möglich")
    return pytesseract.image_to_string(prepare_scan(img), lang=_langs())


# ----------------------------------------------------------------- Extraktion


def extract(path: Path, original_name: str | None = None) -> ExtractResult:
    """Liest den Text einer Datei; die Endung von original_name bestimmt den Typ."""
    ext = Path(original_name or path.name).suffix.lower()
    try:
        if ext == ".pdf":
            result = _pdf(path)
        elif ext in IMAGE_EXTS:
            result = _images(path)
        elif ext in OOXML_EXTS:
            result = ExtractResult(_ooxml(path))
        elif ext in ODF_EXTS:
            result = ExtractResult(_odf(path))
        elif ext in (".html", ".htm"):
            result = ExtractResult(_html_to_text(_read_text(path)))
        elif ext == ".rtf":
            result = ExtractResult(_rtf_to_text(_read_text(path)))
        elif ext == ".eml":
            result = ExtractResult(_eml(path))
        elif ext == ".doc":
            result = _legacy_doc(path)
        elif ext in TEXT_EXTS:
            result = ExtractResult(_read_text(path))
        else:
            return ExtractResult("", method="none", warnings=[f"Dateityp {ext or '?'} wird nicht unterstützt"])
    except Exception as exc:  # defekte Datei soll den Import nicht abbrechen
        return ExtractResult("", method="none", warnings=[f"Text konnte nicht gelesen werden: {exc}"])
    result.text = _clean(result.text)
    if not result.text:
        result.warnings.append("Kein Text gefunden")
    return result


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _pdf(path: Path) -> ExtractResult:
    import pypdf

    reader = pypdf.PdfReader(str(path))
    texts: list[str] = []
    ocr_pages: list[int] = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        texts.append(t)
        if len(t.strip()) < MIN_TEXT_PER_PAGE:
            ocr_pages.append(i)

    warnings = []
    if ocr_pages:
        if ocr_available():
            import pypdfium2

            pdf = pypdfium2.PdfDocument(str(path))
            for i in ocr_pages:
                img = pdf[i].render(scale=OCR_DPI / 72).to_pil()
                texts[i] = ocr_image(img)
            pdf.close()
        else:
            warnings.append("Gescannte Seiten gefunden, aber Tesseract fehlt – kein OCR")

    if not ocr_pages:
        method = "text"
    elif len(ocr_pages) == len(texts):
        method = "ocr"
    else:
        method = "mixed"
    return ExtractResult("\n\n".join(texts), pages=len(texts), method=method, warnings=warnings)


def _images(path: Path) -> ExtractResult:
    with Image.open(path) as img:
        frames = getattr(img, "n_frames", 1)
        texts = []
        for i in range(frames):
            img.seek(i)
            texts.append(ocr_image(img.copy()))
    return ExtractResult("\n\n".join(texts), pages=frames, method="ocr")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ooxml_paragraphs(xml: bytes, para: str = "p") -> str:
    """Text aus Word-/PowerPoint-XML, ein Absatz je <w:p>/<a:p>."""
    out = []
    for p in ElementTree.fromstring(xml).iter():
        if _local(p.tag) != para:
            continue
        buf = []
        for el in p.iter():
            name = _local(el.tag)
            if name == "t" and el.text:
                buf.append(el.text)
            elif name == "tab":
                buf.append("\t")
            elif name in ("br", "cr"):
                buf.append("\n")
        out.append("".join(buf))
    return "\n".join(out)


def _xlsx_sheet(xml: bytes, shared: list[str]) -> str:
    rows = []
    for row in ElementTree.fromstring(xml).iter():
        if _local(row.tag) != "row":
            continue
        cells = []
        for c in row:
            if _local(c.tag) != "c":
                continue
            kind = c.get("t")
            value = next((e.text for e in c if _local(e.tag) == "v"), None)
            if kind == "s" and value is not None:
                cells.append(shared[int(value)] if int(value) < len(shared) else "")
            elif kind == "inlineStr":
                cells.append("".join(e.text or "" for e in c.iter() if _local(e.tag) == "t"))
            elif value is not None:
                cells.append(value)
        rows.append("\t".join(cells))
    return "\n".join(rows)


def _ooxml(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        parts: list[str] = []
        if "word/document.xml" in names:
            order = (
                [n for n in names if re.match(r"word/header\d*\.xml$", n)]
                + ["word/document.xml"]
                + [n for n in names if re.match(r"word/(footer\d*|footnotes)\.xml$", n)]
            )
            parts = [_ooxml_paragraphs(z.read(n)) for n in order]
        elif any(n.startswith("ppt/slides/") for n in names):
            slides = sorted(
                (n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
            )
            parts = [_ooxml_paragraphs(z.read(n)) for n in slides]
        elif "xl/workbook.xml" in names:
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
                shared = [
                    "".join(t.text or "" for t in si.iter() if _local(t.tag) == "t")
                    for si in root if _local(si.tag) == "si"
                ]
            sheets = sorted(
                (n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
            )
            parts = [_xlsx_sheet(z.read(n), shared) for n in sheets]
        return "\n\n".join(parts)


def _odf(path: Path) -> str:
    """Text aus OpenDocument (LibreOffice), ein Absatz je <text:p>/<text:h>."""
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("content.xml"))

    def inner(el, buf):
        if el.text:
            buf.append(el.text)
        for child in el:
            name = _local(child.tag)
            if name == "s":
                buf.append(" " * int(child.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}c", "1")))
            elif name == "tab":
                buf.append("\t")
            elif name == "line-break":
                buf.append("\n")
            elif name not in ("p", "h", "note"):
                inner(child, buf)
            if child.tail:
                buf.append(child.tail)

    out = []
    for el in root.iter():
        if _local(el.tag) in ("p", "h"):
            buf: list[str] = []
            inner(el, buf)
            out.append("".join(buf))
    return "\n".join(out)


def _html_to_text(src: str) -> str:
    src = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", src)
    src = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", src)
    src = re.sub(r"(?s)<[^>]+>", " ", src)
    return html.unescape(src)


def _rtf_to_text(src: str) -> str:
    # Einfacher RTF-Konverter: reicht für Briefe und Rechnungen.
    src = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252"), src)
    src = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), src)
    src = re.sub(r"\\(par|line)\b ?", "\n", src)
    src = re.sub(r"\\tab\b ?", "\t", src)
    src = re.sub(r"\{\\\*[^{}]*\}", "", src)
    src = re.sub(r"\{\\(fonttbl|colortbl|stylesheet|info)(?:[^{}]|\{[^{}]*\})*\}", "", src)
    src = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", src)
    return src.replace("{", "").replace("}", "")


def _eml(path: Path) -> str:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    head = [f"{h}: {msg[h]}" for h in ("From", "To", "Date", "Subject") if msg[h]]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = _html_to_text(text)
    return "\n".join(head) + "\n\n" + text


def _legacy_doc(path: Path) -> ExtractResult:
    for tool in ("antiword", "catdoc"):
        if shutil.which(tool):
            out = subprocess.run([tool, str(path)], capture_output=True, timeout=60)
            if out.returncode == 0:
                return ExtractResult(out.stdout.decode("utf-8", "replace"))
    # Notlösung: Textstücke aus der Binärdatei (Word speichert UTF-16LE)
    raw = path.read_bytes()
    runs = re.findall(rb"(?:[\x20-\x7e\xc0-\xff]\x00){4,}", raw)
    text = "\n".join(r.decode("utf-16le", "ignore") for r in runs)
    return ExtractResult(text, warnings=["Altes .doc-Format: Text nur näherungsweise gelesen (antiword installieren)"])


# ------------------------------------------------------------ Scans & Vorschau


def images_to_pdf(images: list[Image.Image], target: Path) -> None:
    """Fügt mehrere Handy-Scans zu einem PDF zusammen (ausgerichtet, verkleinert)."""
    pages = []
    for img in images:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((2480, 2480))  # ~A4 bei 300 dpi
        pages.append(img)
    pages[0].save(target, "PDF", save_all=True, append_images=pages[1:], resolution=200, quality=80)


def render_preview(path: Path, original_name: str, target: Path, width: int = 900) -> bool:
    """Speichert ein Vorschaubild der ersten Seite. False, wenn nicht möglich."""
    ext = Path(original_name).suffix.lower()
    try:
        if ext == ".pdf":
            import pypdfium2

            pdf = pypdfium2.PdfDocument(str(path))
            page = pdf[0]
            img = page.render(scale=width / page.get_width()).to_pil()
            pdf.close()
        elif ext in IMAGE_EXTS:
            img = ImageOps.exif_transpose(Image.open(path))
        else:
            return False
        img = img.convert("RGB")
        img.thumbnail((width, width * 2))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        target.write_bytes(buf.getvalue())
        return True
    except Exception:
        return False
