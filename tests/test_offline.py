"""Testy offline (bez sieci): klasyfikacja, parser, mapowanie ogłoszenia BZP, bisekcja klienta."""
from datetime import datetime
from unittest import mock

from tenderscan import bzp_client
from tenderscan.classify import classify
from tenderscan.matcher import load_catalog
from tenderscan.pipeline import analyse, from_bzp

SAMPLE = {
    "noticeType": "ContractNotice", "noticeNumber": "2026/BZP 00999999/01", "bzpNumber": "2026/BZP 00999999",
    "publicationDate": "2026-11-17T08:00:00Z", "submittingOffersDate": "2026-11-27T10:00:00Z",
    "orderObject": "Zakup pojazdu typu UTV wraz z pługiem", "cpvCode": "34144700-5 (Pojazdy użytkowe)",
    "organizationName": "Gmina Testowa", "organizationCity": "Testów", "organizationProvince": "PL24",
    "organizationNationalId": "1234567890", "tenderId": "ocds-148610-test", "objectId": "abc",
    "htmlBody": "<p>Adres strony internetowej prowadzonego postępowania: https://platformazakupowa.pl/transakcja/1</p>"
                "<p>Pojazd UTV 3-osobowy, napęd 4x4, pojemność skokowa silnika nie mniej niż 950 cm3, "
                "moc min. 60 kW, prześwit co najmniej 300 mm, ładowność skrzyni min. 450 kg; wyciągarka min. 4500 lbs.</p>",
    "contractors": None,
}


def test_classify():
    assert classify("Zakup quada", "", "")["vehicle_type"] == "ATV"
    assert classify("Dostawa procesorów quad-core", "", "")["relevance"] == 0
    assert classify("Zakup pojazdu do ewakuacji ATV_UTV", "", "")["vehicle_type"] == "Łączone"
    assert classify("Zakup beczki asenizacyjnej", "", "")["relevance"] == 0


def test_bzp_record():
    cat = load_catalog("seed/catalog.json")
    r = analyse(from_bzp(SAMPLE), cat)
    assert r["province"] == "Śląskie" and r["platform"] == "platformazakupowa.pl"
    params = {q["param"]: q for q in r["reqs"]["reqs"]}
    assert params["engine_cc"]["min"] == 950 and params["power_hp"]["min"] > 80
    assert r["matches"][0]["model"] == "Ranger XP 1000", r["matches"][0]


def test_bisection():
    c = bzp_client.BzpClient(page_size=2, pause_s=0)
    calls = []

    def fake(params):
        calls.append(params)
        return [{"n": 1}, {"n": 2}] if len(calls) == 1 else [{"n": len(calls)}]
    with mock.patch.object(c, "_get", side_effect=fake):
        out = list(c.fetch_window("ContractNotice", datetime(2026, 11, 1), datetime(2026, 11, 1, 23, 59)))
    assert len(calls) == 3 and len(out) == 2


if __name__ == "__main__":
    test_classify(); test_bzp_record(); test_bisection(); print("OK – wszystkie testy przeszły")
