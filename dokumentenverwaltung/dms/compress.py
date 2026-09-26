"""Verlustfreie Optimierung gespeicherter Dokumente.

Die Dateien bleiben im selben Format (PDF bleibt PDF, JPEG bleibt JPEG …) und
lassen sich weiter mit jedem Programm öffnen. Jede optimierte Fassung wird vor
dem Ersetzen geprüft – identische Bildpunkte, identischer Text, gleiche
Seitenzahl, gleiche ZIP-Inhalte. Ist sie nicht kleiner oder schlägt eine
Prüfung fehl, bleibt das Original unverändert.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from PIL import Image, ImageSequence

log = logging.getLogger(__name__)

ZIP_EXTS = {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".pptx", ".pptm", ".odt", ".ods", ".odp", ".ott"}
JPEG_EXTS = {".jpg", ".jpeg"}
TIFF_EXTS = {".tif", ".tiff"}


class NotLossless(Exception):
    pass


def available() -> bool:
    return shutil.which("jpegtran") is not None


# -------------------------------------------------------------------- JPEG

def _pixels(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        return img.mode, img.size, img.tobytes()


def jpeg_lossless(data: bytes, keep_metadata: bool = True) -> bytes:
    """JPEG verlustfrei neu kodieren (optimierte Huffman-Tabellen, progressiv).

    jpegtran verändert die DCT-Koeffizienten nicht; zur Sicherheit wird das
    dekodierte Bild trotzdem Pixel für Pixel verglichen."""
    if not available():
        return data
    best = data
    for args in (["-progressive"], []):
        cmd = ["jpegtran", "-optimize", "-copy", "all" if keep_metadata else "none", *args]
        try:
            out = subprocess.run(cmd, input=data, capture_output=True, timeout=120, check=True).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        if out and len(out) < len(best):
            try:
                if _pixels(out) == _pixels(data):
                    best = out
                    break
            except Exception:
                continue
    return best


# ------------------------------------------------------------ PNG / TIFF

def _frames(data: bytes) -> list:
    with Image.open(io.BytesIO(data)) as img:
        return [(f.mode, f.size, f.tobytes()) for f in (fr.copy() for fr in ImageSequence.Iterator(img))]


def png_lossless(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        info = {k: v for k, v in img.info.items() if k in ("icc_profile", "dpi", "gamma", "transparency")}
        if getattr(img, "n_frames", 1) > 1:
            return data  # animierte PNG unverändert lassen
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True, compress_level=9, **info)
    out = buf.getvalue()
    if len(out) < len(data) and _frames(out) == _frames(data):
        return out
    return data


def tiff_lossless(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        frames = [f.copy() for f in ImageSequence.Iterator(img)]
        dpi = img.info.get("dpi")
    if any(f.mode not in ("1", "L", "RGB", "RGBA", "CMYK", "P", "LA") for f in frames):
        return data
    buf = io.BytesIO()
    kw = {"dpi": dpi} if dpi else {}
    compression = "group4" if all(f.mode == "1" for f in frames) else "tiff_adobe_deflate"
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:], compression=compression, **kw)
    out = buf.getvalue()
    if len(out) < len(data) and _frames(out) == _frames(data):
        return out
    return data


# --------------------------------------------------------------------- ZIP

def zip_lossless(data: bytes) -> bytes:
    """Office-Dateien (ZIP) mit höchster Stufe neu packen; Reihenfolge bleibt,
    'mimetype' (OpenDocument) bleibt unkomprimiert an erster Stelle."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as dst:
        for info in src.infolist():
            content = src.read(info)
            new = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            new.external_attr = info.external_attr
            new.comment = info.comment
            if info.filename == "mimetype" or info.compress_type == zipfile.ZIP_STORED and info.filename.lower().endswith((".jpg", ".jpeg", ".png")):
                new.compress_type = zipfile.ZIP_STORED
                dst.writestr(new, content)
            else:
                new.compress_type = zipfile.ZIP_DEFLATED
                dst.writestr(new, content, compresslevel=9)
        dst.comment = src.comment
    out = buf.getvalue()
    if len(out) >= len(data):
        return data
    check = zipfile.ZipFile(io.BytesIO(out))
    if [i.filename for i in check.infolist()] != [i.filename for i in src.infolist()]:
        raise NotLossless("ZIP-Einträge weichen ab")
    for a, b in zip(src.infolist(), check.infolist()):
        if a.CRC != b.CRC or a.file_size != b.file_size:
            raise NotLossless(f"ZIP-Eintrag {a.filename} weicht ab")
    if check.testzip() is not None:
        raise NotLossless("ZIP defekt")
    return out


