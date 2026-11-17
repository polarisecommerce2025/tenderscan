"""Klient publicznego API Biuletynu Zamówień Publicznych (e-Zamówienia).

Endpoint (zweryfikowany 17.11.2026):
    GET https://ezamowienia.gov.pl/mo-board/api/v1/notice
Parametry: NoticeType, OrderType, PublicationDateFrom, PublicationDateTo, PageSize
API nie wymaga klucza. Nie ma udokumentowanego stronicowania, więc gdy wynik
jest "pełny" (== PageSize), okno czasowe jest dzielone na pół (bisekcja).
Bez zależności zewnętrznych (urllib).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Iterator

import os
BASE_URL = os.environ.get("TS_BZP_URL", "https://ezamowienia.gov.pl/mo-board/api/v1/notice")
NOTICE_URL = "https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/{objectId}"
TENDER_URL = "https://ezamowienia.gov.pl/mp-client/search/list/{tenderId}"

CONTRACT_NOTICE = "ContractNotice"          # ogłoszenie o zamówieniu -> nowy przetarg
RESULT_NOTICE = "TenderResultNotice"        # ogłoszenie o wyniku -> kto wygrał

PROVINCES = {
    "PL02": "Dolnośląskie", "PL04": "Kujawsko-Pomorskie", "PL06": "Lubelskie",
    "PL08": "Lubuskie", "PL10": "Łódzkie", "PL12": "Małopolskie",
    "PL14": "Mazowieckie", "PL16": "Opolskie", "PL18": "Podkarpackie",
    "PL20": "Podlaskie", "PL22": "Pomorskie", "PL24": "Śląskie",
    "PL26": "Świętokrzyskie", "PL28": "Warmińsko-Mazurskie",
    "PL30": "Wielkopolskie", "PL32": "Zachodniopomorskie",
}


class BzpClient:
    def __init__(self, page_size: int = 500, pause_s: float = 0.5, timeout_s: int = 120, log=None):
        self.log = log
        self.page_size = page_size
        self.pause_s = pause_s
        self.timeout_s = timeout_s
        self.requests = 0

    def _get(self, params: dict) -> list[dict]:
        url = BASE_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": "TenderScan/1.0 (monitoring przetargow ATV/UTV)",
            "Accept": "application/json"})
        for attempt in range(4):
            try:
                self.requests += 1
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    data = json.loads(r.read().decode("utf-8") or "[]")
                    return data if isinstance(data, list) else data.get("items", []) or []
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        return []

    def fetch_range(self, notice_type: str, date_from: datetime, date_to: datetime,
                    order_type: str | None = None) -> Iterator[dict]:
        """Pobiera wszystkie ogłoszenia z zakresu dat.

        Zachowanie API (sprawdzone 17.11.2026): PublicationDateFrom działa jako dolna granica,
        PublicationDateTo jest ignorowane, wynik jest posortowany rosnąco po dacie publikacji
        i ucięty do PageSize (max 500). Kolejne strony: SearchAfter=<objectId ostatniego rekordu>.
        OrderType również jest ignorowany – filtrujemy po stronie klienta.
        """
        to_iso = date_to.strftime("%Y-%m-%dT%H:%M:%S")
        after, n, pages = None, 0, 0
        while True:
            params = {"NoticeType": notice_type,
                      "PublicationDateFrom": date_from.strftime("%Y-%m-%dT%H:%M:%S"),
                      "PublicationDateTo": to_iso,   # wymagane przez API, choć nie ogranicza wyniku
                      "PageSize": self.page_size}
            if after:
                params["SearchAfter"] = after
            items = self._get(params)
            pages += 1
            time.sleep(self.pause_s)
            if not items:
                break
            stop = False
            for it in items:
                if (it.get("publicationDate") or "")[:19] > to_iso:
                    stop = True
                    break
                if order_type and it.get("orderType") not in (None, order_type):
                    continue
                n += 1
                yield it
            if self.log and pages % 4 == 0:
                self.log(f"  {notice_type}: {n} ogłoszeń, doszedłem do {(items[-1].get('publicationDate') or '')[:16]}")
            if stop or len(items) < self.page_size:
                break
            new_after = items[-1].get("objectId")
            if not new_after or new_after == after:
                break
            after = new_after
        if self.log:
            self.log(f"  {notice_type}: razem {n} ogłoszeń ({pages} zapytań)")


def notice_link(n: dict) -> str:
    if n.get("objectId"):
        return NOTICE_URL.format(objectId=n["objectId"])
    return TENDER_URL.format(tenderId=n.get("tenderId", ""))
