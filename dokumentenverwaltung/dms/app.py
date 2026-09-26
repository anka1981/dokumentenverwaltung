"""HTTP-API und Auslieferung der Weboberfläche."""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

from . import db, gdrive
from .service import Conflict, DmsError, Store, Upload

STATIC = Path(__file__).parent / "static"


def create_app(data_dir: str | Path | None = None, password: str | None = None,
               trusted_ips: list[str] | None = None) -> Flask:
    """trusted_ips: Absender, die ohne Passwort zugreifen dürfen, weil sie selbst
    anmelden – unter Home Assistant der Ingress-Proxy (172.30.32.2). Sind sie
    gesetzt, wird jeder andere Zugriff ohne Passwort abgewiesen."""
    data_dir = Path(data_dir or os.environ.get("DMS_DATA", Path.cwd() / "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    password = password if password is not None else os.environ.get("DMS_PASSWORD", "")
    if trusted_ips is None:
        trusted_ips = [ip.strip() for ip in os.environ.get("DMS_TRUSTED_IPS", "").split(",") if ip.strip()]
    db_path = data_dir / "dms.sqlite"

    conn = db.connect(db_path)
    db.init(conn)
    conn.close()

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    app.config["DATA_DIR"] = data_dir

    def store() -> Store:
        if "store" not in g:
            g.store = Store(db.connect(db_path), data_dir)
        return g.store

    @app.teardown_appcontext
    def close(_exc):
        s = g.pop("store", None)
        if s is not None:
            s.conn.close()

    @app.before_request
    def auth():
        if request.remote_addr in trusted_ips:
            return None
        if not password:
            if trusted_ips:
                return Response("Zugriff nur über Home Assistant oder mit Passwort", 403)
            return None
        a = request.authorization
        if a and hmac.compare_digest((a.password or "").encode(), password.encode()):
            return None
        return Response("Anmeldung erforderlich", 401, {"WWW-Authenticate": 'Basic realm="Dokumente"'})

    @app.errorhandler(DmsError)
    def dms_error(exc: DmsError):
        body = {"error": str(exc)}
        if isinstance(exc, Conflict) and exc.document_id:
            body["document_id"] = exc.document_id
        return jsonify(body), exc.status

    @app.errorhandler(gdrive.DriveError)
    def drive_error(exc):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify({"error": "Datei zu groß (max. 200 MB)"}), 413

    # ------------------------------------------------------------ Oberfläche

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC, name)

    # ------------------------------------------------------------ Dokumente

    @app.get("/api/status")
    def status():
        return jsonify(store().stats())

    @app.post("/api/documents")
    def upload():
        files = [Upload(f.filename or "Datei", f.read()) for f in request.files.getlist("files")]
        if not files:
            raise DmsError("Keine Datei übermittelt")
        if request.form.get("mode") == "scan":
            return jsonify([{"document": store().ingest_scan(files, request.form.get("title"))}]), 201
        return jsonify(store().ingest_files(files)), 201

    @app.get("/api/documents")
    def list_documents():
        folder = request.args.get("folder_id", type=int)
        return jsonify(store().search(
            q=request.args.get("q", ""),
            status=request.args.get("status") or None,
            folder_id=folder,
            tag=request.args.get("tag") or None,
        ))

    @app.get("/api/documents/<int:doc_id>")
    def get_document(doc_id):
        return jsonify(store().get(doc_id))

    @app.patch("/api/documents/<int:doc_id>")
    def update_document(doc_id):
        return jsonify(store().update_document(doc_id, **_json()))

    @app.post("/api/documents/<int:doc_id>/file")
    def file_document(doc_id):
        data = _json()
        folder_id = data.pop("folder_id", None)
        if not isinstance(folder_id, int):
            raise DmsError("Bitte einen Ordner wählen")
        return jsonify(store().file_document(doc_id, folder_id, **data))

    @app.post("/api/documents/<int:doc_id>/suggest")
    def resuggest(doc_id):
        return jsonify(store().resuggest(doc_id))

    @app.delete("/api/documents/<int:doc_id>")
    def delete_document(doc_id):
        store().delete_document(doc_id)
        return "", 204

    @app.get("/api/documents/<int:doc_id>/content")
    def content(doc_id):
        path, name = store().file_path(doc_id)
        return send_file(path, download_name=name, as_attachment=request.args.get("download") == "1")

    @app.get("/api/documents/<int:doc_id>/preview")
    def preview(doc_id):
        store().get(doc_id, with_text=False)
        return send_from_directory(data_dir / "vorschau", f"{doc_id}.jpg", max_age=0)

    @app.get("/api/tags")
    def tags():
        return jsonify(store().all_tags())

    # ------------------------------------------------ Google-Drive-Import

    @app.get("/api/drive")
    def drive_status():
        return jsonify(gdrive.status(store().conn))

    @app.put("/api/drive")
    def drive_configure():
        d = _json()
        return jsonify(gdrive.configure(
            store().conn, d.get("client_id"), d.get("client_secret"),
            d.get("folder"), d.get("interval"), d.get("enabled")))

    @app.post("/api/drive/auth")
    def drive_auth():
        return jsonify({"url": gdrive.auth_url(store().conn)})

    @app.post("/api/drive/connect")
    def drive_connect():
        return jsonify(gdrive.finish_auth(store().conn, _json().get("url", "")))

    @app.post("/api/drive/disconnect")
    def drive_disconnect():
        return jsonify(gdrive.disconnect(store().conn))

    @app.post("/api/drive/run")
    def drive_run():
        return jsonify(gdrive.run_import(store()))

    # -------------------------------------------------------------- Ordner

    @app.get("/api/folders")
    def folders():
        return jsonify(store().folder_tree())

    @app.post("/api/folders")
    def create_folder():
        d = _json()
        return jsonify(store().create_folder(d.get("name"), d.get("parent_id"), d.get("keywords", ""))), 201

    @app.patch("/api/folders/<int:folder_id>")
    def update_folder(folder_id):
        d = _json()
        return jsonify(store().update_folder(
            folder_id, d.get("name"), d.get("keywords"), d["parent_id"] if "parent_id" in d else "unchanged"))

    @app.delete("/api/folders/<int:folder_id>")
    def delete_folder(folder_id):
        store().delete_folder(folder_id)
        return "", 204

    return app


def _json() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DmsError("JSON-Objekt erwartet")
    return data
