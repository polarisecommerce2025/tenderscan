"""Wydobywanie wymagań technicznych z opisu przedmiotu zamówienia (OPZ/SWZ/ogłoszenie).

Rozpoznaje zapisy typu:
  "Silnik Pb min.995ccm, min.80KM, prześwit min.350mm, ładowność min.450kg"
  "pojemność skokowa silnika nie mniej niż 950 cm3", "dł.2200-2300mm",
  "wymiary 2445x1282x1450mm (+/- 5%)", "wyciągarka min.2040kg", "4x4", "CVT", "T3b".
Wynik: lista wymagań {param, min, max, raw}.
"""
from __future__ import annotations

import re

NUM = r"(\d+(?:[.,]\d+)?)"
MIN_W = r"(?:min\.?|minimum|co najmniej|nie mniej niż|nie mniejsz\w* niż|od|≥|>=)"
MAX_W = r"(?:max\.?|maks\.?|maksymaln\w*|nie więcej niż|nie większ\w* niż|do|≤|<=)"

# parametr -> (wzorzec nazwy, jednostki dozwolone, jednostka docelowa)
PARAMS = {
    "engine_cc":      (r"pojemno\w*|silnik\w*|\bpoj\.", r"ccm|cm3|cm³|cm 3|cc\b", "ccm"),
    "power_hp":       (r"moc\w*|silnik\w*", r"KM\b|kW\b|HP\b", "KM"),
    "clearance_mm":   (r"prze[śs]wit\w*", r"mm|cm", "mm"),
    "payload_kg":     (r"ładowno\w*|nośno\w*", r"kg", "kg"),
    "towing_kg":      (r"uci[ąa]g\w*|holowan\w*|hak\w*", r"kg", "kg"),
    "winch_lbs":      (r"wyci[ąa]gark\w*", r"lbs|lb\b|kg", "lbs"),
    "length_mm":      (r"\bdł\.|długo\w*", r"mm|cm", "mm"),
    "width_mm":       (r"\bszer\.|szeroko\w*", r"mm|cm", "mm"),
    "height_mm":      (r"\bwys\.|wysoko\w*", r"mm|cm", "mm"),
    "wheelbase_mm":   (r"rozstaw\w* osi", r"mm|cm", "mm"),
    "dry_weight_kg":  (r"masa wł\.?|masa własn\w*|waga", r"kg", "kg"),
    "fuel_l":         (r"zb\.?\s?pal\.?|zbiornik\w* paliwa", r"L\b|l\b|litr\w*|dm3", "L"),
    "susp_travel_mm": (r"skok\w* zawieszenia", r"mm", "mm"),
    "speed_kmh":      (r"V\s?min|prędko\w*", r"km/h", "km/h"),
}

FLAGS = {
    "4x4": r"4x4|napęd\w* na (?:cztery|4) koła|AWD",
    "6x6": r"6x6|6x4",
    "cvt": r"\bCVT\b|automatyczn\w* skrzyni|skrzynia AT\b|\bAT/CVT",
    "t3b": r"\bT3b\b|ciągnik\w* rolnicz",
    "l7e": r"\bL7e",
    "winch": r"wyci[ąa]gark",
    "plow": r"pług",
    "cab": r"kabin",
    "diff_lock": r"blokad\w* (?:osi|mechanizmu|dyferencja)",
    "tow_hitch": r"hak\w*",
    "eps": r"wspomagani\w* (?:kierownicy|układu kierowniczego)|\bEPS\b",
    "trailer": r"przyczep",
    "privileged": r"uprzywilejowan|sygnaliz\w* świetln|ośw\.? ?ostrzegawcz",
}


ACCESSORY_RX = re.compile(r"pług|lemiesz|bagażnik|skrzyni\w* ładunkow|nosz|rozsiewacz|przyczep|"
                          r"opon|koła|felg|lina|kufer|zbiornik\w* (?!paliwa)", re.I)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _convert(val: float, unit: str, target: str) -> float:
    u = unit.lower().strip()
    if target == "KM" and u == "kw":
        return round(val * 1.35962, 1)
    if target == "lbs" and u == "kg":
        return round(val * 2.20462)
    if target == "mm" and u == "cm":
        return val * 10
    return val


def _seats(text: str) -> dict | None:
    m = re.search(r"(min\.?\s?)?(\d)\s?(?:os\b|osob|miejsc)", text, re.I)
    if m:
        n = int(m.group(2))
        return {"param": "seats", "min": n, "max": None if m.group(1) else None, "raw": m.group(0)}
    return None


