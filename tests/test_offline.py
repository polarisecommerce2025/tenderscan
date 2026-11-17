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


def test_paging():
    """API: From = dolna granica, To ignorowane, max 500, kolejne strony przez SearchAfter."""
    c = bzp_client.BzpClient(page_size=2, pause_s=0)
    data = [{"objectId": f"o{i}", "publicationDate": f"2026-11-01T0{i}:00:00Z"} for i in range(7)]
    calls = []

    def fake(params):
        calls.append(params)
        start = 0
        if "SearchAfter" in params:
            start = [d["objectId"] for d in data].index(params["SearchAfter"]) + 1
        return data[start:start + 2]
    with mock.patch.object(c, "_get", side_effect=fake):
        out = list(c.fetch_range("ContractNotice", datetime(2026, 11, 1), datetime(2026, 11, 1, 4, 30)))
    assert [o["objectId"] for o in out] == ["o0", "o1", "o2", "o3", "o4"], out
    assert len(calls) == 3


def test_false_positives():
    for t, body, cpv in [("Dostawa sprzętu rehabilitacyjnego", "Podpórka czterokołowa (rollator)", "33193120"),
                         ("Zakup pojazdu użytkowego (segment LCV)", "", "34130000"),
                         ("Lekki pojazd ratowniczo-gaśniczy", "", "34144200"),
                         ("Zakup kosiarki wrzecionowej", "kosiarka samojezdna, czterokołowa", "16311100")]:
        assert classify(t, cpv, body)["relevance"] < 50, t
    assert classify("Dostawa pojazdu typu UTV i czterech pojazdów typu ATV", "34113300-5", "")["relevance"] == 100
    assert classify("Zakup pojazdu terenowego", "34113300-5 (Pojazdy terenowe)", "")["relevance"] >= 50


if __name__ == "__main__":
    test_classify(); test_bzp_record(); test_paging(); test_false_positives(); print("OK – wszystkie testy przeszły")
