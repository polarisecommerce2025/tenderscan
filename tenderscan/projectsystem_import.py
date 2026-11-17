"""Import danych z obecnych plików Projectsystem (systemmt.xlsb, systempz.xlsx).

Pozwala przenieść historię (oferty, wyniki, unieważnienia, plany) do nowej bazy
i porównać wyniki prototypu z tym, co dostarcza Projectsystem.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def _xl_date(v):
    if isinstance(v, (int, float)):
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y-%m-%d %H:%M")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    return v


SHEET_KIND = {"Zamowienia": "open", "Oferty": "offers", "Rozstrzygniete": "result",
              "Uniewaznione": "cancelled"}


def read_systemmt(path: str) -> list[dict]:
    from pyxlsb import open_workbook
    out = []
    with open_workbook(path) as wb:
        for sheet, kind in SHEET_KIND.items():
            if sheet not in wb.sheets:
                continue
            with wb.get_sheet(sheet) as sh:
                rows = [[c.v for c in r] for r in sh.rows()]
            hdr = rows[1]
            for r in rows[2:]:
                d = {h: v for h, v in zip(hdr, r) if h}
                if not d.get("BZP"):
                    continue
                offers = [(d.get(f"Oferent {i}"), d.get(f"Oferta {i}")) for i in range(1, 9)
                          if d.get(f"Oferent {i}")]
                out.append({
                    "id": f"{d['BZP']} cz.{int(d.get('Cz') or 1)}", "kind": kind, "source": "Projectsystem",
                    "published": _xl_date(d.get("Data Publikacji")),
                    "deadline": _xl_date(d.get("Data Skladania")),
                    "buyer": d.get("Zamawiający"), "buyer_nip": str(d.get("NIP") or "").strip(),
                    "city": d.get("Miasto"), "province": d.get("Wojewodztwo"),
                    "title": d.get("Przedmiot Zam") or "", "cpv": "",
                    "vehicle_type": d.get("Typ Pojazdu"),
                    "qty": d.get("Ilosc"), "option": d.get("Opcja"),
                    "link": d.get("Lokalizacja"), "platform_link": d.get("Lokalizacja"),
                    "description": d.get("Komentarz") or "",
                    "winners": [d["Wykonawca"]] if d.get("Wykonawca") else [],
                    "value": d.get("Wartosc"), "budget": d.get("Budżet"),
                    "status": d.get("Stan Postępowania") or kind,
                    "cancel_reason": d.get("Przyczyna Unieważnienia"),
                    "offers": [{"name": n, "price": p} for n, p in offers],
                    "criteria": d.get("Waga"), "delivery": d.get("Termin Dostawy"),
                    "wadium": d.get("Wadium"), "ref": d.get("Nr Ref."),
                })
    # unikalność: ten sam BZP/część może być w kilku arkuszach -> łączymy
    merged: dict[str, dict] = {}
    for r in out:
        m = merged.setdefault(r["id"], r)
        if m is not r:
            for k, v in r.items():
                if v and not m.get(k):
                    m[k] = v
            if r["kind"] == "offers":
                m["offers"] = r["offers"] or m.get("offers")
            prio = ["open", "offers", "cancelled", "result"]
            if prio.index(r["kind"]) > prio.index(m["kind"]):
                m["kind"] = r["kind"]; m["status"] = r["status"]
    return list(merged.values())


def read_systempz(path: str) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Baza Danych"] if "Baza Danych" in wb.sheetnames else wb["Plany Zamówień"]
    rows = list(ws.iter_rows(values_only=True))
    start = 0 if rows[0][0] == "NIP" else 1
    hdr = rows[start]
    out = []
    for r in rows[start + 1:]:
        d = dict(zip(hdr, r))
        if not d.get("BZP"):
            continue
        out.append({
            "id": f"{d['BZP']} poz.{d.get('Pozycja')}", "kind": "plan", "source": "Projectsystem",
            "published": _xl_date(d.get("Data Publikacji")), "deadline": d.get("Kwartał"),
            "buyer": d.get("Zamawiający"), "buyer_nip": d.get("NIP"), "city": d.get("Miasto"),
            "province": d.get("Województwo"), "title": d.get("Opis") or "", "cpv": "",
            "vehicle_type": d.get("Typoszereg") if d.get("Typoszereg") in ("ATV", "UTV") else "?",
            "link": d.get("Link"), "description": "", "winners": [], "value": None,
            "budget": d.get("Budżet"), "status": d.get("Informacje Dodatkowe") or "plan",
            "contact": d.get("Mail"),
        })
    return out
