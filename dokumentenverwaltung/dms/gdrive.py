"""Automatischer Import aus einem Google-Drive-Ordner.

Ablauf: Mit der Google-Drive-App in den Ordner „Dokumente-Eingang“ scannen. Die
Dokumentenverwaltung holt neue Dateien regelmäßig ab, legt sie in den Eingang
und verschiebt sie in Drive in den Unterordner „importiert“.

Anmeldung: OAuth mit einem eigenen Google-Cloud-Client vom Typ „Desktop-App“.
Google leitet nach der Zustimmung auf http://127.0.0.1 weiter. Diese Seite lädt
nicht; man kopiert die Adresse aus der Adressleiste zurück in die App. So braucht
die App keine feste, öffentlich erreichbare Weiterleitungsadresse.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import db

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive"
REDIRECT_URI = "http://127.0.0.1:8765/"
FOLDER_MIME = "application/vnd.google-apps.folder"
DONE_FOLDER = "importiert"
DEFAULT_FOLDER = "Dokumente-Eingang"
DEFAULT_INTERVAL = 10  # Minuten
# Google-eigene Formate werden als PDF exportiert
EXPORTS = {
    "application/vnd.google-apps.document": ".pdf",
    "application/vnd.google-apps.spreadsheet": ".pdf",
    "application/vnd.google-apps.presentation": ".pdf",
}

_run_lock = threading.Lock()


class DriveError(Exception):
    pass


# --------------------------------------------------------------- Einstellungen

def get(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def put(conn: sqlite3.Connection, **values) -> None:
    for key, value in values.items():
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", ("drive_" + key,))
        else:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("drive_" + key, str(value)))
    conn.commit()


def setting(conn, key, default=None):
    return get(conn, "drive_" + key, default)


def status(conn: sqlite3.Connection) -> dict:
    last = setting(conn, "last_result")
    return {
        "client_id": setting(conn, "client_id", ""),
        "has_secret": bool(setting(conn, "client_secret")),
        "connected": bool(setting(conn, "refresh_token")),
        "enabled": setting(conn, "enabled", "1") == "1",
        "folder": setting(conn, "folder", DEFAULT_FOLDER),
        "interval": int(setting(conn, "interval", DEFAULT_INTERVAL)),
        "last_run": setting(conn, "last_run"),
        "last_result": json.loads(last) if last else None,
        "redirect_uri": REDIRECT_URI,
    }


def configure(conn, client_id=None, client_secret=None, folder=None, interval=None, enabled=None) -> dict:
    changes = {}
    if client_id is not None:
        client_id = client_id.strip()
        if client_id != setting(conn, "client_id"):
            changes["refresh_token"] = None  # anderer Client: neu verbinden
        changes["client_id"] = client_id or None
    if client_secret:
        changes["client_secret"] = client_secret.strip()
    if folder is not None:
        folder = folder.strip().strip("/")
        if not folder or "/" in folder or "'" in folder:
            raise DriveError("Ungültiger Ordnername")
        changes["folder"] = folder
    if interval is not None:
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            raise DriveError("Intervall ist keine Zahl")
        changes["interval"] = max(1, min(interval, 1440))
    if enabled is not None:
        changes["enabled"] = "1" if enabled else "0"
    put(conn, **changes)
    return status(conn)


# ------------------------------------------------------------------- HTTP

def http(method: str, url: str, *, params=None, form=None, body=None, token=None, headers=None) -> bytes:
    """Kleine HTTP-Hilfe ohne Zusatzbibliothek (in Tests ersetzt)."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdrs = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return res.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise DriveError(f"Google antwortet mit Fehler {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise DriveError(f"Google nicht erreichbar: {exc.reason}") from None


def _json(method, url, **kw) -> dict:
    return json.loads(http(method, url, **kw) or b"{}")


# ------------------------------------------------------------------ Anmeldung

def auth_url(conn: sqlite3.Connection) -> str:
    client_id = setting(conn, "client_id")
    if not client_id or not setting(conn, "client_secret"):
        raise DriveError("Bitte zuerst Client-ID und Clientschlüssel speichern")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    put(conn, verifier=verifier, state=state)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })


def finish_auth(conn: sqlite3.Connection, pasted: str) -> dict:
    """Nimmt die kopierte Adresse (…?code=…&state=…) entgegen und holt das Token."""
    pasted = (pasted or "").strip()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted).query)
    if "error" in query:
        raise DriveError("Google hat den Zugriff abgelehnt: " + query["error"][0])
    code = query.get("code", [None])[0]
    if code is None and pasted and "://" not in pasted and "=" not in pasted:
        code = pasted  # nur der Code eingefügt
    if not code:
        raise DriveError("In der eingefügten Adresse steht kein Code. Bitte die komplette Adresse kopieren.")
    state = query.get("state", [None])[0]
    if state is not None and state != setting(conn, "state"):
        raise DriveError("Die Adresse gehört zu einer älteren Anmeldung. Bitte „Mit Google verbinden“ neu starten.")
    verifier = setting(conn, "verifier")
    if not verifier:
        raise DriveError("Bitte zuerst „Mit Google verbinden“ klicken")
    tokens = _json("POST", TOKEN_URL, form={
        "code": code,
        "client_id": setting(conn, "client_id"),
        "client_secret": setting(conn, "client_secret"),
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    })
    if not tokens.get("refresh_token"):
        raise DriveError("Google hat kein dauerhaftes Token geliefert. Bitte Zugriff unter "
                         "myaccount.google.com/permissions entfernen und neu verbinden.")
    put(conn, refresh_token=tokens["refresh_token"], verifier=None, state=None)
    return status(conn)


