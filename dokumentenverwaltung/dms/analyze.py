"""Verschlagwortung und Metadaten: Schlagwörter, Dokumentart, Datum, Betrag,
Absender und ein Titelvorschlag."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from .textutil import is_content_word, norm, surface_words

MONTHS = {
    "januar": 1, "jan": 1, "jaenner": 1, "februar": 2, "feb": 2, "maerz": 3, "mrz": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10,
    "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}

# (Dokumentart, Suchbegriffe) – spezifischere Arten zuerst.
DOC_TYPES = [
    ("Mahnung", ["mahnung", "zahlungserinnerung"]),
    ("Kündigung", ["kuendigung", "kuendigungsbestaetigung"]),
    ("Gutschrift", ["gutschrift"]),
    ("Kontoauszug", ["kontoauszug", "kontostand"]),
    ("Gehaltsabrechnung", ["gehaltsabrechnung", "entgeltabrechnung", "lohnabrechnung", "verdienstabrechnung"]),
    ("Steuerbescheid", ["steuerbescheid", "einkommensteuerbescheid"]),
    ("Bescheid", ["bescheid"]),
    ("Versicherungsschein", ["versicherungsschein", "police", "nachtrag zum versicherungsschein"]),
    ("Rechnung", ["rechnung", "rechnungsnummer", "rechnungsbetrag", "invoice"]),
    ("Angebot", ["angebot", "kostenvoranschlag"]),
    ("Auftragsbestätigung", ["auftragsbestaetigung", "bestellbestaetigung"]),
    ("Lieferschein", ["lieferschein"]),
    ("Quittung", ["quittung", "kassenbon", "kassenbeleg", "beleg nr"]),
    ("Vertrag", ["vertrag", "vertragsbedingungen", "vereinbarung"]),
    ("Arztbrief", ["befund", "arztbrief", "diagnose"]),
    ("Rezept", ["rezept", "verordnung"]),
    ("Zeugnis", ["zeugnis"]),
    ("Bescheinigung", ["bescheinigung", "nachweis", "bestaetigung"]),
]

_LEGAL_FORMS = r"(GmbH|AG|KG|OHG|e\.\s?V\.|eG|mbH|SE|UG|KGaA|Ltd\.?|Inc\.?|GbR|Sparkasse|Bank|Versicherung|Stadtwerke|Finanzamt|Amt|Praxis|Krankenkasse)"
_LEGAL_RE = re.compile(rf"\b{_LEGAL_FORMS}\b")

_DATE_NUM = re.compile(r"\b(\d{1,2})\s?\.\s?(\d{1,2})\s?\.\s?(\d{4}|\d{2})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_WORD = re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zÄÖÜäöü]{3,9})\.?\s+(\d{4})\b")
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?!\d)\s*(?:€|EUR|Euro)?|(?:€|EUR)\s*(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?!\d)")
_TOTAL_WORDS = re.compile(
    r"(gesamtbetrag|rechnungsbetrag|endbetrag|gesamtsumme|zu zahlen|zahlbetrag|summe|gesamt|betrag)",
    re.I,
)
# Nur Leerzeichen innerhalb der IBAN – keine Zeilenumbrüche (OCR-Text)
_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?)\b")


@dataclass
class Analysis:
    keywords: list[str] = field(default_factory=list)
    doc_type: str | None = None
    doc_date: str | None = None  # ISO
    amount: float | None = None
    iban: str | None = None
    correspondent: str | None = None
    title: str = ""


def extract_keywords(text: str, df: dict[str, int], n_docs: int, limit: int = 8) -> list[str]:
    """Schlagwörter per TF-IDF. Substantive (großgeschrieben) werden bevorzugt.

    df: Dokumenthäufigkeit je normalisiertem Wort in der Datenbank.
    """
    counts: Counter[str] = Counter()
    capital: Counter[str] = Counter()
    forms: dict[str, Counter[str]] = {}
    for n, word in surface_words(text):
        if not is_content_word(n) or len(n) < 4:
            continue
        counts[n] += 1
        if word[0].isupper():
            capital[n] += 1
        forms.setdefault(n, Counter())[word] += 1

    # Deutsche Substantive sind großgeschrieben – reichen sie aus, nur diese nehmen
    candidates = [t for t in counts if capital[t]]
    if len(candidates) < limit:
        candidates = list(counts)
    scored = []
    for term in candidates:
        tf = counts[term]
        idf = math.log((n_docs + 1) / (df.get(term, 0) + 1)) + 1
        noun = capital[term] / tf  # Anteil großgeschriebener Vorkommen
        # Wörter nur in Großbuchstaben (Überschriften) nicht übergewichten
        score = (1 + math.log(tf)) * idf * (0.4 + noun) * min(1.0, len(term) / 6)
        scored.append((score, term))
    scored.sort(reverse=True)

    result = []
    for _, term in scored:
        if any(term in r or r in term for r in (norm(k) for k in result)):
            continue  # "versicherung" neben "haftpflichtversicherung" vermeiden
        form = _display_form(forms[term])
        result.append(form)
        if len(result) >= limit:
            break
    return result


def _display_form(forms: Counter[str]) -> str:
    # Bevorzugt "Rechnung" gegenüber "RECHNUNG" oder "rechnung"
    best = max(forms.items(), key=lambda kv: (kv[0][:1].isupper() and not kv[0].isupper(), kv[1]))
    word = best[0]
    return word.capitalize() if word.isupper() and len(word) > 4 else word


def detect_doc_type(text: str) -> str | None:
    n = norm(text)
    head = n[:1500]
    best, best_score = None, 0.0
    for name, words in DOC_TYPES:
        score = 0.0
        for w in words:
            hits = len(re.findall(rf"\b{re.escape(w)}", n))
            head_hits = len(re.findall(rf"\b{re.escape(w)}", head))
            score += hits + 2 * head_hits
        if score > best_score:
            best, best_score = name, score
    return best


def _valid_date(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000 if y < 70 else 1900
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    return dt if 1950 <= dt.year <= date.today().year + 2 else None


def find_dates(text: str) -> list[tuple[int, date]]:
    """Alle Datumsangaben als (Position, Datum)."""
    found = []
    for m in _DATE_NUM.finditer(text):
        dt = _valid_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            found.append((m.start(), dt))
    for m in _DATE_ISO.finditer(text):
        dt = _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            found.append((m.start(), dt))
    for m in _DATE_WORD.finditer(text):
        month = MONTHS.get(norm(m.group(2)))
        if month:
            dt = _valid_date(int(m.group(3)), month, int(m.group(1)))
            if dt:
                found.append((m.start(), dt))
    return sorted(found, key=lambda x: x[0])


def detect_date(text: str) -> str | None:
    """Das Briefdatum: bevorzugt nach "Datum", sonst das erste im Kopfbereich."""
    dates = find_dates(text)
    if not dates:
        return None
    for m in re.finditer(r"(?i)(datum|date|vom|ausgestellt am|den)\s*:?\s*", text):
        for pos, dt in dates:
            if 0 <= pos - m.end() <= 3:
                return dt.isoformat()
    head = [dt for pos, dt in dates if pos < 1200]
    # Im Briefkopf ist das jüngste Datum meist das Briefdatum (ältere = Bezug)
    return max(head or [dates[0][1]]).isoformat()


def _parse_amount(whole: str, cents: str) -> float:
    return float(whole.replace(".", "") + "." + cents)


def detect_amount(text: str) -> float | None:
    """Gesamtbetrag: Zahl hinter "Gesamtbetrag", "Summe" usw., sonst der größte Betrag mit €."""
    best_total = None
    for line in text.splitlines():
        if not _TOTAL_WORDS.search(line):
            continue
        amounts = [
            _parse_amount(m.group(1) or m.group(3), m.group(2) or m.group(4))
            for m in _AMOUNT.finditer(line)
        ]
        if amounts:
            best_total = max(amounts[-1], best_total or 0)
    if best_total is not None:
        return best_total
    euro = [
        _parse_amount(m.group(1) or m.group(3), m.group(2) or m.group(4))
        for m in _AMOUNT.finditer(text)
        if "€" in m.group(0) or "EUR" in m.group(0) or "Euro" in m.group(0)
    ]
    return max(euro) if euro else None


def _iban_ok(iban: str) -> bool:
    if not iban.isascii() or not iban.isalnum():
        return False
    s = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in s)
    return int(digits) % 97 == 1


def detect_iban(text: str) -> str | None:
    for m in _IBAN.finditer(text.upper()):
        iban = re.sub(r"\s", "", m.group(1))
        if 15 <= len(iban) <= 34 and _iban_ok(iban):
            return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))
    return None


def detect_correspondent(text: str, known: list[str]) -> str | None:
    """Absender: bekannter Korrespondent im Text, sonst Firmenname im Briefkopf."""
    n = norm(text)
    hits = [(n.find(norm(k)), k) for k in known if k and len(k) >= 3 and norm(k) in n]
    if hits:
        # frühestes Vorkommen, bei Gleichstand der längste Name
        return min(hits, key=lambda h: (h[0], -len(h[1])))[1]

    lines = [l.strip() for l in text.splitlines()[:25] if l.strip()]
    for line in lines:
        # Absenderzeile im Fensterumschlag: "Firma GmbH · Straße 1 · 12345 Ort"
        name = re.split(r"\s[·|•,–\-:]\s|\s{2,}", line)[0].strip(" ,;·")
        if _LEGAL_RE.search(name) and 3 <= len(name) <= 60 and not re.search(r"\d{3,}", name):
            return name
    return None


_TRAILING_FORM = re.compile(r"(\s+(GmbH|AG|KG|OHG|e\.\s?V\.|eG|mbH|SE|UG|KGaA|GbR|&\s?Co\.?))+$")


def suggest_title(doc_type: str | None, correspondent: str | None, keywords: list[str], fallback: str) -> str:
    sender = _TRAILING_FORM.sub("", correspondent).strip() if correspondent else None
    parts = [p for p in (doc_type, sender) if p]
    if not parts and keywords:
        parts = keywords[:2]
    if len(parts) == 1 and keywords and norm(keywords[0]) not in norm(parts[0]):
        parts.append(keywords[0])
    return " ".join(parts) if parts else fallback


def analyze(text: str, fallback_title: str, df: dict[str, int], n_docs: int, known_correspondents: list[str]) -> Analysis:
    a = Analysis()
    a.keywords = extract_keywords(text, df, n_docs)
    a.doc_type = detect_doc_type(text)
    a.doc_date = detect_date(text)
    a.amount = detect_amount(text)
    a.iban = detect_iban(text)
    a.correspondent = detect_correspondent(text, known_correspondents)
    a.title = suggest_title(a.doc_type, a.correspondent, a.keywords, fallback_title)
    return a
