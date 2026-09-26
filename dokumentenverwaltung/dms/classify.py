"""Ordnervorschlag für neue Dokumente.

Drei Hinweise werden kombiniert:

1. Regeln: Begriffe, die an einem Ordner hinterlegt sind (auch als Wortteil,
   z. B. "versicherung" in "Haftpflichtversicherung"). Begriffe des
   übergeordneten Ordners zählen für Unterordner zur Hälfte mit.
2. Gelerntes: Ähnlichkeit (Kosinus, TF-IDF) zu den Dokumenten, die bereits
   bestätigt in einem Ordner abgelegt wurden.
3. Absender: wohin frühere Dokumente desselben Absenders abgelegt wurden.

Jede Bestätigung oder manuelle Korrektur fließt über learn() in 2. und 3. ein.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict

from .textutil import content_tokens, norm, tokens

MAX_SUGGESTIONS = 3
FALLBACK_FOLDER = "Sonstiges"


def split_keywords(raw: str) -> list[str]:
    return [k.strip() for k in raw.replace(";", ",").replace("\n", ",").split(",") if k.strip()]


def _rule_hits(keywords: list[str], token_counts: Counter, joined: str) -> dict[str, int]:
    hits = {}
    for kw in keywords:
        parts = tokens(kw)
        if not parts:
            continue
        if len(parts) > 1:
            n = f" {joined} ".count(" " + " ".join(parts) + " ")
        elif len(parts[0]) >= 5:
            n = sum(c for t, c in token_counts.items() if parts[0] in t)
        else:
            n = token_counts.get(parts[0], 0)
        if n:
            hits[kw] = n
    return hits


def _idf(conn: sqlite3.Connection) -> tuple[dict[str, int], int]:
    df = {r["term"]: r["df"] for r in conn.execute("SELECT term, df FROM term_df")}
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    return df, n


def _vector(counts: dict[str, float], df: dict[str, int], n_docs: int) -> dict[str, float]:
    return {
        t: (1 + math.log(c)) * (math.log((n_docs + 1) / (df.get(t, 0) + 1)) + 1)
        for t, c in counts.items()
        if c >= 1
    }


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(w * b.get(t, 0.0) for t, w in a.items())
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def folder_paths(conn: sqlite3.Connection) -> dict[int, str]:
    rows = {r["id"]: r for r in conn.execute("SELECT id, parent_id, name FROM folders")}

    def path(fid):
        r = rows[fid]
        return r["name"] if r["parent_id"] is None else f"{path(r['parent_id'])} / {r['name']}"

    return {fid: path(fid) for fid in rows}


def suggest(conn: sqlite3.Connection, text: str, correspondent: str | None = None, exclude_doc: int | None = None) -> list[dict]:
    """Liefert die besten Ordner mit Sicherheit (0–1) und Begründung."""
    folders = list(conn.execute("SELECT id, parent_id, name, keywords FROM folders"))
    if not folders:
        return []
    paths = folder_paths(conn)
    all_tokens = tokens(text)
    token_counts = Counter(all_tokens)
    joined = " ".join(all_tokens)

    # 1. Regeln
    own: dict[int, tuple[float, dict]] = {}
    for f in folders:
        hits = _rule_hits(split_keywords(f["keywords"]), token_counts, joined)
        weight = sum(1 + 0.5 * math.log(n) for n in hits.values())
        own[f["id"]] = (weight, hits)
    parent_of = {f["id"]: f["parent_id"] for f in folders}

    def effective(fid, depth=0):
        w = own[fid][0]
        p = parent_of[fid]
        return w + (0.5 * effective(p, depth + 1) if p is not None and depth < 10 else 0)

    # 2. Gelerntes
    df, n_docs = _idf(conn)
    doc_vec = _vector(Counter(content_tokens(text)), df, n_docs)
    learned_terms: dict[int, dict[str, float]] = defaultdict(dict)
    for r in conn.execute("SELECT folder_id, term, count FROM folder_terms"):
        learned_terms[r["folder_id"]][r["term"]] = r["count"]
    filed = Counter()
    sql = "SELECT folder_id, COUNT(*) FROM documents WHERE status='abgelegt' AND folder_id IS NOT NULL"
    args: tuple = ()
    if exclude_doc is not None:
        sql += " AND id != ?"
        args = (exclude_doc,)
    for fid, cnt in conn.execute(sql + " GROUP BY folder_id", args):
        filed[fid] = cnt

    # 3. Absender
    by_sender = Counter()
    if correspondent:
        rows = conn.execute(
            "SELECT folder_id, correspondent FROM documents WHERE status='abgelegt' AND folder_id IS NOT NULL"
            + (" AND id != ?" if exclude_doc is not None else ""),
            args,
        )
        key = norm(correspondent)
        for r in rows:
            if r["correspondent"] and norm(r["correspondent"]) == key:
                by_sender[r["folder_id"]] += 1
    sender_total = sum(by_sender.values())

    results = []
    for f in folders:
        fid = f["id"]
        w = effective(fid)
        rule = w / (w + 1.5)
        learned = 0.0
        if learned_terms.get(fid) and filed[fid]:
            cos = _cosine(doc_vec, _vector(learned_terms[fid], df, n_docs))
            learned = min(1.0, 1.5 * cos) * filed[fid] / (filed[fid] + 1)
        # Eigene Ablageentscheidungen wiegen schwerer als die allgemeinen Regeln
        sender = 0.95 * by_sender[fid] / (sender_total + 0.25) if sender_total else 0.0
        score = 1 - (1 - rule) * (1 - learned) * (1 - sender)
        if score <= 0:
            continue
        reasons = []
        hits = dict(own[fid][1])
        p = parent_of[fid]
        while p is not None:
            for k, v in own[p][1].items():
                hits.setdefault(k, v)
            p = parent_of[p]
        if hits:
            top = sorted(hits.items(), key=lambda kv: -kv[1])[:5]
            reasons.append("Begriffe: " + ", ".join(k for k, _ in top))
        if learned >= 0.05:
            reasons.append(f"ähnlich zu {filed[fid]} abgelegten Dokument{'en' if filed[fid] != 1 else ''}")
        if by_sender[fid]:
            reasons.append(f"{by_sender[fid]}× von {correspondent} hier abgelegt")
        results.append({
            "folder_id": fid,
            "path": paths[fid],
            "score": round(score, 3),
            "reasons": reasons,
        })

    results.sort(key=lambda r: (-r["score"], r["path"]))
    results = results[:MAX_SUGGESTIONS]
    if not results or results[0]["score"] < 0.1:
        fallback = next((f["id"] for f in folders if f["name"] == FALLBACK_FOLDER and f["parent_id"] is None), None)
        if fallback is not None and all(r["folder_id"] != fallback for r in results):
            results.insert(0, {"folder_id": fallback, "path": paths[fallback], "score": 0.0,
                               "reasons": ["keine eindeutigen Hinweise gefunden"]})
    return results


def learn(conn: sqlite3.Connection, folder_id: int, text: str, sign: int = 1) -> None:
    """Merkt sich die Wörter eines Dokuments für den Ordner (sign=-1: vergessen)."""
    counts = Counter(content_tokens(text))
    for term, c in counts.items():
        conn.execute(
            "INSERT INTO folder_terms(folder_id, term, count) VALUES (?, ?, ?) "
            "ON CONFLICT(folder_id, term) DO UPDATE SET count = count + excluded.count",
            (folder_id, term, sign * c),
        )
    if sign < 0:
        conn.execute("DELETE FROM folder_terms WHERE folder_id = ? AND count <= 0", (folder_id,))


def update_df(conn: sqlite3.Connection, text: str, sign: int = 1) -> None:
    for term in set(content_tokens(text)):
        conn.execute(
            "INSERT INTO term_df(term, df) VALUES (?, ?) "
            "ON CONFLICT(term) DO UPDATE SET df = df + excluded.df",
            (term, sign),
        )
    if sign < 0:
        conn.execute("DELETE FROM term_df WHERE df <= 0")
