"""Übernahme nach Paperless-ngx über dessen REST-API.

Abbildung:
    Ordner            → Speicherpfade (echte Ordnerstruktur, Zuordnung per Regex
                        aus den Erkennungsbegriffen – auch als Wortteil wie hier)
    Dokumentart       → Dokumenttypen
    Absender          → Korrespondenten
    Schlagwörter      → Tags
    Betrag/IBAN/Notiz → Notiz am Dokument
    Eingang           → Tag „Posteingang“ (Posteingangs-Tag in Paperless)

Bereits übertragene Dokumente werden gemerkt und bei einem erneuten Lauf
übersprungen; Speicherpfade, Tags usw. werden nur angelegt, wenn sie fehlen.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from . import analyze, classify, db

log = logging.getLogger(__name__)

INBOX_TAG = "Posteingang"
MATCH_NONE, MATCH_ANY, MATCH_LITERAL, MATCH_REGEX = 0, 1, 3, 4
TASK_TIMEOUT = 20 * 60  # Sekunden je Dokument (OCR auf dem Raspberry Pi kann dauern)
POLL_INTERVAL = 5

_lock = threading.Lock()


class PaperlessError(Exception):
    pass


# --------------------------------------------------------------- Einstellungen

def _get(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", ("paperless_" + key,)).fetchone()
    return row[0] if row else default


def _put(conn, **values):
    for key, value in values.items():
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", ("paperless_" + key,))
        else:
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         ("paperless_" + key, value if isinstance(value, str) else json.dumps(value)))
    conn.commit()


def status(conn) -> dict:
    state = _get(conn, "state")
    return {
        "url": _get(conn, "url", ""),
        "has_token": bool(_get(conn, "token")),
        "state": json.loads(state) if state else None,
        "running": _lock.locked(),
        "transferred": len(json.loads(_get(conn, "done", "{}"))),
    }


def configure(conn, url=None, token=None) -> dict:
    if url is not None:
        url = url.strip().rstrip("/")
        if url and not re.match(r"^https?://", url):
            raise PaperlessError("Adresse muss mit http:// oder https:// beginnen")
        url = re.sub(r"/api$", "", url)
        _put(conn, url=url or None)
    if token:
        _put(conn, token=token.strip())
    return status(conn)


# ------------------------------------------------------------------- HTTP

def http(method: str, url: str, *, token: str, params=None, body=None, files=None, fields=None):
    """Liefert (Statuscode, Header, Antwort). In Tests ersetzt."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    data = None
    if files is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in fields or []:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        for name, (filename, content) in files.items():
            safe = filename.replace('"', "'")
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
                         f'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n")
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (401, 403):
            raise PaperlessError("Paperless lehnt den Token ab (bitte Token prüfen)") from None
        raise PaperlessError(f"Paperless antwortet mit Fehler {exc.code}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PaperlessError(f"Paperless nicht erreichbar ({reason}). Stimmt die Adresse mit Port?") from None


class Client:
    def __init__(self, url: str, token: str):
        if not url or not token:
            raise PaperlessError("Bitte Adresse und API-Token eintragen")
        self.url, self.token = url.rstrip("/"), token
        self.version = None

    def call(self, method, path, **kw):
        status_code, headers, raw = http(method, f"{self.url}/api/{path}", token=self.token, **kw)
        if self.version is None:
            self.version = headers.get("X-Version") or headers.get("x-version")
        return json.loads(raw) if raw and raw[:1] in (b"{", b"[", b'"') else raw

    def check(self) -> str:
        self.call("GET", "documents/", params={"page_size": 1})
        return self.version or "unbekannt"

    def get_or_create(self, kind: str, name: str, **fields) -> int:
        found = self.call("GET", f"{kind}/", params={"name__iexact": name, "page_size": 1})
        if found.get("results"):
            return found["results"][0]["id"]
        return self.call("POST", f"{kind}/", body={"name": name, **fields})["id"]


# ------------------------------------------------------------ Hilfsfunktionen

_UMLAUTS = {"ä": "(?:ä|ae)", "ö": "(?:ö|oe)", "ü": "(?:ü|ue)", "ß": "(?:ß|ss)"}


def keyword_regex(words: list[str]) -> str:
    """Regex, die jeden Begriff auch als Wortteil findet („versicherung“ in
    „Haftpflichtversicherung“) und Umlaute wie ausgeschrieben behandelt."""
    alts = []
    for w in words:
        w = w.strip().lower()
        if not w:
            continue
        alts.append("".join(_UMLAUTS.get(c, re.escape(c)) for c in w).replace(r"\ ", r"\s+").replace(r"\-", r"[-\s]?"))
    return "(?:" + "|".join(alts) + ")" if alts else ""


def uses_jinja(version: str | None) -> bool:
    """Speicherpfad-Vorlagen: Jinja ab Paperless-ngx 2.10, davor {platzhalter}."""
    m = re.match(r"(\d+)\.(\d+)", version or "")
    return not m or (int(m.group(1)), int(m.group(2))) >= (2, 10)


def path_template(folder_path: str, jinja: bool) -> str:
    base = "/".join(p.strip().replace("/", "-") for p in folder_path.split(" / "))
    if jinja:
        return base + "/{{ created_year }}-{{ created_month }}-{{ created_day }}_{{ title }}"
    return base + "/{created_year}-{created_month}-{created_day}_{title}"


def _note(row) -> str:
    lines = []
    if row["notes"]:
        lines.append(row["notes"])
    if row["amount"] is not None:
        lines.append("Betrag: " + f"{row['amount']:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
    if row["iban"]:
        lines.append(f"IBAN: {row['iban']}")
    lines.append(f"Übernommen aus der Dokumentenverwaltung (Nr. {row['id']}, {row['original_name']})")
    return "\n".join(lines)


# ------------------------------------------------------------------ Übertragung

def test_connection(conn) -> dict:
    client = Client(_get(conn, "url"), _get(conn, "token"))
    return {"version": client.check()}


def migrate(store, include_inbox: bool = True, include_documents: bool = True) -> dict:
    """Überträgt Struktur und Dokumente. Liefert den Endstand."""
    if not _lock.acquire(blocking=False):
        raise PaperlessError("Eine Übertragung läuft bereits")
    conn = store.conn
    state = {"phase": "Verbindung prüfen", "total": 0, "done": 0, "skipped": 0, "errors": [],
             "started": datetime.now().isoformat(timespec="seconds"), "finished": None}

    def save(**kw):
        state.update(kw)
        _put(conn, state=state)

    try:
        save()
        client = Client(_get(conn, "url"), _get(conn, "token"))
        version = client.check()
        jinja = uses_jinja(version)
        save(version=version)

        # 1. Ordnerstruktur → Speicherpfade
        save(phase="Ordnerstruktur anlegen")
        paths = classify.folder_paths(conn)
        folders = {r["id"]: r for r in conn.execute("SELECT id, name, keywords FROM folders")}
        storage = {}
        for fid, fpath in sorted(paths.items(), key=lambda kv: kv[1]):
            words = classify.split_keywords(folders[fid]["keywords"])
            rx = keyword_regex(words)
            storage[fid] = client.get_or_create(
                "storage_paths", fpath, path=path_template(fpath, jinja),
                match=rx, matching_algorithm=MATCH_REGEX if rx else MATCH_NONE, is_insensitive=True)

        # 2. Dokumenttypen, Korrespondenten, Tags
        save(phase="Dokumenttypen, Absender und Tags anlegen")
        types = {}
        for name, words in analyze.DOC_TYPES:
            types[name] = client.get_or_create("document_types", name, match=keyword_regex(words),
                                               matching_algorithm=MATCH_REGEX, is_insensitive=True)
        for (name,) in conn.execute("SELECT DISTINCT doc_type FROM documents WHERE doc_type IS NOT NULL"):
            if name not in types:
                types[name] = client.get_or_create("document_types", name, matching_algorithm=MATCH_NONE)
        corrs = {}
        for (name,) in conn.execute("SELECT DISTINCT correspondent FROM documents WHERE correspondent IS NOT NULL"):
            corrs[name] = client.get_or_create("correspondents", name, match=name,
                                               matching_algorithm=MATCH_LITERAL, is_insensitive=True)
        tags = {}
        for (name,) in conn.execute("SELECT DISTINCT t.name FROM tags t JOIN document_tags dt ON dt.tag_id = t.id"):
            tags[name] = client.get_or_create("tags", name, matching_algorithm=MATCH_NONE)

        # 3. Dokumente
        done = json.loads(_get(conn, "done", "{}"))  # eigene ID → Paperless-ID
        sql = "SELECT * FROM documents" + ("" if include_inbox else " WHERE status = 'abgelegt'") + " ORDER BY id"
        rows = [r for r in conn.execute(sql) if str(r["id"]) not in done] if include_documents else []
        inbox_docs = []
        save(phase="Dokumente übertragen", total=len(rows))
        for i, row in enumerate(rows, 1):
            save(phase=f"Dokument {i} von {len(rows)}: {row['title']}")
            try:
                pid = _upload(client, store, row, storage, types, corrs, tags)
            except PaperlessError as exc:
                if "duplicate" in str(exc).lower() or "duplikat" in str(exc).lower():
                    save(skipped=state["skipped"] + 1)
                else:
                    state["errors"].append(f"{row['title']}: {exc}")
                    save()
                continue
            done[str(row["id"])] = pid
            _put(conn, done=done)
            if row["status"] == "eingang":
                inbox_docs.append(pid)
            save(done=state["done"] + 1)

        # 4. Unbestätigte Dokumente in den Posteingang von Paperless
        if inbox_docs:
            save(phase="Posteingang markieren")
            inbox = client.get_or_create("tags", INBOX_TAG, is_inbox_tag=True, matching_algorithm=MATCH_NONE,
                                         color="#f0b55a")
            client.call("POST", "documents/bulk_edit/", body={
                "documents": inbox_docs, "method": "add_tag", "parameters": {"tag": inbox}})
        save(phase="Fertig", folders=len(storage))
    except PaperlessError as exc:
        state["errors"].append(str(exc))
        save(phase="Abgebrochen")
    except Exception as exc:
        log.exception("Übertragung nach Paperless fehlgeschlagen")
        state["errors"].append(f"Unerwarteter Fehler: {type(exc).__name__}: {exc}")
        save(phase="Abgebrochen")
    finally:
        save(finished=datetime.now().isoformat(timespec="seconds"))
        _lock.release()
    return state


def text_pdf(title: str, text: str) -> bytes:
    """Einfaches PDF mit dem erkannten Text – für Dateitypen, die Paperless ohne
    Tika/Gotenberg nicht annimmt (Word, LibreOffice, RTF …)."""
    import textwrap

    lines = [title, ""]
    for para in (text or "(kein Text erkannt)").splitlines():
        lines += textwrap.wrap(para, 95) or [""]
    pages = [lines[i:i + 60] for i in range(0, len(lines), 60)] or [[]]

    def esc(s):
        return s.encode("cp1252", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for page in pages:
        content = b"BT /F1 10 Tf 50 800 Td 12.5 TL " + b" ".join(b"(" + esc(l) + b") '" for l in page) + b" ET"
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 3 0 R >> >> >>" % (len(objs)))
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _upload(client, store, row, storage, types, corrs, tags) -> int:
    path = store.files_dir / row["file_path"]
    if not path.is_file():
        raise PaperlessError("Datei fehlt im Ablageverzeichnis")
    fields = [("title", row["title"]), ("created", row["doc_date"] or row["created_at"][:10])]
    if row["correspondent"] in corrs:
        fields.append(("correspondent", corrs[row["correspondent"]]))
    if row["doc_type"] in types:
        fields.append(("document_type", types[row["doc_type"]]))
    if row["status"] == "abgelegt" and row["folder_id"] in storage:
        fields.append(("storage_path", storage[row["folder_id"]]))
    for name in store._tags(row["id"]):
        if name in tags:
            fields.append(("tags", tags[name]))
    note = _note(row)
    try:
        task = client.call("POST", "documents/post_document/",
                           files={"document": (row["original_name"], path.read_bytes())}, fields=fields)
    except PaperlessError as exc:
        if "not supported" not in str(exc).lower():
            raise
        # Paperless ohne Tika/Gotenberg: Textfassung als PDF übertragen
        pdf_name = Path(row["original_name"]).stem + ".pdf"
        task = client.call("POST", "documents/post_document/",
                           files={"document": (pdf_name, text_pdf(row["title"], row["text"]))}, fields=fields)
        note += (f"\nHinweis: Paperless nimmt {Path(row['original_name']).suffix}-Dateien ohne Tika/Gotenberg nicht an. "
                 "Übertragen wurde eine Textfassung als PDF; das Original liegt weiter in der Dokumentenverwaltung.")
    task_id = task if isinstance(task, str) else (task.get("task_id") if isinstance(task, dict) else None)
    if not task_id:
        raise PaperlessError("Paperless hat keine Aufgaben-ID geliefert")
    pid = _wait(client, task_id)
    client.call("POST", f"documents/{pid}/notes/", body={"note": note})
    return pid


def _task_document(task: dict) -> int | None:
    """Dokument-ID aus einer Aufgabe – Paperless 2.x und 3.x liefern sie unterschiedlich."""
    for value in (task.get("related_document"),
                  (task.get("related_document_ids") or [None])[0],
                  (task.get("result_data") or {}).get("document_id") if isinstance(task.get("result_data"), dict) else None):
        if value not in (None, ""):
            return int(value)
    return None


def _task_error(task: dict) -> str:
    for value in (task.get("result"), task.get("result_data"), task.get("result_message")):
        if value:
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return "Verarbeitung in Paperless fehlgeschlagen"


def _wait(client, task_id: str) -> int:
    deadline = time.monotonic() + TASK_TIMEOUT
    while time.monotonic() < deadline:
        tasks = client.call("GET", "tasks/", params={"task_id": task_id})
        if isinstance(tasks, dict):  # je nach Version als Liste oder paginiert
            tasks = tasks.get("results") or []
        task = tasks[0] if tasks else None
        if task:
            state = str(task.get("status", "")).lower()
            if state == "success":
                pid = _task_document(task)
                if pid is not None:
                    return pid
            elif state in ("failure", "revoked"):
                raise PaperlessError(_task_error(task))
        time.sleep(POLL_INTERVAL)
    raise PaperlessError("Paperless hat das Dokument nicht rechtzeitig verarbeitet")


def start_migration(db_path: Path, data_dir: Path, include_inbox: bool, include_documents: bool = True) -> bool:
    from .service import Store

    if _lock.locked():
        return False

    def work():
        store = Store(db.connect(db_path), data_dir)
        try:
            migrate(store, include_inbox, include_documents)
        except PaperlessError:
            pass
        finally:
            store.conn.close()

    threading.Thread(target=work, name="paperless-migration", daemon=True).start()
    return True
