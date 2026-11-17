# TenderScan – monitoring przetargów ATV/UTV

Prototyp zastępujący pliki Projectsystem (`systemmt.xlsb`, `systempz.xlsx`): sam pobiera
ogłoszenia z Biuletynu Zamówień Publicznych, wybiera te dotyczące ATV/UTV, wyciąga z nich
wymagania techniczne i porównuje je z katalogiem naszych modeli.

## 1. Co robią obecne pliki Excel (analiza)

| Element | Ustalenie |
|---|---|
| Dostawca | Projectsystem Sp. z o.o. (projectsystem.one), licencja na użytkownika |
| Mechanizm | Power Query (bez makr VBA). Arkusze są chronione hasłem (struktura skoroszytu) |
| Źródło danych | `ftp://31.0.213.202/projectsystemnet/` – serwer dostawcy |
| Licencja/filtry | `VDT` czyta lokalny plik `sa_ca707.json` (obok skoroszytu) → `VTV` pobiera z FTP JSON z listami: `OT, VT, CT, RT, BM, NT, DLN, NZW, OFT` (systemmt) oraz `RTPZ, CTPZ, NTPZ, BZPPZ, QTRPZ, YPZ, BMPZ, VTPZ` (systempz) – województwa, typy pojazdów, wykluczone miasta/NIP-y itd. |
| Status konta | `sa_ac910` (FTP) – rekord włączający/wyłączający dostęp (kolumna DLN) |
| systemmt.xlsb | tabela `api_PS_szablon` (FTP) → zapytania `Zamowienia`, `Wybrane`, `Oferty`, `Rozstrzygniete`, `Uniewaznione`, `Realizacje`; zakładki odświeżane przy otwarciu (`refreshOnLoad`) |
| systempz.xlsx | tabela `api_URL_dane` (FTP) → plany postępowań (`sa_dane_pz`), pole wyboru WYB + notatki |
| Własne dane użytkownika | `ZamowieniaMK`, `WybraneMK`, `RealizacjeDT`, `sa_dane_pz_note_cp` – samoodwołujące się tabele zachowujące wybór, handlowca, notatki, daty umów |
| Kluczowy wniosek | **Excel niczego nie skanuje.** Pobiera gotową bazę, którą dostawca opracowuje (częściowo ręcznie – np. skrócone opisy „Silnik Pb min.995ccm, min.80KM…”, kategorie QUAD/ZESTAW, ceny z otwarcia ofert). |

### Źródła pierwotne (z linków w danych)
- **ezamowienia.gov.pl / BZP** – 40 z 71 linków; ogłoszenia, wyniki, plany postępowań. **Publiczne API bez klucza** – zweryfikowane 17.11.2026 (`/mo-board/api/v1/notice`).
- **platformazakupowa.pl** (13), ***.ezamawiajacy.pl** (6), **pzp.dubiecko.pl / zamowienia.gminaludwin.pl** (6), **logintrade.net** (2), **josephine.proebiz.com** (2) – platformy, gdzie leżą SWZ/OPZ i informacje z otwarcia ofert.
- **TED** – przetargi powyżej progów UE (np. `2026/S 193-688826`).

## 2. Jak działa prototyp

```
API BZP ──► bzp_client.py ──► classify.py ──► spec_parser.py ──► matcher.py ──► SQLite ──► dashboard.html
(ogłoszenia + wyniki)   (ATV/UTV? trafność)  (ccm, KM, wymiary…)  (vs catalog.json)
```

- `bzp_client.py` – pobieranie dzień po dniu, `ContractNotice` + `TenderResultNotice`, typ `Delivery`; przy pełnej stronie dzieli okno czasu na pół.
- `classify.py` – słowa kluczowe (ATV, UTV, quad, SSV, side-by-side, L7e, T3b…), wykluczenia (quad-core…), CPV pomocniczo; adres platformy z treści ogłoszenia.
- `spec_parser.py` – wymagania min/max: pojemność, moc (kW→KM), prześwit, ładowność, uciąg, wyciągarka (kg→lbs), wymiary (także `AxBxC ±5%`), masa, zbiornik, skok zawieszenia, miejsca + cechy (4x4, 6x6, CVT, T3b, L7e, blokada, kabina…). Ignoruje osprzęt (pług, przyczepa…).
- `matcher.py` – ocena każdego modelu: SPEŁNIA / BLISKO (1 rozbieżność) / NIE SPEŁNIA + lista rozbieżności.
- `projectsystem_import.py` – import obecnych plików (historia ofert, wyników, planów).

## 3. Uruchomienie

### Serwer (Docker)
`docker-compose.yml` uruchamia kontener `python:3.12-slim`, który przy każdym starcie pobiera aktualny kod z tego repo
(bez dodatkowych bibliotek – sama biblioteka standardowa Pythona). Dane (SQLite, katalog modeli) leżą w wolumenie `tenderscan_data`.

```bash
TS_PASS='silne-haslo' docker compose up -d     # panel: http://<serwer>:18765  (login: admin)
```
Aktualizacja: `git push` do `main`, potem restart kontenera.

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| TS_PASS / TS_USER | – / admin | logowanie do panelu |
| TS_INTERVAL_H | 6 | co ile godzin skan BZP |
| TS_BACKFILL_DAYS | 30 | ile dni wstecz przy pierwszym starcie |
| TS_MIN_RELEVANCE | 50 | próg trafności (0–100) |

### Lokalnie
```bash
TS_PASS=test python -m tenderscan.server        # http://localhost:8000
PYTHONPATH=. python tests/test_offline.py        # testy
```
Historię z plików Projectsystem wgrywa się w panelu: „Skany i log” → Import historii (plik `history.json`
generowany przez `tenderscan/projectsystem_import.py`; wymaga `pyxlsb`, `openpyxl`). Dane te nie są trzymane w repozytorium.

### Panel
Przegląd · Obserwowane (gwiazdka + notatka handlowca) · Nowe przetargi · Otwarcie ofert · Rozstrzygnięte · Unieważnione ·
Plany · Konkurencja · Nasz katalog (edycja JSON → automatyczne przeliczenie dopasowań) · Skany i log (przycisk „Skanuj teraz”).

## 4. Ograniczenia prototypu / następne kroki
1. **Katalog `data/catalog.json` jest przykładowy** – trzeba wpisać dane z kart technicznych (Polaris, TGB, ZXMOTO…).
2. Ogłoszenie BZP często ma tylko ogólny opis – pełne wymagania są w SWZ/OPZ na platformie. Następny moduł: pobieranie załączników (platformazakupowa.pl, ezamawiajacy.pl) i parsowanie PDF/DOCX, ewentualnie z LLM do trudnych opisów.
3. **Plany postępowań** – w API BZP nie znaleziono jeszcze publicznego endpointu dla planów; do ustalenia (alternatywnie wyszukiwarka BZP lub pliki CSV/XML planów).
4. **Wyniki/ceny ofert** – ogłoszenie o wyniku daje zwycięzcę; ceny wszystkich ofert są w informacji z otwarcia ofert na platformach (scraper do dopisania).
5. TED (przetargi unijne) – dodać klienta API TED.
6. Rekordy z importu Projectsystem i z BZP mają różne identyfikatory (cz./pozycja vs nr ogłoszenia) – do ujednolicenia.
7. Powiadomienia (e-mail/Teams) o nowych przetargach z oceną SPEŁNIA/BLISKO.
