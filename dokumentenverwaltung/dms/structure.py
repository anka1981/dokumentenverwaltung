"""Ordnerstruktur aus einer eingerückten Liste übernehmen.

Beispiel (Einrückung mit Leerzeichen oder Tabs, Aufzählungszeichen optional):

    * Versicherung
       * Krankenversicherung
          * AU
       * Hausrat
    * Wohnung

Gleichnamige Ordner auf derselben Ebene werden zusammengeführt. Neue Ordner
bekommen passende Erkennungsbegriffe; unbekannte Namen (Adressen, Personen,
Firmen) werden selbst zum Suchbegriff.
"""

from __future__ import annotations

import re

from .textutil import norm

# Erkennungsbegriffe für häufige Ordnernamen (Schlüssel normalisiert, siehe norm())
SUGGESTIONS = {
    "rechnungen": "rechnung, rechnungsnummer, rechnungsbetrag, zahlbar, fällig, mwst, umsatzsteuer, quittung, kassenbon",
    "rechnung": "rechnung, rechnungsnummer, rechnungsbetrag, zahlbar, fällig, mwst",
    "wohnung": "wohnung, miete, mietvertrag, vermieter, hausverwaltung, nebenkosten, nebenkostenabrechnung, betriebskosten, kaution",
    "wohnen": "wohnung, miete, mietvertrag, vermieter, hausverwaltung, nebenkosten, betriebskosten",
    "garten": "garten, kleingarten, parzelle, pacht, gartenverein, kleingartenverein",
    "pflanzen": "pflanze, pflanzen, saatgut, gärtnerei, baumschule, staude",
    "pflanzenpass": "pflanzenpass, plant passport",
    "arbeit": "arbeitgeber, arbeitnehmer, personalnummer",
    "gehalt": "gehaltsabrechnung, entgeltabrechnung, lohnabrechnung, verdienstabrechnung, bruttolohn, nettolohn, auszahlungsbetrag",
    "vertrage": "vertrag, arbeitsvertrag, änderungsvertrag, aufhebungsvertrag, vereinbarung, zusatzvereinbarung",
    "vertraege": "vertrag, arbeitsvertrag, änderungsvertrag, aufhebungsvertrag, vereinbarung, zusatzvereinbarung",
    "versicherung": "versicherung, versicherungsschein, versicherungsnummer, police, versicherungsnehmer, beitrag",
    "versicherungen": "versicherung, versicherungsschein, versicherungsnummer, police, versicherungsnehmer, beitrag",
    "krankenversicherung": "krankenkasse, krankenversicherung, versichertennummer, aok, barmer, techniker, dak, ikk, bkk, gesundheitskarte",
    "au": "arbeitsunfähigkeit, arbeitsunfähigkeitsbescheinigung, au-bescheinigung, krankschreibung, krankmeldung",
    "rente": "rentenversicherung, renteninformation, rentenauskunft, altersvorsorge, riester, betriebsrente, deutsche rentenversicherung",
    "auto": "kfz, kfz-versicherung, fahrzeug, kennzeichen, fahrzeugschein, zulassung, tüv, hauptuntersuchung, werkstatt",
    "berufsunfahigkeit": "berufsunfähigkeit, berufsunfähigkeitsversicherung, bu-versicherung",
    "berufsunfaehigkeit": "berufsunfähigkeit, berufsunfähigkeitsversicherung, bu-versicherung",
    "hausrat": "hausrat, hausratversicherung",
    "haftpflicht": "haftpflicht, haftpflichtversicherung, privathaftpflicht",
    "personliche dokumente": "personalausweis, reisepass, ausweis, geburtsurkunde, heiratsurkunde, meldebescheinigung, führerschein, zeugnis, urkunde",
    "persoenliche dokumente": "personalausweis, reisepass, ausweis, geburtsurkunde, heiratsurkunde, meldebescheinigung, führerschein, zeugnis, urkunde",
    "erbschaft": "erbschaft, erbschein, erbe, erben, nachlass, testament, nachlassgericht, erbschaftsteuer, erbauseinandersetzung",
    "steuern": "finanzamt, steuerbescheid, einkommensteuer, steuererklärung, lohnsteuer, steuernummer",
    "bank": "kontoauszug, girokonto, kontostand, überweisung, kreditkarte, depot",
    "gesundheit": "arzt, praxis, rezept, befund, diagnose, krankenhaus, klinik, zahnarzt, apotheke",
}

# Zu allgemeine Namen, die nicht selbst als Suchbegriff taugen
_TOO_GENERIC = {"vater", "mutter", "sonstiges", "allgemein", "diverses", "privat", "archiv", "neu", "alt"}

_BULLET = re.compile(r"^\s*(?:[*\-•·–+]|\d+[.)])\s+")


class OutlineError(Exception):
    pass


def parse_outline(text: str) -> list[dict]:
    """Eingerückte Liste → [{"name": …, "children": […]}]."""
    roots: list[dict] = []
    stack: list[tuple[int, list]] = [(-1, roots)]
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        line = raw.replace("\t", "    ")
        indent = len(line) - len(line.lstrip(" "))
        name = _BULLET.sub("", line).strip().strip(":")
        name = re.sub(r"\s+", " ", name)
        if not name:
            continue
        if "/" in name or "\\" in name:
            raise OutlineError(f"„{name}“: Schrägstriche sind in Ordnernamen nicht erlaubt")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        siblings = stack[-1][1]
        node = next((n for n in siblings if n["name"].lower() == name.lower()), None)
        if node is None:
            node = {"name": name, "children": []}
            siblings.append(node)
        stack.append((indent, node["children"]))
    if not roots:
        raise OutlineError("Die Liste ist leer")
    return roots


def suggest_keywords(name: str) -> str:
    key = norm(name).strip()
    if key in SUGGESTIONS:
        return SUGGESTIONS[key]
    if key in _TOO_GENERIC or len(key) < 3:
        return ""
    return name.lower()  # Adressen, Personen, Firmen: der Name selbst


def flatten(tree: list[dict], prefix: tuple = ()) -> list[tuple[str, ...]]:
    out = []
    for node in tree:
        path = prefix + (node["name"],)
        out.append(path)
        out += flatten(node["children"], path)
    return out