def disconnect(conn: sqlite3.Connection) -> dict:
    put(conn, refresh_token=None, verifier=None, state=None)
    return status(conn)


def _access_token(conn) -> str:
    refresh = setting(conn, "refresh_token")
    if not refresh:
        raise DriveError("Nicht mit Google verbunden")
    try:
        tokens = _json("POST", TOKEN_URL, form={
            "client_id": setting(conn, "client_id"),
            "client_secret": setting(conn, "client_secret"),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })
    except DriveError as exc:
        if "invalid_grant" in str(exc):
            put(conn, refresh_token=None)
            raise DriveError("Die Google-Verbindung ist abgelaufen oder wurde widerrufen – bitte neu verbinden.")
        raise
    return tokens["access_token"]


# --------------------------------------------------------------------- Import

def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _folder(token: str, name: str, parent: str) -> str:
    """ID des Ordners name in parent; wird angelegt, falls er fehlt."""
    found = _json("GET", f"{API}/files", token=token, params={
        "q": f"name = '{_q(name)}' and mimeType = '{FOLDER_MIME}' and '{parent}' in parents and trashed = false",
        "fields": "files(id)",
        "pageSize": 1,
    }).get("files", [])
    if found:
        return found[0]["id"]
    return _json("POST", f"{API}/files", token=token, params={"fields": "id"},
                 body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]})["id"]


def _files(token: str, folder_id: str) -> list[dict]:
    files, page = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false and mimeType != '{FOLDER_MIME}'",
            "fields": "nextPageToken, files(id, name, mimeType)",
            "orderBy": "createdTime",
            "pageSize": 100,
        }
        if page:
            params["pageToken"] = page
        res = _json("GET", f"{API}/files", token=token, params=params)
        files += res.get("files", [])
        page = res.get("nextPageToken")
        if not page:
            return files


def _download(token: str, f: dict) -> tuple[str, bytes]:
    name = f["name"]
    if f["mimeType"] in EXPORTS:
        data = http("GET", f"{API}/files/{f['id']}/export", token=token,
                    params={"mimeType": "application/pdf"})
        return Path(name).stem + EXPORTS[f["mimeType"]] if Path(name).suffix else name + ".pdf", data
    if f["mimeType"].startswith("application/vnd.google-apps."):
        raise DriveError(f"{name}: Google-Format {f['mimeType']} kann nicht importiert werden")
    return name, http("GET", f"{API}/files/{f['id']}", token=token, params={"alt": "media"})


def run_import(store) -> dict:
    """Holt alle Dateien aus dem Drive-Ordner ab. Liefert eine Zusammenfassung."""
    from .service import Upload

    if not _run_lock.acquire(blocking=False):
        raise DriveError("Ein Abruf läuft bereits")
    conn = store.conn
    result = {"imported": 0, "duplicates": 0, "errors": [], "documents": []}
    try:
        token = _access_token(conn)
        folder_id = _folder(token, setting(conn, "folder", DEFAULT_FOLDER), "root")
        done_id = _folder(token, DONE_FOLDER, folder_id)
        for f in _files(token, folder_id):
            try:
                name, data = _download(token, f)
            except DriveError as exc:
                result["errors"].append(str(exc))
                continue
            outcome = store.ingest_files([Upload(name, data)], source="drive")[0]
            if "document" in outcome:
                result["imported"] += 1
                result["documents"].append(outcome["document"]["id"])
            elif outcome.get("document_id"):
                result["duplicates"] += 1
            else:
                # nicht unterstützter Typ o. Ä.: in Drive lassen, damit nichts verloren geht
                result["errors"].append(f"{name}: {outcome['error']}")
                continue
            http("PATCH", f"{API}/files/{f['id']}", token=token, body={},
                 params={"addParents": done_id, "removeParents": folder_id, "fields": "id"})
    except DriveError as exc:
        result["errors"].append(str(exc))
    finally:
        put(conn, last_run=datetime.now().isoformat(timespec="seconds"),
            last_result=json.dumps(result, ensure_ascii=False))
        _run_lock.release()
    return result


def start_scheduler(db_path: Path, data_dir: Path) -> threading.Thread:
    """Hintergrund-Thread: ruft den Ordner im eingestellten Intervall ab."""
    from .service import Store

    def loop():
        while True:
            time.sleep(30)
            try:
                store = Store(db.connect(db_path), data_dir)
                try:
                    st = status(store.conn)
                    if not (st["connected"] and st["enabled"]):
                        continue
                    last = st["last_run"]
                    due = not last or (datetime.now() - datetime.fromisoformat(last)).total_seconds() >= st["interval"] * 60
                    if due:
                        run_import(store)
                finally:
                    store.conn.close()
            except Exception as exc:  # der Thread darf nie sterben
                print(f"Google-Drive-Import: {exc}", flush=True)

    t = threading.Thread(target=loop, name="drive-import", daemon=True)
    t.start()
    return t
