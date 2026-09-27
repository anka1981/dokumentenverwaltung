import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fixtures import DOCTOR, INSURANCE, INVOICE, make_docx, make_odt, make_pdf, make_photo  # noqa: E402

from dms import analyze, create_app, extract, gdrive  # noqa: E402

needs_ocr = pytest.mark.skipif(not extract.ocr_available(), reason="Tesseract nicht installiert")


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, password="")
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.data_dir = tmp_path
        yield c


def upload(client, name, data, **form):
    res = client.post("/api/documents", data={"files": [(io.BytesIO(data), name)], **form},
                      content_type="multipart/form-data")
    assert res.status_code == 201, res.json
    return res.json[0]


def folder_id(client, path):
    def walk(nodes):
        for n in nodes:
            if n["path"] == path:
                return n["id"]
            found = walk(n["children"])
            if found:
                return found
    fid = walk(client.get("/api/folders").json)
    assert fid, path
    return fid


# ---------------------------------------------------------------- Extraktion

def test_extract_docx_odt_pdf(tmp_path):
    for name, data in [("a.docx", make_docx(INSURANCE)), ("a.odt", make_odt(DOCTOR)), ("a.pdf", make_pdf(INVOICE))]:
        p = tmp_path / name
        p.write_bytes(data)
        r = extract.extract(p)
        assert r.method == "text", (name, r.warnings)
        assert len(r.text) > 50
    assert "Zählerstand" in extract.extract(tmp_path / "a.pdf").text
    assert "Privathaftpflichtversicherung" in extract.extract(tmp_path / "a.docx").text


def test_extract_unsupported_and_broken(tmp_path):
    p = tmp_path / "x.xyz"
    p.write_bytes(b"abc")
    assert extract.extract(p).method == "none"
    p = tmp_path / "kaputt.docx"
    p.write_bytes(b"kein zip")
    r = extract.extract(p)
    assert r.text == "" and r.warnings


