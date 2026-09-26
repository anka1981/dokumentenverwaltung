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
    assert client.get("/api/status").json == {"eingang": 0, "abgelegt": 1, "ocr": extract.ocr_available()}
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

    r = client.post("/api/drive/run").json
    assert r["imported"] == 2, r
    assert len(r["errors"]) == 1 and "programm.exe" in r["errors"][0]
    inbox = client.get("/api/documents?status=eingang").json
    assert sorted(d["source"] for d in inbox) == ["drive", "drive"]
    assert "Arztbrief.pdf" in [d["original_name"] for d in inbox]
    done = [i for i, (n, _) in fake.folders.items() if n == "importiert"][0]
    assert fake.files["1"]["parents"] == [done] and fake.files["2"]["parents"] == [done]
    assert fake.files["3"]["parents"] == ["inbox"]  # nicht unterstützt: bleibt liegen

    # Zweiter Abruf: nichts Neues
    r = client.post("/api/drive/run").json
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
    r = client.post("/api/drive/run").json
    assert "neu verbinden" in r["errors"][0]
    assert client.get("/api/drive").json["connected"] is False
