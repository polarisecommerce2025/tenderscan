"""Rozpoznawanie, czy ogłoszenie dotyczy ATV/UTV, oraz wydobycie danych z htmlBody."""
from __future__ import annotations

import html as htmllib
import re

# --- słowa kluczowe -------------------------------------------------------
ATV_RX = re.compile(
    r"(?<![A-Za-z])ATV(?![A-Za-z])|\bquad(?:a|y|em|ów|ami)?\b|czterokołowiec|pojazd\w* czterokołow\w*|\bL7e\b", re.I)
UTV_RX = re.compile(
    r"(?<![A-Za-z])UTV(?![A-Za-z])|\bSSV\b|side[\s-]?by[\s-]?side|pojazd\w* (?:typu )?ewakuacyjn\w* typu UTV", re.I)
GENERIC_RX = re.compile(
    r"pojazd\w* (?:typu )?(?:terenow|wielozadaniow|gąsienicow)\w*"
    r"|\bT3b\b|ciągnik\w* rolnicz\w* (?:typu|kategorii) T3", re.I)
EXCLUDE_RX = re.compile(
    r"quad[\s-]?core|czterordzeniow|quadro|quadrat|kwadrat|ATV-?\d{3}\b", re.I)

# CPV traktowane jako sygnał pomocniczy (same nie wystarczą)
CPV_HINTS = ("34113300", "34144700", "34144900", "16700000")   # terenowe, użytkowe, elektryczne, ciągniki


def html_to_text(body: str | None) -> str:
    if not body:
        return ""
    body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = htmllib.unescape(body)
    body = re.sub(r"[ \t\xa0]+", " ", body)
    return re.sub(r"\n\s*\n+", "\n", body).strip()


def classify(order_object: str, cpv: str, text: str) -> dict:
    """Zwraca {'relevance': 0-100, 'vehicle_type': ATV/UTV/Łączone/?, 'why': [...]}"""
    oo = order_object or ""
    why, score = [], 0
    clean_oo = EXCLUDE_RX.sub(" ", oo)
    clean_tx = EXCLUDE_RX.sub(" ", text or "")

    atv_t, utv_t = bool(ATV_RX.search(clean_oo)), bool(UTV_RX.search(clean_oo))
    atv_b, utv_b = bool(ATV_RX.search(clean_tx)), bool(UTV_RX.search(clean_tx))
    if atv_t or utv_t:
        score = 100; why.append("ATV/UTV w nazwie zamówienia")
    elif atv_b or utv_b:
        score = 70; why.append("ATV/UTV w treści ogłoszenia")
    generic = bool(GENERIC_RX.search(clean_oo) or GENERIC_RX.search(clean_tx))
    cpv_hit = any(c in (cpv or "") for c in CPV_HINTS)
    if generic:
        why.append("pojazd terenowy/wielozadaniowy")
        score = max(score, 50 if cpv_hit else 30)
    if cpv_hit:
        why.append("CPV pojazd terenowy/użytkowy")
        score = max(score, 20)

    atv, utv = atv_t or atv_b, utv_t or utv_b
    vtype = "Łączone" if atv and utv else "ATV" if atv else "UTV" if utv else "?"
    return {"relevance": score, "vehicle_type": vtype, "why": why}


PLATFORM_RX = re.compile(
    r"(?:Adres strony internetowej prowadzonego postępowania|adres strony internetowej,? na której)"
    r"[^h]{0,120}(https?://\S+)", re.I)


def platform_url(text: str) -> str | None:
    m = PLATFORM_RX.search(text or "")
    return m.group(1).rstrip(".,;)") if m else None


def find_platform(url: str | None) -> str:
    if not url:
        return "e-Zamówienia"
    for key, name in (("platformazakupowa", "platformazakupowa.pl"),
                      ("ezamawiajacy", "eZamawiający (Marketplanet)"),
                      ("logintrade", "Logintrade"), ("proebiz", "Josephine/Proebiz"),
                      ("ezamowienia.gov.pl", "e-Zamówienia"), ("smartpzp", "SmartPZP"),
                      ("e-propublico", "e-ProPublico"), ("#/offer/", "platforma własna (SmartPZP)")):
        if key in url:
            return name
    return "inna"


DEADLINE_RX = re.compile(r"Termin składania ofert[^0-9]{0,40}(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", re.I)
VALUE_RX = re.compile(r"(?:Wartość umowy|Cena lub koszt oferty wykonawcy)[^0-9]{0,60}([\d\s]+[,.]\d{2})\s*PLN", re.I)
WADIUM_RX = re.compile(r"wymaga wniesienia wadium:?\s*(Tak|Nie)", re.I)


def extract_values(text: str) -> list[float]:
    out = []
    for m in VALUE_RX.finditer(text or ""):
        try:
            out.append(float(m.group(1).replace(" ", "").replace(",", ".")))
        except ValueError:
            pass
    return out
