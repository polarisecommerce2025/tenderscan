"""TenderScan – serwer WWW + harmonogram skanowania BZP.

Zmienne środowiskowe:
  TS_DATA_DIR        katalog na bazę i katalog modeli (domyślnie ./data)
  TS_PORT            port HTTP (8000)
  TS_USER / TS_PASS  logowanie (HTTP Basic). Brak TS_PASS = bez logowania (tylko lokalnie!)
  TS_INTERVAL_H      co ile godzin skanować (6)
  TS_BACKFILL_DAYS   ile dni wstecz pobrać przy pierwszym starcie (30)
  TS_MIN_RELEVANCE   próg trafności 0-100 (50)
"""
from __future__ import annotations

import base64
import collections
import json
import os
import shutil
import threading
import time
import traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .pipeline import analyse, connect, rematch_all, save, scan_bzp

APP_DIR = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("TS_DATA_DIR", APP_DIR / "data"))
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "tenders.db"
CATALOG_PATH = DATA / "catalog.json"
if not CATALOG_PATH.exists():
    shutil.copy(APP_DIR / "seed" / "catalog.json", CATALOG_PATH)

USER, PASS = os.environ.get("TS_USER", "admin"), os.environ.get("TS_PASS", "")
INTERVAL_H = float(os.environ.get("TS_INTERVAL_H", "6"))
BACKFILL = int(os.environ.get("TS_BACKFILL_DAYS", "30"))
MIN_REL = int(os.environ.get("TS_MIN_RELEVANCE", "50"))

con = connect(DB_PATH)
LOG = collections.deque(maxlen=300)
STATE = {"running": False, "current": None, "next_run": None}
SCAN_LOCK = threading.Lock()


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    LOG.append(line)
    print(line, flush=True)


def meta(key, value=None):
    if value is None:
        r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value)); con.commit()


def catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["models"]


def migrate():
    """v2: poprawione pobieranie (SearchAfter) i klasyfikacja – wyczyść wyniki z wersji 1."""
    if meta("schema_ver") != "2":
        con.execute("DELETE FROM tenders WHERE source='BZP'")
        con.execute("DELETE FROM scans")
        con.execute("DELETE FROM meta WHERE key IN ('last_scan_to','last_scan_ok')")
        con.commit()
        meta("schema_ver", "2")
        log("Migracja v2: wyczyszczono wyniki skanów z poprzedniej wersji")


def seed_history():
    """Pierwszy start: wczytaj historię z plików Projectsystem (seed/history.json)."""
    if meta("seeded"):
        return
    f = APP_DIR / "seed" / "history.json"
    if f.exists() and os.environ.get("TS_SEED_HISTORY", "1") == "1":
        recs = json.loads(f.read_text(encoding="utf-8"))
        cat = catalog()
        for r in recs:
            save(con, analyse(r, cat))
        log(f"Wczytano historię Projectsystem: {len(recs)} pozycji")
    meta("seeded", "1")


def run_scan(date_from: datetime, date_to: datetime, reason: str):
    if not SCAN_LOCK.acquire(blocking=False):
        log("Skan już trwa – pomijam"); return
    STATE["running"] = True
    STATE["current"] = f"{date_from:%Y-%m-%d} → {date_to:%Y-%m-%d} ({reason})"
    started = datetime.now()
    err, stats = None, {}
    try:
        log(f"Start skanu BZP {STATE['current']}")
        stats = scan_bzp(con, catalog(), date_from, date_to, MIN_REL, log=log)
        meta("last_scan_to", date_to.isoformat(timespec="seconds"))
        meta("last_scan_ok", datetime.now().isoformat(timespec="seconds"))
        log(f"Koniec skanu: przejrzano {stats['seen']}, ATV/UTV {stats['hits']}, nowych {stats['new']}, "
            f"powiązanych wyników {stats['results_linked']}")
        for it in stats.get("new_items", []):
            log(f"  NOWY: {it['buyer']} – {it['title'][:90]} → {it['model'] or '—'} {it['verdict'] or ''}")
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log("BŁĄD skanu: " + err)
        traceback.print_exc()
    finally:
        con.execute("INSERT INTO scans (started, finished, date_from, date_to, seen, hits, new, error) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (started.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds"),
                     date_from.isoformat(timespec="seconds"), date_to.isoformat(timespec="seconds"),
                     stats.get("seen"), stats.get("hits"), stats.get("new"), err))
        con.commit()
        STATE["running"] = False; STATE["current"] = None
        SCAN_LOCK.release()


def scheduler():
    migrate()
    seed_history()
    while True:
        try:
            now = datetime.now()
            last = meta("last_scan_to")
            start = (datetime.fromisoformat(last) - timedelta(days=1)) if last else now - timedelta(days=BACKFILL)
            run_scan(start, now, "automatyczny" if last else f"pierwszy start – {BACKFILL} dni wstecz")
        except Exception as e:  # noqa: BLE001
            log(f"Błąd harmonogramu: {e}")
        nxt = datetime.now() + timedelta(hours=INTERVAL_H)
        STATE["next_run"] = nxt.isoformat(timespec="minutes")
        while datetime.now() < nxt:
            time.sleep(30)


