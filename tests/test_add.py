"""POST /add: accepted, replayed, refused (docs/spec/endpoints.md, canonical.md, annule.md)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from luca import ledger

from .conftest import SOCIETE, codes, document, messages, rows

# --- accepted -------------------------------------------------------------------


def test_add_records_the_ecriture_numbered_and_dated(books: httpx.Client) -> None:
    response = books.post("/add", json=document())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["societe"] == SOCIETE
    assert body["replay"] is False
    assert body["ecriture"] == {
        "id": 1,
        "journal": "VE",
        "num": 1,
        "date": "2025-01-15",
        "piece": {"ref": "F2025-001", "date": "2025-01-15"},
        "lib": "Facture F2025-001",
        "valid_date": ledger.today(),
        "request_id": "2025-01-15-F2025-001",
        "annule": None,
        "lignes": [
            {"compte": "411000", "lib": None, "debit": "1200.00"},
            {"compte": "706000", "lib": None, "credit": "1000.00"},
            {"compte": "445710", "lib": "TVA 20 %", "credit": "200.00"},
        ],
    }
    assert rows(books, "SELECT idx, compte, lib, debit, credit FROM ligne ORDER BY idx") == [
        [0, "411000", None, 120000, 0],
        [1, "706000", None, 0, 100000],
        [2, "445710", "TVA 20 %", 0, 20000],
    ]


def test_add_numbers_continuously_per_journal(books: httpx.Client) -> None:
    first = books.post("/add", json=document(request_id="a")).json()["ecriture"]
    second = books.post("/add", json=document(request_id="b")).json()["ecriture"]
    other = books.post("/add", json=document(request_id="c", journal="AC")).json()["ecriture"]
    assert (first["num"], second["num"], other["num"]) == (1, 2, 1)
    assert len({first["id"], second["id"], other["id"]}) == 3


@pytest.mark.parametrize(
    ("amount", "stored", "returned"),
    [("1200", 120000, "1200.00"), ("1200.5", 120050, "1200.50"), ("0.01", 1, "0.01")],
)
def test_add_accepts_every_spelling_of_a_decimal_string(
    books: httpx.Client, amount: str, stored: int, returned: str
) -> None:
    lignes = [{"compte": "411000", "debit": amount}, {"compte": "706000", "credit": amount}]
    response = books.post("/add", json=document(lignes=lignes))
    assert response.status_code == 200, response.text
    assert response.json()["ecriture"]["lignes"][0]["debit"] == returned
    assert rows(books, "SELECT debit FROM ligne WHERE idx = 0") == [[stored]]


def test_add_accepts_the_largest_amount_sqlite_stores(books: httpx.Client) -> None:
    amount = "92233720368547758.07"
    lignes = [{"compte": "411000", "debit": amount}, {"compte": "706000", "credit": amount}]
    response = books.post("/add", json=document(lignes=lignes))
    assert response.status_code == 200, response.text
    assert rows(books, "SELECT debit FROM ligne WHERE idx = 0") == [[2**63 - 1]]


def test_two_concurrent_adds_get_distinct_nums(books: httpx.Client, url: str) -> None:
    count = 20
    barrier = threading.Barrier(count)
    results: list[Any] = []

    def add(i: int) -> None:
        with httpx.Client(base_url=url) as client:
            barrier.wait()
            results.append(client.post("/add", json=document(request_id=f"r{i}")).json())

    threads = [threading.Thread(target=add, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all("ecriture" in result for result in results), results
    assert sorted(result["ecriture"]["num"] for result in results) == list(range(1, count + 1))
    assert rows(books, "SELECT count(*) FROM ecriture") == [[count]]


# --- replay and conflict --------------------------------------------------------


def test_add_replays_a_reserialised_request_without_writing(books: httpx.Client) -> None:
    first = books.post("/add", json=document()).json()
    reserialised = json.dumps(document(), indent=2, sort_keys=True).replace('"1200.00"', '"1200"')
    again = books.post("/add", content=reserialised.encode())
    assert again.status_code == 200, again.text
    assert again.json() == {**first, "replay": True}
    assert rows(books, "SELECT count(*) FROM ecriture") == [[1]]


def test_add_refuses_the_same_request_id_with_different_content(books: httpx.Client) -> None:
    first = books.post("/add", json=document()).json()["ecriture"]
    response = books.post("/add", json=document(lib="Autre libellé"))
    assert codes(response) == ["REQUEST_ID_CONFLICT"]
    error = response.json()["errors"][0]
    assert "2025-01-15-F2025-001" in error["message"]
    assert error["ecriture"] == first
    assert rows(books, "SELECT count(*) FROM ecriture") == [[1]]


def test_a_different_label_spacing_is_different_content(books: httpx.Client) -> None:
    books.post("/add", json=document())
    assert codes(books.post("/add", json=document(lib="Facture  F2025-001"))) == [
        "REQUEST_ID_CONFLICT"
    ]


# --- the document ---------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [b"", b"{", b"[]", b'"text"', b"null", b'{"request_id": "a", "request_id": "b"}', b"\xff\xfe"],
)
def test_add_refuses_what_is_not_a_json_object(books: httpx.Client, data: bytes) -> None:
    assert codes(books.post("/add", content=data)) == ["INVALID_JSON"]


def test_add_refuses_nan(books: httpx.Client) -> None:
    data = json.dumps(document()).replace('"1200.00"', "NaN").encode()
    response = books.post("/add", content=data)
    assert codes(response) == ["INVALID_JSON"]
    assert "NaN" in messages(response)[0]


def test_add_refuses_unknown_and_missing_keys_together(books: httpx.Client) -> None:
    doc = document(note="hello")
    del doc["lib"]
    response = books.post("/add", json=doc)
    assert codes(response) == ["INVALID_SHAPE", "INVALID_SHAPE"]
    assert messages(response) == ["note: unknown key", "lib: missing"]


@pytest.mark.parametrize("key", ["request_id", "journal", "lib"])
@pytest.mark.parametrize("value", ["", "  ", 12, None, ["x"]])
def test_add_refuses_blank_or_non_string_fields(books: httpx.Client, key: str, value: Any) -> None:
    response = books.post("/add", json=document(**{key: value}))
    assert codes(response) == ["INVALID_SHAPE"]
    assert messages(response) == [f"{key}: not a non-empty string"]


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("15/01/2025", "'15/01/2025' is not a date (YYYY-MM-DD)"),
        ("2025-02-30", "'2025-02-30' is not a calendar date"),
        (20250115, "not a string"),
    ],
)
def test_add_refuses_malformed_dates(books: httpx.Client, value: Any, reason: str) -> None:
    assert messages(books.post("/add", json=document(date=value))) == [f"date: {reason}"]
    piece = {"ref": "F1", "date": value}
    assert messages(books.post("/add", json=document(piece=piece))) == [f"piece.date: {reason}"]


def test_add_refuses_a_malformed_piece(books: httpx.Client) -> None:
    assert messages(books.post("/add", json=document(piece="F1"))) == ["piece: not a JSON object"]
    assert messages(books.post("/add", json=document(piece={"ref": "F1", "x": 1}))) == [
        "piece.x: unknown key",
        "piece.date: missing",
    ]


def test_add_refuses_lignes_that_are_not_an_array(books: httpx.Client) -> None:
    assert messages(books.post("/add", json=document(lignes={"compte": "411000"}))) == [
        "lignes: not a JSON array"
    ]


def test_add_refuses_fewer_than_two_lignes(books: httpx.Client) -> None:
    assert messages(books.post("/add", json=document(lignes=[]))) == ["lignes: at least two lignes"]
    one = [{"compte": "411000", "debit": "1"}]
    assert messages(books.post("/add", json=document(lignes=one))) == [
        "lignes: at least two lignes"
    ]


@pytest.mark.parametrize(
    ("ligne", "reason"),
    [
        ("text", "lignes[0]: not a JSON object"),
        ({"debit": "1"}, "lignes[0].compte: missing"),
        ({"compte": "411000", "debit": "1", "x": 1}, "lignes[0].x: unknown key"),
        ({"compte": "", "debit": "1"}, "lignes[0].compte: not a non-empty string"),
        ({"compte": "411000", "lib": "", "debit": "1"}, "lignes[0].lib: not a non-empty string"),
        ({"compte": "411000"}, "lignes[0]: exactly one of debit or credit"),
        (
            {"compte": "411000", "debit": "1", "credit": "1"},
            "lignes[0]: exactly one of debit or credit",
        ),
    ],
)
def test_add_refuses_malformed_lignes(books: httpx.Client, ligne: Any, reason: str) -> None:
    lignes = [ligne, {"compte": "706000", "credit": "1"}]
    assert messages(books.post("/add", json=document(lignes=lignes))) == [reason]


@pytest.mark.parametrize(
    ("amount", "reason"),
    [
        (1200, "not a decimal string"),
        (1200.5, "not a decimal string"),
        (True, "not a decimal string"),
        (None, "not a decimal string"),
        ("0", "'0' is not strictly positive"),
        ("0.00", "'0.00' is not strictly positive"),
        ("-1", "'-1' is not a decimal amount with at most two decimals"),
        ("1.005", "'1.005' is not a decimal amount with at most two decimals"),
        ("1,5", "'1,5' is not a decimal amount with at most two decimals"),
        (" 12", "' 12' is not a decimal amount with at most two decimals"),
        ("1e3", "'1e3' is not a decimal amount with at most two decimals"),
        ("abc", "'abc' is not a decimal amount with at most two decimals"),
        ("92233720368547758.08", "'92233720368547758.08' is larger than 92233720368547758.07"),
    ],
)
def test_add_refuses_bad_amounts(books: httpx.Client, amount: Any, reason: str) -> None:
    lignes = [{"compte": "411000", "debit": amount}, {"compte": "706000", "credit": "1"}]
    response = books.post("/add", json=document(lignes=lignes))
    assert codes(response) == ["INVALID_SHAPE"]
    assert messages(response) == [f"lignes[0].debit: {reason}"]


def test_add_refuses_an_unbalanced_ecriture(books: httpx.Client) -> None:
    lignes = [{"compte": "411000", "debit": "1200.00"}, {"compte": "706000", "credit": "1000.00"}]
    response = books.post("/add", json=document(lignes=lignes))
    assert codes(response) == ["UNBALANCED"]
    assert messages(response) == ["debits 1200.00 and credits 1000.00 differ"]


def test_add_reports_every_shape_error_at_once(books: httpx.Client) -> None:
    lignes = [{"compte": "411000", "debit": "x"}, {"compte": "706000", "credit": "-1"}]
    response = books.post("/add", json=document(journal="", date="bad", lignes=lignes))
    assert messages(response) == [
        "journal: not a non-empty string",
        "date: 'bad' is not a date (YYYY-MM-DD)",
        "lignes[0].debit: 'x' is not a decimal amount with at most two decimals",
        "lignes[1].credit: '-1' is not a decimal amount with at most two decimals",
    ]


# --- against the store ----------------------------------------------------------


def test_the_first_add_of_a_new_societe_is_refused_with_no_exercice(http: httpx.Client) -> None:
    response = http.post("/add", json=document())
    assert codes(response) == [
        "NO_EXERCICE",
        "UNKNOWN_JOURNAL",
        "UNKNOWN_COMPTE",
        "UNKNOWN_COMPTE",
        "UNKNOWN_COMPTE",
    ]
    first = messages(response)[0]
    assert "POST /exercice" in first and "luca_open_exercice" in first
    assert rows(http, "SELECT count(*) FROM ecriture") == [[0]]


def test_add_refuses_a_date_outside_the_exercice(books: httpx.Client) -> None:
    response = books.post("/add", json=document(date="2026-01-15"))
    assert codes(response) == ["DATE_OUTSIDE_EXERCICE"]
    assert messages(response) == ["date 2026-01-15 is outside the exercice 2025-01-01 → 2025-12-31"]


def test_add_refuses_an_unknown_journal(books: httpx.Client) -> None:
    response = books.post("/add", json=document(journal="XX"))
    assert codes(response) == ["UNKNOWN_JOURNAL"]
    assert messages(response) == ["journal XX does not exist"]


def test_add_refuses_unknown_comptes_naming_each_ligne(books: httpx.Client) -> None:
    lignes = [{"compte": "999999", "debit": "1"}, {"compte": "888888", "credit": "1"}]
    response = books.post("/add", json=document(lignes=lignes))
    assert codes(response) == ["UNKNOWN_COMPTE", "UNKNOWN_COMPTE"]
    assert messages(response) == [
        "lignes[0].compte: 999999 does not exist",
        "lignes[1].compte: 888888 does not exist",
    ]


def test_add_reports_every_store_rule_at_once_and_writes_nothing(books: httpx.Client) -> None:
    books.post("/add", json=document())
    lignes = [{"compte": "999999", "debit": "1"}, {"compte": "706000", "credit": "1"}]
    response = books.post("/add", json=document(journal="XX", date="2024-12-31", lignes=lignes))
    assert codes(response) == [
        "REQUEST_ID_CONFLICT",
        "DATE_OUTSIDE_EXERCICE",
        "UNKNOWN_JOURNAL",
        "UNKNOWN_COMPTE",
    ]
    assert rows(books, "SELECT count(*) FROM ecriture") == [[1]]


# --- annule ---------------------------------------------------------------------

INVERSE = [
    {"compte": "411000", "credit": "1200.00"},
    {"compte": "706000", "debit": "1000.00"},
    {"compte": "445710", "debit": "200.00"},
]


def cancellation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "request_id": "annule-F2025-001",
        "date": "2025-01-20",
        "lib": "Annulation facture F2025-001",
        "annule": "VE/1",
        "lignes": INVERSE,
    }
    return document(**{**base, **overrides})


def test_add_accepts_the_exact_inverse_and_links_it(books: httpx.Client) -> None:
    books.post("/add", json=document())
    response = books.post("/add", json=cancellation())
    assert response.status_code == 200, response.text
    ecriture = response.json()["ecriture"]
    assert (ecriture["journal"], ecriture["num"], ecriture["annule"]) == ("VE", 2, "VE/1")
    assert rows(books, "SELECT id, annule_id FROM ecriture ORDER BY id") == [[1, None], [2, 1]]


def test_a_cancellation_is_itself_cancellable(books: httpx.Client) -> None:
    books.post("/add", json=document())
    books.post("/add", json=cancellation())
    lignes = document()["lignes"]
    response = books.post("/add", json=cancellation(request_id="re", annule="VE/2", lignes=lignes))
    assert response.status_code == 200, response.text
    assert response.json()["ecriture"]["annule"] == "VE/2"


def test_add_refuses_annule_of_a_missing_ecriture(books: httpx.Client) -> None:
    response = books.post("/add", json=cancellation(annule="VE/7"))
    assert codes(response) == ["ANNULE_NOT_FOUND"]
    assert messages(response) == ["annule VE/7: no such écriture"]


@pytest.mark.parametrize(
    "lignes",
    [
        [{"compte": "411000", "credit": "1200.00"}, {"compte": "706000", "debit": "1200.00"}],
        [INVERSE[1], INVERSE[0], INVERSE[2]],
        [{**INVERSE[0], "compte": "445710"}, *INVERSE[1:]],
        [
            {"compte": "411000", "credit": "1100.00"},
            {"compte": "706000", "debit": "900.00"},
            INVERSE[2],
        ],
        document()["lignes"],
    ],
)
def test_add_refuses_annule_that_is_not_the_exact_inverse(
    books: httpx.Client, lignes: list[dict[str, Any]]
) -> None:
    books.post("/add", json=document())
    response = books.post("/add", json=cancellation(lignes=lignes))
    assert codes(response) == ["ANNULE_NOT_INVERSE"]
    assert rows(books, "SELECT count(*) FROM ecriture") == [[1]]


def test_add_refuses_cancelling_twice(books: httpx.Client) -> None:
    books.post("/add", json=document())
    books.post("/add", json=cancellation())
    response = books.post("/add", json=cancellation(request_id="again"))
    assert codes(response) == ["ANNULE_ALREADY_USED"]
    assert messages(response) == ["annule VE/1: already cancelled by VE/2"]


@pytest.mark.parametrize("reference", ["VE", "VE/0", "VE/x", "/1", "", 1])
def test_add_refuses_a_malformed_annule(books: httpx.Client, reference: Any) -> None:
    assert codes(books.post("/add", json=cancellation(annule=reference))) == ["INVALID_SHAPE"]


def test_annule_is_part_of_the_canonical_content(books: httpx.Client) -> None:
    books.post("/add", json=document())
    books.post("/add", json=cancellation())
    plain = cancellation()
    del plain["annule"]
    assert codes(books.post("/add", json=plain)) == ["REQUEST_ID_CONFLICT"]


# --- the day of acceptance ------------------------------------------------------


def test_valid_date_is_the_day_in_paris(monkeypatch: pytest.MonkeyPatch) -> None:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Frozen:  # type: ignore[override]
            return cls(2025, 12, 31, 23, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(ledger, "datetime", Frozen)
    assert ledger.today() == "2026-01-01"


def test_add_stamps_the_day_of_acceptance(
    books: httpx.Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger, "today", lambda: "2026-01-01")
    response = books.post("/add", json=document(date="2025-12-31"))
    assert response.json()["ecriture"]["valid_date"] == "2026-01-01"
    assert rows(books, "SELECT valid_date FROM ecriture") == [["2026-01-01"]]
