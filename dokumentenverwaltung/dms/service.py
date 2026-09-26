"""Geschäftslogik: Import, Vorschlag, Ablage, Suche und Ordnerverwaltung.

Ablageverzeichnis:
    <data>/ablage/Eingang/...              noch nicht bestätigte Dokumente
    <data>/ablage/<Ordner>/<Unterordner>/  abgelegte Dokumente,
                                           Name: <Datum>_<Titel>.<Endung>
    <data>/vorschau/<id>.jpg               Vorschaubild der ersten Seite
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import analyze, classify, extract
from .textutil import safe_filename

INBOX_DIR = "Eingang"


class DmsError(Exception):
    status = 400


class NotFound(DmsError):
    status = 404


class Conflict(DmsError):
    status = 409

    def __init__(self, message, document_id=None):
        super().__init__(message)
        self.document_id = document_id


@dataclass
class Upload:
    filename: str
    data: bytes


class Store:
    def __init__(self, conn: sqlite3.Connection, data_dir: Path):
        self.conn = conn
        self.data_dir = Path(data_dir)
        self.files_dir = self.data_dir / "ablage"
        self.preview_dir = self.data_dir / "vorschau"
        for d in (self.files_dir / INBOX_DIR, self.preview_dir, self.data_dir / "tmp"):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- Import

    def ingest_files(self, uploads: list[Upload], source: str = "upload") -> list[dict]:
        """Jede Datei wird ein eigenes Dokument."""
        results = []
        for up in uploads:
            try:
                results.append({"document": self._ingest_one(up.filename, up.data, source)})
            except Conflict as exc:
                results.append({"error": str(exc), "filename": up.filename, "document_id": exc.document_id})
            except DmsError as exc:
                results.append({"error": str(exc), "filename": up.filename})
        return results

    def ingest_scan(self, pages: list[Upload], title: str | None = None) -> dict:
        """Mehrere Handyfotos werden zu einem PDF-Dokument zusammengefügt."""
        if not pages:
            raise DmsError("Keine Seiten übermittelt")
        images, texts = [], []
        for p in pages:
            try:
                img = Image.open(io.BytesIO(p.data))
                img.load()
            except Exception:
                raise DmsError(f"{p.filename}: kein lesbares Bild")
            images.append(extract.upright(img))
        warnings = []
        if extract.ocr_available():
            texts = [extract.ocr_image(img) for img in images]
        else:
            warnings.append("Tesseract fehlt – kein OCR")
        with tempfile.NamedTemporaryFile(suffix=".pdf", dir=self.data_dir / "tmp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            extract.images_to_pdf(images, tmp_path)
            name = safe_filename(title or f"Scan {datetime.now():%Y-%m-%d %H-%M}") + ".pdf"
            result = extract.ExtractResult(
                extract._clean("\n\n".join(texts)), pages=len(images),
                method="ocr" if texts else "none", warnings=warnings,
            )
            return self._create(tmp_path.read_bytes(), name, "scan", result, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _ingest_one(self, filename: str, data: bytes, source: str) -> dict:
        filename = safe_filename(Path(filename or "Datei").name)
        ext = Path(filename).suffix.lower()
        if ext not in extract.SUPPORTED_EXTS:
            raise DmsError(f"Dateityp {ext or '(ohne Endung)'} wird nicht unterstützt")
        self._check_duplicate(data)
        with tempfile.NamedTemporaryFile(suffix=ext, dir=self.data_dir / "tmp", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            result = extract.extract(tmp_path, filename)
            return self._create(data, filename, source, result, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _check_duplicate(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        row = self.conn.execute("SELECT id, title FROM documents WHERE sha256 = ?", (sha,)).fetchone()
        if row:
            raise Conflict(f"Bereits vorhanden als „{row['title']}“", row["id"])
        return sha

    def _create(self, data: bytes, filename: str, source: str, result: extract.ExtractResult, src_path: Path) -> dict:
        sha = self._check_duplicate(data)
        conn = self.conn
        df, n_docs = classify._idf(conn)
        known = [r[0] for r in conn.execute(
            "SELECT DISTINCT correspondent FROM documents WHERE correspondent IS NOT NULL AND status='abgelegt'")]
        info = analyze.analyze(result.text, Path(filename).stem, df, n_docs, known)
        suggestions = classify.suggest(conn, result.text, info.correspondent)

        cur = conn.execute(
            """INSERT INTO documents(title, original_name, file_path, sha256, size, source, suggestion,
                   doc_type, doc_date, correspondent, amount, iban, text, extract_method, pages, warnings)
               VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (info.title, filename, sha, len(data), source, json.dumps(suggestions, ensure_ascii=False),
             info.doc_type, info.doc_date, info.correspondent, info.amount, info.iban, result.text,
             result.method, result.pages, "\n".join(result.warnings)),
        )
        doc_id = cur.lastrowid
        rel = Path(INBOX_DIR) / f"{doc_id}_{filename}"
        shutil.copyfile(src_path, self.files_dir / rel)
        has_preview = extract.render_preview(self.files_dir / rel, filename, self.preview_dir / f"{doc_id}.jpg")
        conn.execute("UPDATE documents SET file_path = ?, has_preview = ? WHERE id = ?",
                     (rel.as_posix(), int(has_preview), doc_id))

        tags = ([info.doc_type] if info.doc_type else []) + info.keywords
        self._set_tags(doc_id, tags)
        classify.update_df(conn, result.text, +1)
        self._index(doc_id)
        conn.commit()
        return self.get(doc_id)

    # ------------------------------------------------------------- Lesen

    def get(self, doc_id: int, with_text: bool = True) -> dict:
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise NotFound("Dokument nicht gefunden")
        return self._to_dict(row, with_text)

    def _to_dict(self, row: sqlite3.Row, with_text: bool = False, snippet: str | None = None) -> dict:
        paths = classify.folder_paths(self.conn)
        d = {k: row[k] for k in row.keys() if k not in ("text", "sha256", "suggestion")}
        d["tags"] = self._tags(row["id"])
        d["folder_path"] = paths.get(row["folder_id"]) if row["folder_id"] else None
        d["warnings"] = [w for w in (row["warnings"] or "").split("\n") if w]
        suggestion = json.loads(row["suggestion"] or "[]")
        for s in suggestion:  # Ordner könnte inzwischen umbenannt/gelöscht sein
            s["path"] = paths.get(s["folder_id"], s["path"])
        d["suggestion"] = [s for s in suggestion if s["folder_id"] in paths]
        if with_text:
            d["text"] = row["text"]
        if snippet is not None:
            d["snippet"] = snippet
        return d

    def _tags(self, doc_id: int) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id = t.id WHERE dt.document_id = ? ORDER BY t.name COLLATE NOCASE",
            (doc_id,))]

    def search(self, q: str = "", status: str | None = None, folder_id: int | None = None,
               tag: str | None = None, limit: int = 200) -> list[dict]:
        where, args = [], []
        select = "SELECT d.*, NULL AS snip FROM documents d"
        fts = _fts_query(q)
        if fts:
            select = ("SELECT d.*, snippet(documents_fts, 3, char(1), char(2), '…', 14) AS snip "
                      "FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid")
            where.append("documents_fts MATCH ?")
            args.append(fts)
        if status:
            where.append("d.status = ?")
            args.append(status)
        if folder_id is not None:
            ids = self._subtree(folder_id)
            where.append(f"d.folder_id IN ({','.join('?' * len(ids))})")
            args.extend(ids)
        if tag:
            where.append("d.id IN (SELECT dt.document_id FROM document_tags dt JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)")
            args.append(tag)
        sql = select + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY " + ("bm25(documents_fts), " if fts else "") + \
               "COALESCE(d.doc_date, substr(d.created_at, 1, 10)) DESC, d.id DESC LIMIT ?"
        args.append(limit)
        return [self._to_dict(r, snippet=r["snip"]) for r in self.conn.execute(sql, args)]

    def stats(self) -> dict:
        c = self.conn
        return {
            "eingang": c.execute("SELECT COUNT(*) FROM documents WHERE status='eingang'").fetchone()[0],
            "abgelegt": c.execute("SELECT COUNT(*) FROM documents WHERE status='abgelegt'").fetchone()[0],
            "ocr": extract.ocr_available(),
            "version": os.environ.get("DMS_VERSION", "dev"),
        }

    def all_tags(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT t.name, COUNT(dt.document_id) AS count FROM tags t JOIN document_tags dt ON dt.tag_id = t.id "
            "GROUP BY t.id ORDER BY count DESC, t.name COLLATE NOCASE")]

    def file_path(self, doc_id: int) -> tuple[Path, str]:
        doc = self.get(doc_id, with_text=False)
        return self.files_dir / doc["file_path"], doc["original_name"]

    # ------------------------------------------------------------ Ablegen

    def file_document(self, doc_id: int, folder_id: int, **fields) -> dict:
        """Bestätigt (oder korrigiert) die Ablage und lernt daraus."""
        conn = self.conn
        row = self._row(doc_id)
        if not conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
            raise DmsError("Ordner existiert nicht")
        self._apply_fields(doc_id, fields)
        if row["status"] == "abgelegt" and row["folder_id"]:
            classify.learn(conn, row["folder_id"], row["text"], -1)
        classify.learn(conn, folder_id, row["text"], +1)
        conn.execute(
            "UPDATE documents SET status='abgelegt', folder_id=?, filed_at=datetime('now') WHERE id=?",
            (folder_id, doc_id))
        self._place_file(doc_id)
        self._index(doc_id)
        conn.commit()
        return self.get(doc_id)

    def update_document(self, doc_id: int, **fields) -> dict:
        """Ändert Titel, Datum, Schlagwörter usw., ohne den Ordner zu ändern."""
        row = self._row(doc_id)
        self._apply_fields(doc_id, fields)
        if row["status"] == "abgelegt":
            self._place_file(doc_id)
        self._index(doc_id)
        self.conn.commit()
        return self.get(doc_id)

    def resuggest(self, doc_id: int) -> dict:
        row = self._row(doc_id)
        suggestions = classify.suggest(self.conn, row["text"], row["correspondent"], exclude_doc=doc_id)
        self.conn.execute("UPDATE documents SET suggestion = ? WHERE id = ?",
                          (json.dumps(suggestions, ensure_ascii=False), doc_id))
        self.conn.commit()
        return self.get(doc_id)

    def delete_document(self, doc_id: int) -> None:
        row = self._row(doc_id)
        if row["status"] == "abgelegt" and row["folder_id"]:
            classify.learn(self.conn, row["folder_id"], row["text"], -1)
        classify.update_df(self.conn, row["text"], -1)
        self.conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self.conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM document_tags)")
        self.conn.commit()
        path = self.files_dir / row["file_path"]
        path.unlink(missing_ok=True)
        (self.preview_dir / f"{doc_id}.jpg").unlink(missing_ok=True)
        self._prune_dirs(path.parent)

    def _row(self, doc_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise NotFound("Dokument nicht gefunden")
        return row

    def _apply_fields(self, doc_id: int, fields: dict) -> None:
        editable = {"title", "doc_date", "correspondent", "notes", "amount", "doc_type"}
        updates = {}
        for k, v in fields.items():
            if k in editable:
                if isinstance(v, str):
                    v = v.strip() or None
                if k == "title" and not v:
                    raise DmsError("Titel darf nicht leer sein")
                if k == "doc_date" and v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    raise DmsError("Datum bitte als JJJJ-MM-TT")
                if k == "notes":
                    v = v or ""
                if k == "amount" and v is not None:
                    try:
                        v = float(str(v).replace(",", "."))
                    except ValueError:
                        raise DmsError("Betrag ist keine Zahl")
                updates[k] = v
        if updates:
            self.conn.execute(
                f"UPDATE documents SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                (*updates.values(), doc_id))
        if fields.get("tags") is not None:
            self._set_tags(doc_id, fields["tags"])

    def _set_tags(self, doc_id: int, names) -> None:
        conn = self.conn
        conn.execute("DELETE FROM document_tags WHERE document_id = ?", (doc_id,))
        seen = set()
        for name in names:
            name = re.sub(r"\s+", " ", str(name)).strip(" ,;#")[:60]
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO document_tags VALUES (?, ?)", (doc_id, tag_id))
        conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM document_tags)")

    def _index(self, doc_id: int) -> None:
        row = self._row(doc_id)
        self.conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
        self.conn.execute(
            "INSERT INTO documents_fts(rowid, title, correspondent, tags, text) VALUES (?, ?, ?, ?, ?)",
            (doc_id, row["title"], row["correspondent"] or "", " ".join(self._tags(doc_id)), row["text"]))

    # ----------------------------------------------------- Dateien im Ordner

    def _folder_dir(self, folder_id: int) -> Path:
        parts = []
        fid = folder_id
        while fid is not None:
            r = self.conn.execute("SELECT parent_id, name FROM folders WHERE id = ?", (fid,)).fetchone()
            parts.append(safe_filename(r["name"]))
            fid = r["parent_id"]
        return Path(*reversed(parts))

    def _place_file(self, doc_id: int) -> None:
        """Verschiebt die Datei an ihren Platz: <Ordner>/<Datum>_<Titel>.<Endung>."""
        row = self._row(doc_id)
        if row["status"] != "abgelegt" or not row["folder_id"]:
            return
        current = self.files_dir / row["file_path"]
        ext = Path(row["original_name"]).suffix.lower()
        day = row["doc_date"] or row["created_at"][:10]
        target_dir = self._folder_dir(row["folder_id"])
        stem = f"{day}_{safe_filename(row['title'], 100)}"
        rel = target_dir / f"{stem}{ext}"
        n = 2
        while (self.files_dir / rel).exists() and (self.files_dir / rel) != current:
            rel = target_dir / f"{stem} ({n}){ext}"
            n += 1
        if (self.files_dir / rel) == current:
            return
        (self.files_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(current, self.files_dir / rel)
        self.conn.execute("UPDATE documents SET file_path = ? WHERE id = ?", (rel.as_posix(), doc_id))
        self._prune_dirs(current.parent)

    def _prune_dirs(self, d: Path) -> None:
        """Entfernt leer gewordene Ordner (nicht die Wurzel und nicht den Eingang)."""
        keep = {self.files_dir.resolve(), (self.files_dir / INBOX_DIR).resolve()}
        d = d.resolve()
        while d not in keep and self.files_dir.resolve() in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent

    # -------------------------------------------------------------- Ordner

    def folder_tree(self) -> list[dict]:
        rows = [dict(r) for r in self.conn.execute(
            "SELECT f.id, f.parent_id, f.name, f.keywords, "
            "(SELECT COUNT(*) FROM documents d WHERE d.folder_id = f.id AND d.status='abgelegt') AS count "
            "FROM folders f ORDER BY f.name COLLATE NOCASE")]
        by_parent: dict = {}
        for r in rows:
            r["children"] = []
            by_parent.setdefault(r["parent_id"], []).append(r)
        for r in rows:
            r["children"] = by_parent.get(r["id"], [])
        paths = classify.folder_paths(self.conn)

        def total(node):
            node["path"] = paths[node["id"]]
            node["total"] = node["count"] + sum(total(c) for c in node["children"])
            return node["total"]

        roots = by_parent.get(None, [])
        for r in roots:
            total(r)
        return roots

    def create_folder(self, name: str, parent_id: int | None = None, keywords: str = "") -> dict:
        name = _folder_name(name)
        if parent_id is not None and not self.conn.execute("SELECT 1 FROM folders WHERE id=?", (parent_id,)).fetchone():
            raise DmsError("Übergeordneter Ordner existiert nicht")
        try:
            cur = self.conn.execute("INSERT INTO folders(parent_id, name, keywords) VALUES (?, ?, ?)",
                                    (parent_id, name, _clean_keywords(keywords)))
        except sqlite3.IntegrityError:
            raise Conflict(f"Ordner „{name}“ gibt es hier schon")
        self.conn.commit()
        return self._folder(cur.lastrowid)

    def update_folder(self, folder_id: int, name=None, keywords=None, parent_id="unchanged") -> dict:
        f = self._folder(folder_id)
        new_name = _folder_name(name) if name is not None else f["name"]
        new_parent = f["parent_id"] if parent_id == "unchanged" else parent_id
        if new_parent is not None:
            if new_parent == folder_id or new_parent in self._subtree(folder_id):
                raise DmsError("Ein Ordner kann nicht in sich selbst verschoben werden")
            self._folder(new_parent)
        try:
            self.conn.execute(
                "UPDATE folders SET name=?, parent_id=?, keywords=? WHERE id=?",
                (new_name, new_parent, _clean_keywords(keywords) if keywords is not None else f["keywords"], folder_id))
        except sqlite3.IntegrityError:
            raise Conflict(f"Ordner „{new_name}“ gibt es hier schon")
        if new_name != f["name"] or new_parent != f["parent_id"]:
            ids = self._subtree(folder_id)
            for r in self.conn.execute(
                    f"SELECT id FROM documents WHERE status='abgelegt' AND folder_id IN ({','.join('?' * len(ids))})", ids).fetchall():
                self._place_file(r["id"])
        self.conn.commit()
        return self._folder(folder_id)

    def delete_folder(self, folder_id: int) -> None:
        self._folder(folder_id)
        if self.conn.execute("SELECT 1 FROM folders WHERE parent_id = ?", (folder_id,)).fetchone():
            raise DmsError("Ordner enthält Unterordner")
        if self.conn.execute("SELECT 1 FROM documents WHERE folder_id = ?", (folder_id,)).fetchone():
            raise DmsError("Ordner enthält Dokumente")
        self.conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        self.conn.commit()

    def _folder(self, folder_id: int) -> dict:
        r = self.conn.execute("SELECT id, parent_id, name, keywords FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if not r:
            raise NotFound("Ordner nicht gefunden")
        d = dict(r)
        d["path"] = classify.folder_paths(self.conn)[folder_id]
        return d

    def _subtree(self, folder_id: int) -> list[int]:
        return [r[0] for r in self.conn.execute(
            "WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id) "
            "SELECT id FROM sub", (folder_id,))]


def _folder_name(name) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name:
        raise DmsError("Ordnername fehlt")
    if "/" in name or "\\" in name or name in (".", "..") or name.lower() == INBOX_DIR.lower():
        raise DmsError("Ungültiger Ordnername")
    return name[:80]


def _clean_keywords(raw: str) -> str:
    return ", ".join(dict.fromkeys(k.lower() for k in classify.split_keywords(raw or "")))


def _fts_query(q: str) -> str:
    """Macht aus einer Suchanfrage eine sichere FTS5-Abfrage mit Präfixsuche."""
    words = re.findall(r"[^\W_]+", q or "", re.UNICODE)
    return " AND ".join(f'"{w}"*' for w in words)
