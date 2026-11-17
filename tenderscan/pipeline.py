"""Przetwarzanie ogłoszeń: klasyfikacja -> parsowanie wymagań -> dopasowanie -> zapis w SQLite."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import bzp_client as bzp
from .classify import (WADIUM_RX, classify, extract_values, find_platform,
                       html_to_text, platform_url)
from .matcher import best_matches
from .spec_parser import parse_requirements

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
  id TEXT PRIMARY KEY, kind TEXT, source TEXT, tender_id TEXT,
  published TEXT, deadline TEXT, buyer TEXT, buyer_nip TEXT, city TEXT, province TEXT,
  title TEXT, cpv TEXT, vehicle_type TEXT, relevance INTEGER, why TEXT,
  link TEXT, platform_link TEXT, platform TEXT,
  description TEXT, reqs TEXT, matches TEXT, best_score INTEGER, best_model TEXT, best_verdict TEXT,
  winners TEXT, value REAL, budget REAL, status TEXT,
  extra TEXT, first_seen TEXT, updated TEXT
);
CREATE INDEX IF NOT EXISTS ix_tid ON tenders(tender_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT,
  date_from TEXT, date_to TEXT, seen INTEGER, hits INTEGER, new INTEGER, error TEXT);
"""

COLS = ["id", "kind", "source", "tender_id", "published", "deadline", "buyer", "buyer_nip",
        "city", "province", "title", "cpv", "vehicle_type", "relevance", "why", "link",
        "platform_link", "platform", "description", "reqs", "matches", "best_score",
        "best_model", "best_verdict", "winners", "value", "budget", "status"]

LOCK = threading.RLock()


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def analyse(record: dict, catalog: list[dict]) -> dict:
    text = record.get("description") or ""
    cls = classify(record.get("title", ""), record.get("cpv", ""), text)
    title_cls = classify(record.get("title", ""), "", "")
    if title_cls["vehicle_type"] != "?":
        cls["vehicle_type"] = title_cls["vehicle_type"]
    elif record.get("vehicle_type") in ("ATV", "UTV", "Łączone"):
        cls["vehicle_type"] = record["vehicle_type"]
    parsed = parse_requirements(text)
    head = parse_requirements(re.split(r"\+|\bwraz z\b|\boraz\b", record.get("title") or "")[0])
    have = {r["param"] for r in parsed["reqs"]}
    parsed["reqs"] += [r for r in head["reqs"] if r["param"] not in have]
    parsed["flags"] = {k: v or head["flags"].get(k, False) for k, v in parsed["flags"].items()}
    matches = best_matches(catalog, parsed, cls["vehicle_type"]) if parsed["reqs"] else []
    record.update({
        "vehicle_type": cls["vehicle_type"],
        "relevance": max(cls["relevance"], record.get("relevance_floor", 0)),
        "why": cls["why"], "reqs": parsed, "matches": matches,
        "best_score": matches[0]["score"] if matches else None,
        "best_model": matches[0]["model"] if matches else None,
        "best_verdict": matches[0]["verdict"] if matches else None,
    })
    return record


def _contractors(n: dict) -> list[str]:
    out = []
    for c in n.get("contractors") or []:
        if isinstance(c, dict):
            name = c.get("contractorName") or c.get("name") or c.get("organizationName")
            if name:
                out.append(name.strip())
        elif isinstance(c, str):
            out.append(c.strip())
    return out


def from_bzp(n: dict) -> dict:
    text = html_to_text(n.get("htmlBody"))
    kind = "result" if n.get("noticeType") == bzp.RESULT_NOTICE else "open"
    purl = platform_url(text)
    vals = extract_values(text) if kind == "result" else []
    wad = WADIUM_RX.search(text)
    title = (n.get("orderObject") or "").strip()
    return {
        "id": n.get("noticeNumber") or n.get("bzpNumber") or n.get("objectId"),
        "kind": kind, "source": "BZP", "tender_id": n.get("tenderId"),
        "published": (n.get("publicationDate") or "")[:10],
        "deadline": (n.get("submittingOffersDate") or "")[:16].replace("T", " "),
        "buyer": n.get("organizationName"), "buyer_nip": n.get("organizationNationalId"),
        "city": n.get("organizationCity"),
        "province": bzp.PROVINCES.get(n.get("organizationProvince") or "", n.get("organizationProvince")),
        "title": title, "cpv": n.get("cpvCode") or "",
        "link": bzp.notice_link(n), "platform_link": purl, "platform": find_platform(purl),
        "description": text[:30000],
        "winners": _contractors(n),
        "value": sum(vals) if vals else None, "budget": None,
        "status": n.get("procedureResult") or ("Składanie ofert" if kind == "open" else "Rozstrzygnięte"),
        "wadium": wad.group(1) if wad else None, "bzp_number": n.get("bzpNumber"),
    }