def tenders_payload():
    rows = []
    for r in con.execute("SELECT * FROM tenders ORDER BY COALESCE(published,'') DESC"):
        d = dict(r)
        for k in ("why", "reqs", "matches", "winners"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except (TypeError, json.JSONDecodeError):
                    pass
        d.update(json.loads(d.pop("extra") or "{}"))
        d["description"] = (d.get("description") or "")[:4000]
        rows.append(d)
    return rows


def status_payload():
    scans = [dict(r) for r in con.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 10")]
    return {"running": STATE["running"], "current": STATE["current"], "next_run": STATE["next_run"],
            "last_ok": meta("last_scan_ok"), "interval_h": INTERVAL_H, "min_relevance": MIN_REL,
            "scans": scans, "log": list(LOG)[-80:]}


class Handler(BaseHTTPRequestHandler):
    server_version = "TenderScan/1.0"

    def log_message(self, *a):
        pass

    def _auth(self) -> bool:
        if not PASS:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                u, p = base64.b64decode(h[6:]).decode().split(":", 1)
                if u == USER and p == PASS:
                    return True
            except Exception:  # noqa: BLE001
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="TenderScan"')
        self.end_headers()
        return False

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, (bytes, bytearray)):
            body = (json.dumps(body, ensure_ascii=False, default=str) if ctype.startswith("application/json")
                    else str(body)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if not self._auth():
            return
        if self.path in ("/", "/index.html"):
            return self._send(200, (APP_DIR / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path in ("/logo-color.svg", "/logo-white.svg"):  # logo Polonia Cup (za logowaniem, jak cały panel)
            return self._send(200, (APP_DIR / "web" / self.path[1:]).read_bytes(), "image/svg+xml")
        if self.path == "/api/data":
            return self._send(200, {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "tenders": tenders_payload(), "catalog": catalog(),
                                    "status": status_payload()})
        if self.path == "/api/status":
            return self._send(200, status_payload())
        if self.path == "/api/catalog":
            return self._send(200, json.loads(CATALOG_PATH.read_text(encoding="utf-8")))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            return
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._send(400, {"error": "Niepoprawny JSON"})
        if self.path == "/api/scan":
            if STATE["running"]:
                return self._send(409, {"error": "Skan już trwa"})
            days = max(1, min(int(body.get("days", 2)), 90))
            now = datetime.now()
            threading.Thread(target=run_scan, args=(now - timedelta(days=days), now, f"ręczny {days} dni"),
                             daemon=True).start()
            return self._send(202, {"ok": True})
        if self.path == "/api/catalog":
            models = body.get("models")
            if not isinstance(models, list) or not all(isinstance(m, dict) and "spec" in m and "name" in m
                                                       for m in models):
                return self._send(400, {"error": "Katalog musi mieć listę 'models' z polami name, spec"})
            CATALOG_PATH.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            n = rematch_all(con, models)
            log(f"Zapisano katalog ({len(models)} modeli), przeliczono dopasowania: {n}")
            return self._send(200, {"ok": True, "rematched": n})
        if self.path == "/api/import":
            recs = body if isinstance(body, list) else body.get("records")
            if not isinstance(recs, list):
                return self._send(400, {"error": "Oczekiwano listy rekordów (history.json)"})
            cat, n = catalog(), 0
            for r in recs:
                if isinstance(r, dict) and r.get("id"):
                    save(con, analyse(r, cat)); n += 1
            log(f"Zaimportowano {n} pozycji z pliku")
            return self._send(200, {"ok": True, "imported": n})
        if self.path.startswith("/api/tender/") and self.path.endswith("/note"):
            tid = self.path[len("/api/tender/"):-len("/note")]
            from urllib.parse import unquote
            tid = unquote(tid)
            row = con.execute("SELECT extra FROM tenders WHERE id=?", (tid,)).fetchone()
            if not row:
                return self._send(404, {"error": "brak"})
            extra = json.loads(row["extra"] or "{}")
            extra["note"] = str(body.get("note", ""))[:2000]
            extra["watch"] = bool(body.get("watch", extra.get("watch", False)))
            con.execute("UPDATE tenders SET extra=? WHERE id=?", (json.dumps(extra, ensure_ascii=False), tid))
            con.commit()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})


def main():
    port = int(os.environ.get("TS_PORT", "8000"))
    if not PASS:
        log("UWAGA: brak TS_PASS – panel bez logowania")
    if os.environ.get("TS_NO_SCHEDULER") != "1":
        threading.Thread(target=scheduler, daemon=True).start()
    else:
        seed_history()
    log(f"TenderScan nasłuchuje na porcie {port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
