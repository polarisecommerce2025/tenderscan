"""Dopasowanie wymagań przetargu do katalogu naszych pojazdów."""
from __future__ import annotations

import json
from pathlib import Path

from .spec_parser import LABELS

FLAG_LABELS = {"4x4": "Napęd 4x4", "6x6": "Napęd 6x6/6x4", "cvt": "Skrzynia CVT/AT",
               "t3b": "Homologacja T3b", "l7e": "Homologacja L7e", "diff_lock": "Blokada osi",
               "eps": "Wspomaganie EPS", "cab": "Kabina"}


def load_catalog(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["models"]


def check_req(req: dict, spec: dict) -> tuple[str, str]:
    v = spec.get(req["param"])
    if v is None:
        return "unknown", "brak danych w katalogu"
    lo, hi = req.get("min"), req.get("max")
    if lo is not None and v < lo:
        return "fail", f"{v} < min {lo:g}"
    if hi is not None and v > hi:
        return "fail", f"{v} > max {hi:g}"
    return "ok", f"{v}"


def match_model(model: dict, parsed: dict, vehicle_type: str) -> dict:
    spec = model["spec"]
    rows, ok, fail, unk = [], 0, 0, 0
    for r in parsed["reqs"]:
        st, note = check_req(r, spec)
        ok += st == "ok"; fail += st == "fail"; unk += st == "unknown"
        rows.append({"param": r["param"], "label": LABELS.get(r["param"], r["param"]),
                     "min": r.get("min"), "max": r.get("max"), "value": spec.get(r["param"]),
                     "status": st, "note": note})
    for f, needed in parsed["flags"].items():
        if not needed or f not in FLAG_LABELS:
            continue
        have = spec.get("flags", {}).get(f)
        st = "unknown" if have is None else ("ok" if have else "fail")
        if f == "4x4" and spec.get("flags", {}).get("6x6"):
            st = "ok"
        ok += st == "ok"; fail += st == "fail"; unk += st == "unknown"
        rows.append({"param": f, "label": FLAG_LABELS[f], "min": None, "max": None,
                     "value": have, "status": st, "note": "wymagane"})
    type_ok = vehicle_type in ("?", "Łączone") or model["type"] == vehicle_type
    checked = ok + fail
    score = round(100 * ok / checked) if checked else 0
    if not type_ok:
        score = min(score, 20)
    return {"model": model["name"], "brand": model["brand"], "type": model["type"],
            "score": score, "ok": ok, "fail": fail, "unknown": unk,
            "type_ok": type_ok, "rows": rows,
            "verdict": ("SPEŁNIA" if fail == 0 and type_ok and checked
                        else "BLISKO" if fail <= 1 and type_ok and checked
                        else "NIE SPEŁNIA")}


def best_matches(catalog: list[dict], parsed: dict, vehicle_type: str, top: int = 3) -> list[dict]:
    res = [match_model(m, parsed, vehicle_type) for m in catalog]
    res.sort(key=lambda r: (r["type_ok"], -r["fail"], r["score"], r["ok"]), reverse=True)
    return res[:top]