def _row_values(r: dict) -> list:
    return [json.dumps(r.get(c), ensure_ascii=False) if isinstance(r.get(c), (list, dict))
            else r.get(c) for c in COLS]


def save(con: sqlite3.Connection, r: dict) -> bool:
    """Zapis/aktualizacja. Zwraca True dla nowego rekordu."""
    now = datetime.now().isoformat(timespec="seconds")
    extra = {k: v for k, v in r.items() if k not in COLS}
    with LOCK:
        old = con.execute("SELECT * FROM tenders WHERE id=?", (r["id"],)).fetchone()
        if old:
            prev = json.loads(old["extra"] or "{}")
            for k in ("note", "watch", "result_link", "result_published"):  # dane użytkownika i wyniku zostają
                if k in prev and k not in extra:
                    extra[k] = prev[k]
            if old["kind"] == "result" and r.get("kind") == "open":  # nie cofaj rozstrzygniętego
                r = {**r, "kind": "result", "status": old["status"], "value": old["value"],
                     "winners": json.loads(old["winners"] or "[]")}
        extra = json.dumps(extra, ensure_ascii=False, default=str)
        vals = _row_values(r)
        if old:
            con.execute(f"UPDATE tenders SET {', '.join(c + '=?' for c in COLS[1:])}, extra=?, updated=? WHERE id=?",
                        vals[1:] + [extra, now, r["id"]])
        else:
            con.execute(f"INSERT INTO tenders ({', '.join(COLS)}, extra, first_seen, updated) "
                        f"VALUES ({', '.join('?' * len(COLS))}, ?, ?, ?)", vals + [extra, now, now])
        con.commit()
    return not old


def apply_result(con: sqlite3.Connection, res: dict) -> bool:
    """Ogłoszenie o wyniku: dopisz zwycięzcę do istniejącego przetargu (po tenderId)."""
    if not res.get("tender_id"):
        return False
    with LOCK:
        row = con.execute("SELECT * FROM tenders WHERE tender_id=? AND id<>? ORDER BY kind='open' DESC",
                          (res["tender_id"], res["id"])).fetchone()
        if not row:
            return False
        extra = json.loads(row["extra"] or "{}")
        extra["result_link"] = res["link"]
        extra["result_published"] = res["published"]
        con.execute("UPDATE tenders SET kind='result', status=?, winners=?, value=?, extra=?, updated=? WHERE id=?",
                    (res["status"], json.dumps(res["winners"], ensure_ascii=False), res["value"],
                     json.dumps(extra, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"),
                     row["id"]))
        con.commit()
    return True


def rematch_all(con: sqlite3.Connection, catalog: list[dict]) -> int:
    """Po zmianie katalogu – przelicz dopasowania dla wszystkich rekordów."""
    n = 0
    with LOCK:
        rows = [dict(r) for r in con.execute("SELECT * FROM tenders")]
    for r in rows:
        rec = {**json.loads(r.get("extra") or "{}"), **{k: r[k] for k in COLS}}
        for k in ("why", "winners"):
            rec[k] = json.loads(rec[k]) if isinstance(rec[k], str) and rec[k].startswith("[") else rec[k]
        analyse(rec, catalog)
        save(con, rec); n += 1
    return n


def scan_bzp(con, catalog, date_from: datetime, date_to: datetime,
             min_relevance: int = 50, log=print, client: bzp.BzpClient | None = None) -> dict:
    client = client or bzp.BzpClient()
    stats = {"seen": 0, "hits": 0, "new": 0, "results_linked": 0, "new_items": []}
    for nt in (bzp.CONTRACT_NOTICE, bzp.RESULT_NOTICE):
        seen = hit = 0
        for n in client.fetch_range(nt, date_from, date_to):
            seen += 1
            rec = analyse(from_bzp(n), catalog)
            if rec["kind"] == "result" and apply_result(con, rec):
                stats["results_linked"] += 1
                hit += 1
                continue
            if rec["relevance"] < min_relevance:
                continue
            hit += 1
            if save(con, rec):
                stats["new"] += 1
                stats["new_items"].append({"id": rec["id"], "title": rec["title"], "buyer": rec["buyer"],
                                           "verdict": rec["best_verdict"], "model": rec["best_model"]})
        log(f"{nt}: przejrzano {seen}, trafień ATV/UTV {hit}")
        stats["seen"] += seen; stats["hits"] += hit
    return stats