# --------------------------------------------------------------------- PDF

def _page_images(page):
    get = getattr(page, "get_images", None)  # pikepdf ≥ 10
    return (get() if get else page.images).items()


def _pdf_fingerprint(data: bytes) -> tuple:
    """Seitenzahl, Text und dekodierte Bilder – muss vorher/nachher gleich sein."""
    import pikepdf
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data))
    texts = tuple((p.extract_text() or "") for p in reader.pages)
    images = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for _name, raw in _page_images(page):
                try:
                    images.append(pikepdf.PdfImage(raw).as_pil_image().tobytes())
                except Exception:
                    images.append(bytes(raw.read_bytes()))
    return len(reader.pages), texts, tuple(images)


def pdf_lossless(data: bytes) -> bytes:
    import pikepdf

    with pikepdf.open(io.BytesIO(data)) as pdf:
        # eingebettete JPEGs (Scans) verlustfrei optimieren
        seen = set()
        for page in pdf.pages:
            for _name, obj in _page_images(page):
                key = obj.objgen
                if key in seen:
                    continue
                seen.add(key)
                filt = obj.get("/Filter")
                if filt != pikepdf.Name.DCTDecode or obj.get("/DecodeParms") is not None:
                    continue
                raw = bytes(obj.read_raw_bytes())
                better = jpeg_lossless(raw, keep_metadata=False)
                if len(better) < len(raw):
                    obj.write(better, filter=pikepdf.Name.DCTDecode)
        buf = io.BytesIO()
        pdf.save(buf, compress_streams=True, recompress_flate=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate)
    out = buf.getvalue()
    if len(out) >= len(data):
        return data
    if _pdf_fingerprint(out) != _pdf_fingerprint(data):
        raise NotLossless("PDF-Inhalt weicht ab")
    return out


# ------------------------------------------------------------------ Einstieg

def optimize_bytes(data: bytes, ext: str) -> bytes:
    ext = ext.lower()
    if ext == ".pdf":
        return pdf_lossless(data)
    if ext in JPEG_EXTS:
        return jpeg_lossless(data)
    if ext == ".png":
        return png_lossless(data)
    if ext in TIFF_EXTS:
        return tiff_lossless(data)
    if ext in ZIP_EXTS:
        return zip_lossless(data)
    return data


def optimize_file(path: Path, ext: str) -> bytes | None:
    """Liefert die optimierte Fassung oder None, wenn nichts zu gewinnen ist.
    Die Datei selbst wird hier nicht verändert (das macht der Aufrufer unter Sperre)."""
    data = path.read_bytes()
    try:
        out = optimize_bytes(data, ext)
    except NotLossless as exc:
        log.warning("Nicht optimiert (%s): %s", path.name, exc)
        return None
    except Exception as exc:  # defekte oder ungewöhnliche Datei: Original behalten
        log.info("Nicht optimiert (%s): %s: %s", path.name, type(exc).__name__, exc)
        return None
    return out if len(out) < len(data) else None


def replace_file(path: Path, data: bytes) -> None:
    """Atomar ersetzen (erst temporär schreiben, dann umbenennen)."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".opt-", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
