# Dokumentenverwaltung für Home Assistant

Home-Assistant-Add-on zur Verwaltung privater Unterlagen:

- Einspielen per **Datei** (PDF, Word, LibreOffice, Excel, Bilder …) oder per **Scan mit der Google-Drive-App** (automatischer Import)
- **Kostenlose Texterkennung** mit Tesseract, lokal auf dem Raspberry Pi
- **Verschlagwortung**: Schlagwörter, Dokumentart, Datum, Betrag, IBAN, Absender
- **Ablagevorschlag** mit Sicherheit und Begründung. Abgelegt wird erst nach Bestätigung oder
  manueller Änderung, und die Vorschläge lernen aus jeder Entscheidung.
- Volltextsuche, eigene Ordnerstruktur, Datenbank (SQLite) und Dateien auf dem eigenen Gerät

## Installation in Home Assistant

1. **Einstellungen → Add-ons → Add-on-Store**, oben rechts ⋮ → **Repositories**
2. `https://github.com/anka1981/dokumentenverwaltung` hinzufügen
3. **Dokumentenverwaltung** auswählen → **Installieren**. Der erste Build auf dem Raspberry Pi
   dauert einige Minuten.
4. **Starten** und **In Seitenleiste anzeigen** aktivieren

Voraussetzung: Home Assistant OS oder Supervised auf einem 64-Bit-System (Raspberry Pi 4/5
mit 64-Bit-Image, oder x86). Details zu Optionen, Speicherort und Backup nach Google Drive: [DOCS.md](dokumentenverwaltung/DOCS.md).

## Ohne Home Assistant

Die App läuft auch eigenständig auf jedem Rechner mit Python:

```bash
sudo apt install tesseract-ocr tesseract-ocr-deu
cd dokumentenverwaltung
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DMS_PASSWORD=geheim python run.py --host 0.0.0.0     # http://<rechner>:8080
```

Datenbank und Dateien liegen dann in `data/` (änderbar mit `--data`).

## Entwicklung

| Datei | Aufgabe |
|---|---|
| `dms/extract.py` | Textauslesen aus allen Dateitypen, OCR, Bildaufbereitung, Scan → PDF, Vorschau |
| `dms/analyze.py` | Schlagwörter, Dokumentart, Datum, Betrag, IBAN, Absender, Titel |
| `dms/classify.py` | Ordnervorschlag: Begriffsregeln, Ähnlichkeit zu Abgelegtem, Absender |
| `dms/service.py` | Import, Ablegen, Verschieben der Dateien, Suche, Ordnerverwaltung |
| `dms/db.py` | SQLite-Schema (mit FTS5-Volltextindex) und Grundstruktur der Ordner |
| `dms/app.py` | HTTP-API (Flask) |
| `dms/static/` | Weboberfläche (ohne Build-Schritt) |

```bash
pip install -r dokumentenverwaltung/requirements-dev.txt
pytest dokumentenverwaltung/tests
```

Die Tests erzeugen ihre Beispieldokumente selbst. Die OCR-Tests werden übersprungen, wenn
Tesseract fehlt.