def test_extract_html_rtf_eml(tmp_path):
    (tmp_path / "a.html").write_text("<html><head><style>x{}</style></head><body><p>Hallo &amp; Tsch&uuml;ss</p></body></html>")
    assert extract.extract(tmp_path / "a.html").text == "Hallo & Tschüss"
    (tmp_path / "a.rtf").write_text(r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0 Gr\'fc\'dfe\par Zeile zwei}")
    assert extract.extract(tmp_path / "a.rtf").text == "Grüße\nZeile zwei"
    (tmp_path / "a.eml").write_text("From: bank@example.com\nSubject: Kontoauszug\n\nIhr Kontoauszug liegt bereit.")
    assert "Kontoauszug liegt bereit" in extract.extract(tmp_path / "a.eml").text


# ---------------------------------------------------------------- Analyse

def test_analyze_invoice():
    text = "\n".join(INVOICE)
    a = analyze.analyze(text, "datei", {}, 0, [])
    assert a.doc_type == "Rechnung"
    assert a.doc_date == "2026-03-14"
    assert a.amount == 1234.56
    assert a.iban == "DE89 3704 0044 0532 0130 00"
    assert a.correspondent == "Stadtwerke Musterstadt GmbH"
    assert a.title == "Rechnung Stadtwerke Musterstadt"
    assert "Zählerstand" in a.keywords


def test_analyze_dates_and_amounts():
    assert analyze.detect_date("Berlin, den 3. Mai 2025\nBezug: Schreiben vom 01.01.2024") == "2025-05-03"
    assert analyze.detect_date("nichts") is None
    assert analyze.detect_date("Kündigung zum 31.02.2025") is None  # ungültig
    assert analyze.detect_amount("Position 12,00 €\nPosition 3,50 €\nGesamtsumme 15,50 €") == 15.5
    assert analyze.detect_amount("Kosten: EUR 1.200,00 und 20,00 EUR") == 1200.0
    assert analyze.detect_iban("IBAN DE89 3704 0044 0532 0130 01") is None  # Prüfziffer falsch
    # OCR-Text: IBAN-ähnliche Zeichen über einen Zeilenumbruch dürfen nicht abstürzen
    assert analyze.detect_iban("DE12 3456\n7890 1234 5678 90") is None
    assert analyze.detect_iban("Konto:\nDE89 3704 0044 0532 0130 00\nBIC") == "DE89 3704 0044 0532 0130 00"
    assert analyze.analyze("AB12\nCDEF GHIJ KLMN\nOPQR", "x", {}, 0, []).iban is None


def test_known_correspondent_wins():
    text = "Kundenservice\nIhre Stadtwerke Musterstadt informieren"
    assert analyze.detect_correspondent(text, ["Stadtwerke Musterstadt"]) == "Stadtwerke Musterstadt"


# ------------------------------------------------------------ Gesamtablauf

def test_upload_suggest_confirm_and_file_on_disk(client):
    r = upload(client, "strom.pdf", make_pdf(INVOICE))
    doc = r["document"]
    assert doc["status"] == "eingang"
    assert doc["suggestion"][0]["path"] == "Wohnen / Energie"
    assert "Rechnung" in doc["tags"]
    assert client.get("/api/status").json["eingang"] == 1

    # Bestätigen mit leicht geändertem Titel
    res = client.post(f"/api/documents/{doc['id']}/file",
                      json={"folder_id": doc["suggestion"][0]["folder_id"], "title": "Stromabrechnung 2025",
                            "tags": doc["tags"] + ["Strom"]})
    assert res.status_code == 200, res.json
    filed = res.json
    assert filed["status"] == "abgelegt"
    assert filed["file_path"] == "Wohnen/Energie/2026-03-14_Stromabrechnung 2025.pdf"
    assert (client.data_dir / "ablage" / filed["file_path"]).is_file()
    assert "Strom" in filed["tags"]
    st = client.get("/api/status").json
    assert (st["eingang"], st["abgelegt"], st["version"]) == (0, 1, "dev")
    assert client.get(f"/api/documents/{doc['id']}/content").data[:5] == b"%PDF-"


def test_manual_correction_is_learned(client):
    # Eine Versicherungspolice wird bewusst unter "Verträge & Abos" abgelegt …
    first = upload(client, "police1.docx", make_docx(INSURANCE))["document"]
    assert first["suggestion"][0]["path"] == "Versicherungen"
    target = folder_id(client, "Verträge & Abos")
    client.post(f"/api/documents/{first['id']}/file", json={"folder_id": target})

    # … die nächste Police desselben Absenders wird dann dort vorgeschlagen.
    second_text = [l.replace("998877", "112233").replace("02.01.2026", "05.01.2027") for l in INSURANCE]
    second = upload(client, "police2.docx", make_docx(second_text + ["Nachtrag"]))["document"]
    assert second["correspondent"] == "Musterversicherung AG"
    top = second["suggestion"][0]
    assert top["path"] == "Verträge & Abos", second["suggestion"]
    assert any("Musterversicherung" in r for r in top["reasons"])


def test_duplicate_rejected(client):
    data = make_docx(DOCTOR)
    first = upload(client, "arzt.docx", data)["document"]
    again = upload(client, "arzt-kopie.docx", data)
    assert again["document_id"] == first["id"]
    assert "Bereits vorhanden" in again["error"]


def test_unsupported_file(client):
    r = upload(client, "programm.exe", b"MZ")
    assert "nicht unterstützt" in r["error"]


def test_search_filters(client):
    a = upload(client, "strom.pdf", make_pdf(INVOICE))["document"]
    b = upload(client, "arzt.odt", make_odt(DOCTOR))["document"]
    gesundheit = folder_id(client, "Gesundheit")
    client.post(f"/api/documents/{b['id']}/file", json={"folder_id": gesundheit})

    hits = client.get("/api/documents?q=zählerst").json  # Präfixsuche, Umlaut
    assert [d["id"] for d in hits] == [a["id"]]
    assert "\x01" in hits[0]["snippet"]
    assert [d["id"] for d in client.get("/api/documents?q=physiotherapie").json] == [b["id"]]
    assert [d["id"] for d in client.get(f"/api/documents?folder_id={gesundheit}").json] == [b["id"]]
    assert [d["id"] for d in client.get("/api/documents?status=eingang").json] == [a["id"]]
    assert [d["id"] for d in client.get("/api/documents?tag=Rechnung").json] == [a["id"]]
    assert client.get('/api/documents?q=" OR (').status_code == 200  # FTS-Syntax wird entschärft


def test_rename_folder_moves_files(client):
    doc = upload(client, "arzt.odt", make_odt(DOCTOR))["document"]
    fid = folder_id(client, "Gesundheit")
    client.post(f"/api/documents/{doc['id']}/file", json={"folder_id": fid, "title": "Befund Knie"})
    parent = client.post("/api/folders", json={"name": "Familie"}).json
    res = client.patch(f"/api/folders/{fid}", json={"name": "Arzt", "parent_id": parent["id"]})
    assert res.status_code == 200, res.json
    d = client.get(f"/api/documents/{doc['id']}").json
    assert d["folder_path"] == "Familie / Arzt"
    assert d["file_path"] == "Familie/Arzt/2026-02-21_Befund Knie.pdf".replace(".pdf", ".odt")
    assert (client.data_dir / "ablage" / d["file_path"]).is_file()
    assert not (client.data_dir / "ablage" / "Gesundheit").exists()

    # Ordner mit Dokumenten lässt sich nicht löschen, Zyklen sind verboten
    assert client.delete(f"/api/folders/{fid}").status_code == 400
    assert client.patch(f"/api/folders/{parent['id']}", json={"parent_id": fid}).status_code == 400


def test_folder_keywords_drive_suggestion(client):
    fid = client.post("/api/folders", json={"name": "Hobby", "keywords": "Modelleisenbahn, Märklin"}).json["id"]
    doc = upload(client, "hobby.txt", "Katalog Märklin Modelleisenbahn Neuheiten 2026".encode())["document"]
    assert doc["suggestion"][0]["folder_id"] == fid
    assert client.post("/api/folders", json={"name": "hobby"}).status_code == 409


def test_update_and_delete(client):
    doc = upload(client, "arzt.odt", make_odt(DOCTOR))["document"]
    fid = folder_id(client, "Gesundheit")
    client.post(f"/api/documents/{doc['id']}/file", json={"folder_id": fid})
    res = client.patch(f"/api/documents/{doc['id']}", json={"doc_date": "2026-02-22", "amount": "12,50"})
    assert res.json["amount"] == 12.5
    assert res.json["file_path"].startswith("Gesundheit/2026-02-22_")
    assert client.patch(f"/api/documents/{doc['id']}", json={"doc_date": "22.02."}).status_code == 400
    path = client.data_dir / "ablage" / res.json["file_path"]
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert not path.exists()
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404


def test_password(tmp_path):
    app = create_app(tmp_path, password="geheim")
    with app.test_client() as c:
        assert c.get("/api/status").status_code == 401
        import base64
        auth = base64.b64encode(b"x:geheim").decode()
        assert c.get("/api/status", headers={"Authorization": "Basic " + auth}).status_code == 200


@needs_ocr
def test_phone_scan_multi_page_rotated(client):
    pages = [make_photo(INVOICE, rotate=90), make_photo(["Seite 2", "Allgemeine Geschäftsbedingungen der Stadtwerke"])]
    res = client.post("/api/documents", data={
        "mode": "scan",
        "files": [(io.BytesIO(p), f"seite{i}.jpg") for i, p in enumerate(pages)],
    }, content_type="multipart/form-data")
    assert res.status_code == 201, res.json
    doc = res.json[0]["document"]
    assert doc["source"] == "scan"
    assert doc["pages"] == 2
    assert doc["original_name"].endswith(".pdf")
    assert doc["extract_method"] == "ocr"
    assert doc["doc_date"] == "2026-03-14"
    assert doc["amount"] == 1234.56
    assert doc["suggestion"][0]["path"] == "Wohnen / Energie"
    assert doc["has_preview"]
    assert client.get(f"/api/documents/{doc['id']}/preview").status_code == 200


@needs_ocr
def test_scanned_pdf_without_text_layer_gets_ocr(tmp_path):
    from PIL import Image
    img = Image.open(io.BytesIO(make_photo(DOCTOR)))
    extract.images_to_pdf([img], tmp_path / "scan.pdf")
    r = extract.extract(tmp_path / "scan.pdf")
    assert r.method == "ocr"
    assert "Physiotherapie" in r.text


def test_home_assistant_ingress_only(tmp_path):
    app = create_app(tmp_path, password="", trusted_ips=["172.30.32.2"])
    with app.test_client() as c:
        assert c.get("/api/status", environ_base={"REMOTE_ADDR": "172.30.32.2"}).status_code == 200
        assert c.get("/api/status", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    app = create_app(tmp_path, password="geheim", trusted_ips=["172.30.32.2"])
    with app.test_client() as c:
        import base64
        auth = {"Authorization": "Basic " + base64.b64encode(b"x:geheim").decode()}
        assert c.get("/api/status", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 401
        assert c.get("/api/status", headers=auth, environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200


def test_ui_uses_relative_urls():
    # Unter Home Assistant läuft die Oberfläche unter /api/hassio_ingress/<token>/
    static = Path(__file__).parent.parent / "dms" / "static"
    for name in ("index.html", "app.js"):
        src = (static / name).read_text()
        for bad in ('"/api', "'/api", "`/api", '"/static'):
            assert bad not in src, (name, bad)


# ------------------------------------------------------ Google-Drive-Import

class FakeGoogle:
    """Simuliert OAuth und die Drive-API für gdrive.http."""

    def __init__(self, files):
        self.files = {f["id"]: dict(f, parents=["inbox"]) for f in files}
        self.folders = {"inbox": ("Dokumente-Eingang", "root")}
        self.token_requests = []

    def __call__(self, method, url, *, params=None, form=None, body=None, token=None, headers=None):
        import json as _json
        params = params or {}
        if url == gdrive.TOKEN_URL:
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                assert form["code"] == "abc" and form["code_verifier"]
                return _json.dumps({"refresh_token": "r1", "access_token": "a1"}).encode()
            return _json.dumps({"access_token": "a2"}).encode()
        assert token == "a2"
        if method == "GET" and url.endswith("/files") and "name =" in params.get("q", ""):
            name = params["q"].split("'")[1]
            parent = params["q"].split("'")[5]
            hits = [{"id": i} for i, (n, p) in self.folders.items() if n == name and p == parent]
            return _json.dumps({"files": hits}).encode()
        if method == "POST" and url.endswith("/files"):
            fid = "f" + str(len(self.folders))
            self.folders[fid] = (body["name"], body["parents"][0])
            return _json.dumps({"id": fid}).encode()
        if method == "GET" and url.endswith("/files"):
            folder = params["q"].split("'")[1]
            hits = [{k: f[k] for k in ("id", "name", "mimeType")} for f in self.files.values() if folder in f["parents"]]
            return _json.dumps({"files": hits}).encode()
        fid = url.split("/files/")[1].split("/")[0]
        if url.endswith("/export"):
            return self.files[fid]["data"]
        if method == "GET":
            return self.files[fid]["data"]
        if method == "PATCH":
            f = self.files[fid]
            f["parents"] = [params["addParents"] if p == params["removeParents"] else p for p in f["parents"]]
            return b"{}"
        raise AssertionError((method, url, params))


def test_drive_connect_and_import(client, monkeypatch):
    fake = FakeGoogle([
        {"id": "1", "name": "Scan Stromrechnung.pdf", "mimeType": "application/pdf", "data": make_pdf(INVOICE)},
        {"id": "2", "name": "Arztbrief", "mimeType": "application/vnd.google-apps.document", "data": make_pdf(DOCTOR)},
        {"id": "3", "name": "programm.exe", "mimeType": "application/octet-stream", "data": b"MZ"},
    ])
    monkeypatch.setattr(gdrive, "http", fake)

    assert client.post("/api/drive/auth").status_code == 400  # erst Zugangsdaten
    client.put("/api/drive", json={"client_id": "cid.apps.googleusercontent.com", "client_secret": "geheim"})
    url = client.post("/api/drive/auth").json["url"]
    assert "code_challenge=" in url and "access_type=offline" in url
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(url).query)["state"][0]

    bad = client.post("/api/drive/connect", json={"url": "http://127.0.0.1:8765/?state=alt&code=abc"})
    assert bad.status_code == 400 and "älteren Anmeldung" in bad.json["error"]
    ok = client.post("/api/drive/connect", json={"url": f"http://127.0.0.1:8765/?state={state}&code=abc&scope=x"})
    assert ok.status_code == 200 and ok.json["connected"]
    assert "geheim" not in str(client.get("/api/drive").json)  # Schlüssel wird nie ausgeliefert

    r = client.post("/api/drive/run?wait=1").json
    assert r["imported"] == 2, r
    assert len(r["errors"]) == 1 and "programm.exe" in r["errors"][0]
    inbox = client.get("/api/documents?status=eingang").json
    assert sorted(d["source"] for d in inbox) == ["drive", "drive"]
    assert "Arztbrief.pdf" in [d["original_name"] for d in inbox]
    done = [i for i, (n, _) in fake.folders.items() if n == "importiert"][0]
    assert fake.files["1"]["parents"] == [done] and fake.files["2"]["parents"] == [done]
    assert fake.files["3"]["parents"] == ["inbox"]  # nicht unterstützt: bleibt liegen

    # Zweiter Abruf: nichts Neues
    r = client.post("/api/drive/run?wait=1").json
    assert r["imported"] == 0
    assert client.get("/api/drive").json["last_result"]["imported"] == 0


def test_drive_revoked_access(client, monkeypatch):
    def revoked(method, url, **kw):
        raise gdrive.DriveError('Google antwortet mit Fehler 400: {"error": "invalid_grant"}')
    monkeypatch.setattr(gdrive, "http", revoked)
    from dms import db
    conn = db.connect(client.data_dir / "dms.sqlite")
    gdrive.put(conn, client_id="c", client_secret="s", refresh_token="r")
    conn.close()
    r = client.post("/api/drive/run?wait=1").json
    assert "neu verbinden" in r["errors"][0]
    assert client.get("/api/drive").json["connected"] is False


def test_drive_import_survives_broken_file(client, monkeypatch):
    fake = FakeGoogle([
        {"id": "1", "name": "gut.pdf", "mimeType": "application/pdf", "data": make_pdf(INVOICE)},
        {"id": "2", "name": "kaputt.pdf", "mimeType": "application/pdf", "data": make_pdf(DOCTOR)},
    ])
    monkeypatch.setattr(gdrive, "http", fake)
    from dms import db, service
    conn = db.connect(client.data_dir / "dms.sqlite")
    gdrive.put(conn, client_id="c", client_secret="s", refresh_token="r")
    conn.close()
    original = service.Store._ingest_one

    def flaky(self, filename, data, source):
        if filename == "kaputt.pdf":
            raise RuntimeError("Tesseract abgestürzt")
        return original(self, filename, data, source)
    monkeypatch.setattr(service.Store, "_ingest_one", flaky)

    res = client.post("/api/drive/run?wait=1")
    assert res.status_code == 200
    assert res.json["imported"] == 1
    assert "kaputt.pdf: Tesseract abgestürzt" in res.json["errors"]
    assert fake.files["2"]["parents"] == ["inbox"]  # bleibt für den nächsten Versuch liegen


def test_unexpected_error_is_json(client, monkeypatch):
    from dms import service
    monkeypatch.setattr(service.Store, "stats", lambda self: 1 / 0)
    res = client.get("/api/status")
    assert res.status_code == 500
    assert "ZeroDivisionError" in res.json["error"]
    assert client.get("/api/gibtsnicht").status_code == 404


def test_index_busts_cache(client):
    res = client.get("/")
    assert "no-store" in res.headers["Cache-Control"]
    html = res.get_data(as_text=True)
    import re
    v = re.search(r'static/app\.js\?v=([0-9a-f]+)"', html).group(1)
    assert f'static/style.css?v={v}"' in html
    assert "immutable" in client.get(f"/static/app.js?v={v}").headers["Cache-Control"]
    assert client.get("/static/app.js").headers["Cache-Control"] == "no-cache"
    assert client.get("/api/status").headers["Cache-Control"] == "no-store"


def test_drive_run_in_background(client, monkeypatch):
    import time
    fake = FakeGoogle([{"id": "1", "name": "a.pdf", "mimeType": "application/pdf", "data": make_pdf(INVOICE)}])
    monkeypatch.setattr(gdrive, "http", fake)
    from dms import db
    conn = db.connect(client.data_dir / "dms.sqlite")
    gdrive.put(conn, client_id="c", client_secret="s", refresh_token="r")
    conn.close()
    res = client.post("/api/drive/run")
    assert res.status_code == 202 and res.json["started"]
    for _ in range(100):
        st = client.get("/api/drive").json
        if not st["running"] and st["last_run"]:
            break
        time.sleep(0.1)
    assert st["last_result"]["imported"] == 1


def test_clientlog(client, caplog):
    assert client.post("/api/clientlog", json={"msg": "Kamera: secure=false"}).status_code == 204
    assert "Kamera: secure=false" in caplog.text


# ------------------------------------------------ verlustfreie Kompression

from dms import compress  # noqa: E402

needs_jpegtran = pytest.mark.skipif(not compress.available(), reason="jpegtran nicht installiert")


def _decoded(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        return img.mode, img.size, img.tobytes()


@needs_jpegtran
def test_jpeg_lossless():
    data = make_photo(INVOICE)
    out = compress.jpeg_lossless(data)
    assert len(out) < len(data)
    assert _decoded(out) == _decoded(data)


def test_png_and_tiff_lossless():
    from PIL import Image
    img = Image.open(io.BytesIO(make_photo(DOCTOR))).convert("RGB")
    raw_png = io.BytesIO(); img.save(raw_png, "PNG", compress_level=0)
    out = compress.png_lossless(raw_png.getvalue())
    assert len(out) < len(raw_png.getvalue()) and _decoded(out) == _decoded(raw_png.getvalue())
    raw_tif = io.BytesIO(); img.save(raw_tif, "TIFF", save_all=True, append_images=[img.rotate(90, expand=True)])
    out = compress.tiff_lossless(raw_tif.getvalue())
    assert len(out) < len(raw_tif.getvalue())
    assert compress._frames(out) == compress._frames(raw_tif.getvalue())


def test_zip_lossless_keeps_contents():
    import zipfile
    for data in (make_docx(INSURANCE * 20), make_odt(DOCTOR * 20)):
        out = compress.zip_lossless(data)
        assert len(out) < len(data)
        a, b = zipfile.ZipFile(io.BytesIO(data)), zipfile.ZipFile(io.BytesIO(out))
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]
        assert all(a.read(n) == b.read(n) for n in a.namelist())
    odt = zipfile.ZipFile(io.BytesIO(compress.zip_lossless(make_odt(DOCTOR * 20))))
    first = odt.infolist()[0]
    assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED


@needs_jpegtran
def test_pdf_lossless_scan_and_text(tmp_path):
    from PIL import Image
    imgs = [Image.open(io.BytesIO(make_photo(INVOICE))), Image.open(io.BytesIO(make_photo(DOCTOR)))]
    extract.images_to_pdf(imgs, tmp_path / "scan.pdf")
    scan = (tmp_path / "scan.pdf").read_bytes()
    for data in (scan, make_pdf(INVOICE * 3)):
        out = compress.pdf_lossless(data)
        assert len(out) < len(data)
        assert compress._pdf_fingerprint(out) == compress._pdf_fingerprint(data)


def test_broken_file_is_left_alone(tmp_path):
    p = tmp_path / "kaputt.pdf"
    p.write_bytes(b"%PDF-1.4 kaputt")
    assert compress.optimize_file(p, ".pdf") is None
    assert p.read_bytes() == b"%PDF-1.4 kaputt"


@needs_jpegtran
def test_import_optimizes_and_keeps_duplicate_check(client):
    data = make_docx(INSURANCE * 30)
    doc = upload(client, "police.docx", data)["document"]
    assert doc["stored_size"] < doc["size"] == len(data)
    stored = client.get(f"/api/documents/{doc['id']}/content").data
    import zipfile
    a, b = zipfile.ZipFile(io.BytesIO(data)), zipfile.ZipFile(io.BytesIO(stored))
    assert all(a.read(n) == b.read(n) for n in a.namelist())
    again = upload(client, "nochmal.docx", data)  # gleiche Originaldatei → Duplikat
    assert again["document_id"] == doc["id"]
    st = client.get("/api/status").json
    assert st["bytes_stored"] < st["bytes_original"] and st["optimize_pending"] == 0


def test_optimize_existing_documents(client):
    from dms import db, service
    doc = upload(client, "police.docx", make_docx(INSURANCE * 30))["document"]
    conn = db.connect(client.data_dir / "dms.sqlite")
    conn.execute("UPDATE documents SET stored_size = NULL")
    conn.commit()
    service.optimize_existing(client.data_dir / "dms.sqlite", client.data_dir).join(timeout=60)
    assert conn.execute("SELECT stored_size FROM documents WHERE id = ?", (doc["id"],)).fetchone()[0] is not None
    conn.close()


def test_entry_prefix_for_home_assistant(client):
    """ingress_entry: app/ – Seite, Skript und API funktionieren auch unter /app/."""
    import re
    html = client.get("/app/").get_data(as_text=True)
    v = re.search(r'static/app\.js\?v=([0-9a-f]+)"', html).group(1)
    assert client.get(f"/app/static/app.js?v={v}").status_code == 200
    assert client.get("/app/api/status").json["eingang"] == 0
    assert client.get("/app").status_code == 301
    assert client.get("/api/status").status_code == 200  # ohne Präfix weiterhin


# ------------------------------------------------ Übernahme nach Paperless

from dms import paperless  # noqa: E402


class FakePaperless:
    def __init__(self, version="2.14.7"):
        self.version = version
        self.v3 = version.startswith("3")
        self.objects = {k: {} for k in ("storage_paths", "document_types", "correspondents", "tags")}
        self.documents, self.tasks, self.notes, self.bulk = {}, {}, {}, []
        self.hashes = set()

    def __call__(self, method, url, *, token, params=None, body=None, files=None, fields=None):
        import hashlib as _h, json as _json
        assert token == "tok-geheim-123"
        path = url.split("/api/", 1)[1]
        hdr = {"X-Version": self.version}
        out = lambda obj: (200, hdr, _json.dumps(obj).encode())
        kind = path.rstrip("/").split("/")[0]
        if method == "GET" and path == "documents/":
            return out({"count": len(self.documents), "results": []})
        if kind in self.objects and method == "GET":
            name = params["name__iexact"].lower()
            return out({"results": [o for o in self.objects[kind].values() if o["name"].lower() == name]})
        if kind in self.objects and method == "POST":
            oid = len(self.objects[kind]) + 1
            self.objects[kind][oid] = {"id": oid, **body}
            return out(self.objects[kind][oid])
        if path == "documents/post_document/":
            if files["document"][0].endswith(".odt") and self.v3:
                raise paperless.PaperlessError('Paperless antwortet mit Fehler 400: {"document":["File type '
                                               'application/vnd.oasis.opendocument.text not supported"]}')
            data = files["document"][1]
            tid = f"task-{len(self.tasks) + 1}"
            h = _h.md5(data).hexdigest()
            if h in self.hashes:
                self.tasks[tid] = ({"status": "failure", "result_data": {"error": "It is a duplicate of y"}}
                                   if self.v3 else {"status": "FAILURE", "result": "Not consuming x: It is a duplicate of y"})
            else:
                self.hashes.add(h)
                did = len(self.documents) + 1
                f = {}
                for k, v in fields:
                    f.setdefault(k, []).append(v)
                self.documents[did] = {"name": files["document"][0], **f}
                self.tasks[tid] = ({"status": "success", "related_document_ids": [did], "result_data": {"document_id": did}}
                                   if self.v3 else {"status": "SUCCESS", "related_document": str(did)})
            return out(tid)
        if path == "tasks/":
            task = self.tasks[params["task_id"]]
            return out({"count": 1, "results": [task]} if self.v3 else [task])
        if path.endswith("/notes/"):
            self.notes[int(path.split("/")[1])] = body["note"]
            return out([])
        if path == "documents/bulk_edit/":
            self.bulk.append(body)
            return out({"result": "OK"})
        raise AssertionError((method, path))


def test_keyword_regex_matches_compounds_and_umlauts():
    import re
    rx = paperless.keyword_regex(["versicherung", "zählerstand", "kfz-steuer", "steuer id"])
    for text in ("Haftpflichtversicherung", "Zaehlerstand", "KFZ-Steuer", "Kfz Steuer", "Steuer ID"):
        assert re.search(rx, text, re.I), text
    assert not re.search(rx, "Rechnung", re.I)
    assert paperless.path_template("Wohnen / Energie", True) == \
        "Wohnen/Energie/{{ created_year }}-{{ created_month }}-{{ created_day }}_{{ title }}"
    assert "{created_year}" in paperless.path_template("Wohnen / Energie", paperless.uses_jinja("2.8.1"))


@pytest.mark.parametrize("version", ["2.14.7", "3.2.1"])
def test_migrate_to_paperless(client, monkeypatch, version):
    fake = FakePaperless(version)
    monkeypatch.setattr(paperless, "http", fake)
    monkeypatch.setattr(paperless, "POLL_INTERVAL", 0)
    filed = upload(client, "strom.pdf", make_pdf(INVOICE))["document"]
    client.post(f"/api/documents/{filed['id']}/file", json={
        "folder_id": filed["suggestion"][0]["folder_id"], "notes": "Zählernummer 42", "tags": ["Strom", "Rechnung"]})
    inbox = upload(client, "arzt.odt", make_odt(DOCTOR))["document"]

    assert client.post("/api/paperless/test").status_code == 400  # noch keine Zugangsdaten
    assert client.put("/api/paperless", json={"url": "paperless:8000"}).status_code == 400
    client.put("/api/paperless", json={"url": "http://192.168.1.5:8000/api/", "token": "tok-geheim-123"})
    st = client.get("/api/paperless").json
    assert st["url"] == "http://192.168.1.5:8000" and st["has_token"] and "geheim" not in str(st)
    assert client.post("/api/paperless/test").json["version"] == version

    res = client.post("/api/paperless/migrate?wait=1", json={"include_inbox": True}).json
    assert res["phase"] == "Fertig" and res["done"] == 2 and not res["errors"], res

    sp = {o["name"]: o for o in fake.objects["storage_paths"].values()}
    assert sp["Wohnen / Energie"]["path"].startswith("Wohnen/Energie/{{ created_year }}")
    assert sp["Wohnen / Energie"]["matching_algorithm"] == paperless.MATCH_REGEX
    assert sp["Sonstiges"]["matching_algorithm"] == paperless.MATCH_NONE
    doc_filed = next(d for d in fake.documents.values() if d["name"] == "strom.pdf")
    energie_id = sp["Wohnen / Energie"]["id"]
    assert doc_filed["storage_path"] == [energie_id]
    assert doc_filed["title"] == [filed["title"]] and doc_filed["created"] == ["2026-03-14"]
    tag_ids = {o["name"]: o["id"] for o in fake.objects["tags"].values()}
    assert set(doc_filed["tags"]) >= {tag_ids["Strom"], tag_ids["Rechnung"]}
    odt_name = "arzt.pdf" if fake.v3 else "arzt.odt"  # 3.x-Fake ohne Tika: Textfassung als PDF
    doc_inbox = next(d for d in fake.documents.values() if d["name"] == odt_name)
    if fake.v3:
        assert "Textfassung" in fake.notes[2]
    assert "storage_path" not in doc_inbox  # unbestätigt: Paperless schlägt per Regel vor
    note = fake.notes[1]
    assert "Zählernummer 42" in note and "Betrag: 1.234,56 €" in note and "IBAN: DE89" in note
    inbox_tag = next(o for o in fake.objects["tags"].values() if o["name"] == "Posteingang")
    assert inbox_tag["is_inbox_tag"] and fake.bulk[0]["documents"] == [2]

    # Erneuter Lauf: nichts doppelt
    again = client.post("/api/paperless/migrate?wait=1", json={}).json
    assert again["total"] == 0 and len(fake.documents) == 2
    assert client.get("/api/paperless").json["transferred"] == 2


@pytest.mark.parametrize("version", ["2.8.0", "3.2.1"])
def test_migrate_reports_duplicates_and_errors(client, monkeypatch, version):
    fake = FakePaperless(version=version)
    monkeypatch.setattr(paperless, "http", fake)
    monkeypatch.setattr(paperless, "POLL_INTERVAL", 0)
    doc = upload(client, "arzt.pdf", make_pdf(DOCTOR))["document"]
    fake.hashes.add(__import__("hashlib").md5((client.data_dir / "ablage" / doc["file_path"]).read_bytes()).hexdigest())
    client.put("/api/paperless", json={"url": "http://p:8000", "token": "tok-geheim-123"})
    res = client.post("/api/paperless/migrate?wait=1", json={}).json
    assert res["skipped"] == 1 and res["done"] == 0 and not res["errors"]
    template = next(iter(fake.objects["storage_paths"].values()))["path"]
    assert ("{created_year}" in template) == (version == "2.8.0")

    def down(*a, **kw):
        raise paperless.PaperlessError("Paperless nicht erreichbar (Connection refused). Stimmt die Adresse mit Port?")
    monkeypatch.setattr(paperless, "http", down)
    res = client.post("/api/paperless/migrate?wait=1", json={}).json
    assert res["phase"] == "Abgebrochen" and "nicht erreichbar" in res["errors"][0]


# ------------------------------------------------ Ordnerstruktur aus Liste

OUTLINE = """
* Rechnungen
* Wohnung
   * Musterstraße 12
   * Am Beispielweg 3
* Garten
* Pflanzen
* Arbeit
   * Beispiel GmbH
      * Gehalt
      * Verträge
* Versicherung
   * Krankenversicherung
      * AU
   * Rente
   * Hausrat
* Pflanzen
   * Pflanzenpass
* Persönliche Dokumente
* Erbschaft
   * Vater
   * Tante Erna
"""


def test_parse_outline_merges_duplicates():
    from dms import structure
    tree = structure.parse_outline(OUTLINE)
    names = [n["name"] for n in tree]
    assert names.count("Pflanzen") == 1
    pflanzen = next(n for n in tree if n["name"] == "Pflanzen")
    assert [c["name"] for c in pflanzen["children"]] == ["Pflanzenpass"]
    paths = structure.flatten(tree)
    assert ("Arbeit", "Beispiel GmbH", "Gehalt") in paths
    assert ("Versicherung", "Krankenversicherung", "AU") in paths
    assert structure.parse_outline("\tA\n\t\tB\n- C\n1. D")[0]["children"][0]["name"] == "B"
    assert "krankenkasse" in structure.suggest_keywords("Krankenversicherung")
    assert "arbeitsvertrag" in structure.suggest_keywords("Verträge")
    assert structure.suggest_keywords("Musterstraße 12") == "musterstraße 12"
    assert structure.suggest_keywords("Vater") == ""
    with pytest.raises(structure.OutlineError):
        structure.parse_outline("* A/B")


def test_apply_structure(client):
    # Ein Dokument in einem Standardordner, der nicht in der Liste steht
    doc = upload(client, "arzt.odt", make_odt(DOCTOR))["document"]
    client.post(f"/api/documents/{doc['id']}/file", json={"folder_id": folder_id(client, "Gesundheit")})
    before = client.get("/api/folders").json

    preview = client.post("/api/folders/structure", json={"text": OUTLINE, "remove_empty": True}).json
    assert preview["dry_run"] and client.get("/api/folders").json == before  # Vorschau ändert nichts
    created = {c["path"]: c["keywords"] for c in preview["create"]}
    assert "Versicherung / Krankenversicherung / AU" in created
    assert "Arbeit / Beispiel GmbH / Gehalt" in created
    assert "Arbeit" in preview["exists"]  # Standardordner gleichen Namens bleibt
    assert {"path": "Gesundheit", "documents": 1} in preview["keep_nonempty"]
    assert "Fahrzeuge" in preview["remove"] and "Arbeit / Gehaltsabrechnungen" in preview["remove"]

    res = client.post("/api/folders/structure", json={"text": OUTLINE, "remove_empty": True, "dry_run": False}).json
    assert not res["dry_run"]
    tree = client.get("/api/folders").json
    top = sorted(n["name"] for n in tree)
    assert top == sorted(["Rechnungen", "Wohnung", "Garten", "Pflanzen", "Arbeit", "Versicherung",
                          "Persönliche Dokumente", "Erbschaft", "Gesundheit"])
    au = folder_id(client, "Versicherung / Krankenversicherung / AU")
    assert client.get(f"/api/documents/{doc['id']}").json["folder_path"] == "Gesundheit"

    # Neue Ordner werden vorgeschlagen
    au_doc = upload(client, "au.txt", "Arbeitsunfähigkeitsbescheinigung zur Vorlage beim Arbeitgeber".encode())["document"]
    assert au_doc["suggestion"][0]["folder_id"] == au

    again = client.post("/api/folders/structure", json={"text": OUTLINE, "dry_run": False}).json
    assert again["create"] == []  # nichts doppelt


def test_text_pdf_is_readable():
    import pypdf
    long = "\n".join(f"Zeile {i}: Größe, Maße & (Klammern) äöüß" for i in range(150))
    pdf = paperless.text_pdf("Arztbrief", long)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 3
    text = "".join(p.extract_text() for p in reader.pages)
    assert "Arztbrief" in text and "Zeile 149" in text and "(Klammern) äöüß" in text