def _extract(cl: str, p: str, target: str, unit_rx: str) -> dict:
    rng = re.search(NUM + r"\s?[-–]\s?" + NUM + r"\s?(" + unit_rx + r")", cl, re.I)
    if rng:
        a, b, unit = _num(rng.group(1)), _num(rng.group(2)), rng.group(3)
        return {"param": p, "min": _convert(a, unit, target), "max": _convert(b, unit, target), "raw": cl}
    um = re.search(NUM + r"\s?(" + unit_rx + r")", cl, re.I)
    pt = re.search(NUM + r"\s?/\s?" + NUM + r"\s?(" + unit_rx + r")", cl, re.I)
    val = _convert(_num(um.group(1)), um.group(2), target)
    if pt:  # P/T min.250/300mm -> słabsza z wartości
        val = _convert(min(_num(pt.group(1)), _num(pt.group(2))), pt.group(3), target)
    pre = cl[:um.start()][-25:]
    if re.search(r"max|maks|nie więcej|nie większ|do\s*$|≤", pre, re.I):
        return {"param": p, "min": None, "max": val, "raw": cl}
    return {"param": p, "min": val, "max": None, "raw": cl}


def parse_requirements(text: str) -> dict:
    """Zwraca {'reqs': [...], 'flags': {...}}"""
    text = text or ""
    reqs: list[dict] = []
    tol = 0.05 if re.search(r"\+/?-\s?5\s?%", text) else 0.0

    # część po "Dodatkowo" / "Przyczepa ..." dotyczy osprzętu, nie pojazdu
    main = re.split(r"Dodatkowo|Przyczep\w*\s*(?:transportow\w*|ewakuacyjn\w*|ratownicz\w*|quada|:)",
                    text, maxsplit=1, flags=re.I)[0]

    # wymiary AxBxC mm (dł. x szer. x wys.)
    m = re.search(r"(\d{3,4})\s?x\s?(\d{3,4})\s?x\s?(\d{3,4})\s?mm", main)
    if m:
        ctx = main[max(0, m.start() - 15):m.start()].lower()
        for p, v in zip(("length_mm", "width_mm", "height_mm"), m.groups()):
            v = float(v)
            if "min" in ctx:
                lo, hi = v, None
            elif "max" in ctx:
                lo, hi = None, v
            else:  # wymiar "dokładny" – przyjmujemy tolerancję (domyślnie 5%)
                t = tol or 0.05
                lo, hi = round(v * (1 - t)), round(v * (1 + t))
            reqs.append({"param": p, "min": lo, "max": hi, "target": v, "raw": m.group(0)})

    # dzielimy na klauzule; część po "Dodatkowo" / "Przyczepa" dotyczy osprzętu
    clauses = re.split(r"[;,\n]|\.\s(?=[A-ZŁŚŻ])", main)
    last_param = None
    for cl in clauses:
        cl = cl.strip()
        if not cl:
            continue
        if ACCESSORY_RX.search(cl) and not re.search(r"wyci[ąa]gark", cl, re.I):
            continue
        hit = None
        for p, (name_rx, unit_rx, target) in PARAMS.items():
            um = re.search(NUM + r"\s?(" + unit_rx + r")", cl, re.I)
            if not um:
                continue
            if re.search(name_rx, cl, re.I) or (p == "power_hp") or (p == "engine_cc"):
                # moc i pojemność rozpoznajemy po jednostce (KM/kW, ccm)
                if p == "power_hp" and not re.search(r"KM\b|kW\b|HP\b", cl):
                    continue
                if p == "engine_cc" and not re.search(unit_rx, cl, re.I):
                    continue
                hit = (p, target, unit_rx)
                break
        if not hit:
            continue
        p, target, unit_rx = hit
        reqs.append(_extract(cl, p, target, unit_rx))
        # pojemność i moc mają jednoznaczne jednostki – mogą wystąpić w tej samej klauzuli
        for extra in ("engine_cc", "power_hp"):
            _, unit_rx2, target2 = PARAMS[extra]
            if extra != p and re.search(NUM + r"\s?(" + unit_rx2 + r")", cl, re.I):
                reqs.append(_extract(cl, extra, target2, unit_rx2))

    s = _seats(main)
    if s:
        reqs.append(s)

    flags = {k: bool(re.search(rx, text, re.I)) for k, rx in FLAGS.items()}
    # deduplikacja: zostaw najostrzejsze
    best: dict[tuple, dict] = {}
    for r in reqs:
        key = (r["param"], r["min"] is not None, r["max"] is not None)
        if key not in best:
            best[key] = r
        else:
            o = best[key]
            if r["min"] is not None and (o["min"] is None or r["min"] > o["min"]):
                best[key] = r
            elif r["max"] is not None and (o["max"] is None or r["max"] < o["max"]):
                best[key] = r
    return {"reqs": list(best.values()), "flags": flags}


LABELS = {
    "engine_cc": "Pojemność [ccm]", "power_hp": "Moc [KM]", "clearance_mm": "Prześwit [mm]",
    "payload_kg": "Ładowność [kg]", "towing_kg": "Uciąg [kg]", "winch_lbs": "Wyciągarka [lbs]",
    "length_mm": "Długość [mm]", "width_mm": "Szerokość [mm]", "height_mm": "Wysokość [mm]",
    "wheelbase_mm": "Rozstaw osi [mm]", "dry_weight_kg": "Masa własna [kg]",
    "fuel_l": "Zbiornik [L]", "susp_travel_mm": "Skok zawieszenia [mm]",
    "speed_kmh": "Prędkość [km/h]", "seats": "Liczba miejsc",
}
