"""SQLite-Datenbank: Schema, Verbindung und Grundstruktur der Ablage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS folders (
    id          INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
    name        TEXT NOT NULL,
    -- Kommagetrennte Begriffe, die auf diesen Ordner hinweisen
    keywords    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS folders_unique_name
    ON folders(COALESCE(parent_id, 0), name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL,
    original_name   TEXT NOT NULL,
    file_path       TEXT NOT NULL,           -- relativ zum Ablageverzeichnis
    sha256          TEXT NOT NULL UNIQUE,
    size            INTEGER NOT NULL,
    source          TEXT NOT NULL,           -- upload | scan
    status          TEXT NOT NULL DEFAULT 'eingang',  -- eingang | abgelegt
    folder_id       INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    suggestion      TEXT,                    -- JSON: Ordnervorschläge mit Begründung
    doc_type        TEXT,
    doc_date        TEXT,
    correspondent   TEXT,
    amount          REAL,
    iban            TEXT,
    notes           TEXT NOT NULL DEFAULT '',
    text            TEXT NOT NULL DEFAULT '',
    extract_method  TEXT,
    pages           INTEGER NOT NULL DEFAULT 1,
    warnings        TEXT NOT NULL DEFAULT '',
    has_preview     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    filed_at        TEXT
);
CREATE INDEX IF NOT EXISTS documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS documents_folder ON documents(folder_id);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS document_tags (
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id       INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);

-- Gelerntes Wissen: Worthäufigkeiten der bestätigt abgelegten Dokumente je Ordner
CREATE TABLE IF NOT EXISTS folder_terms (
    folder_id  INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    term       TEXT NOT NULL,
    count      REAL NOT NULL,
    PRIMARY KEY (folder_id, term)
);

-- In wie vielen Dokumenten kommt ein Wort vor (für TF-IDF)
CREATE TABLE IF NOT EXISTS term_df (
    term  TEXT PRIMARY KEY,
    df    INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, correspondent, tags, text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

# Vorgeschlagene Grundstruktur für private Unterlagen. Ordner und Begriffe
# lassen sich in der Oberfläche jederzeit ändern.
DEFAULT_STRUCTURE = [
    ("Finanzen", "", [
        ("Bank & Konten", "kontoauszug, girokonto, kontostand, überweisung, kreditkarte, sparkasse, volksbank, depot, dauerauftrag, bankverbindung", []),
        ("Steuern", "finanzamt, steuerbescheid, einkommensteuer, steuererklärung, lohnsteuer, steuernummer, steuer-id, elster, identifikationsnummer", []),
        ("Kredite", "darlehen, kredit, tilgung, finanzierung, ratenkredit, restschuld", []),
    ]),
    ("Rechnungen & Einkäufe", "rechnung, rechnungsnummer, rechnungsbetrag, zahlbar, fällig, mwst, umsatzsteuer, quittung, kassenbon, bestellung, lieferschein, garantie, gewährleistung, bestellnummer", []),
    ("Versicherungen", "versicherung, versicherungsschein, versicherungsnummer, police, versicherungsnehmer, beitrag, schadensmeldung, haftpflicht, hausrat, rechtsschutz, lebensversicherung, berufsunfähigkeit", []),
    ("Wohnen", "wohnung, vermieter, mieter, hausverwaltung, eigentümer", [
        ("Miete & Nebenkosten", "miete, mietvertrag, kaltmiete, nebenkosten, nebenkostenabrechnung, betriebskosten, kaution, heizkosten", []),
        ("Energie", "strom, gas, stadtwerke, zählerstand, kwh, abschlag, jahresabrechnung, energie, fernwärme, wasser", []),
        ("Internet & Telefon", "telekom, vodafone, internet, dsl, glasfaser, mobilfunk, rufnummer, tarif, handy, o2, rundfunkbeitrag", []),
    ]),
    ("Gesundheit", "arzt, praxis, krankenkasse, rezept, befund, diagnose, krankenhaus, klinik, zahnarzt, apotheke, arbeitsunfähigkeit, aok, barmer, techniker, patient, behandlung", []),
    ("Arbeit", "arbeitgeber, arbeitnehmer, personalnummer", [
        ("Gehaltsabrechnungen", "gehaltsabrechnung, entgeltabrechnung, lohnabrechnung, verdienstabrechnung, bruttolohn, nettolohn, auszahlungsbetrag, sozialversicherung", []),
        ("Verträge & Zeugnisse", "arbeitsvertrag, arbeitszeugnis, bewerbung, kündigungsfrist, probezeit, aufhebungsvertrag", []),
    ]),
    ("Fahrzeuge", "kfz, fahrzeug, auto, werkstatt, tüv, hauptuntersuchung, zulassung, kennzeichen, inspektion, fahrrad, reifen, fahrzeugschein", []),
    ("Behörden & Ausweise", "ausweis, personalausweis, reisepass, meldebescheinigung, bürgeramt, standesamt, geburtsurkunde, führerschein, stadtverwaltung, landratsamt, rentenversicherung, bescheid, aktenzeichen", []),
    ("Verträge & Abos", "vertrag, vertragsnummer, kündigung, laufzeit, abonnement, abo, mitgliedschaft, mitgliedsnummer, verlängerung", []),
    ("Bildung", "schule, zeugnis, universität, hochschule, studium, kurs, zertifikat, immatrikulation, semester, prüfung", []),
    ("Sonstiges", "", []),
]


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection, seed: bool = True) -> None:
    conn.executescript(SCHEMA)
    if seed and conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0:
        _seed(conn, DEFAULT_STRUCTURE, None)
    conn.commit()


def _seed(conn, items, parent_id):
    for name, keywords, children in items:
        cur = conn.execute(
            "INSERT INTO folders(parent_id, name, keywords) VALUES (?, ?, ?)",
            (parent_id, name, keywords),
        )
        _seed(conn, children, cur.lastrowid)
