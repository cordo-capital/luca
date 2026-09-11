"""POST /compte, /journal, /exercice (docs/spec/endpoints.md)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from .conftest import SOCIETE, codes, messages, rows

# --- /compte --------------------------------------------------------------------


def test_compte_is_added_as_given(http: httpx.Client) -> None:
    response = http.post("/compte", json={"numero": "411000", "lib": "Clients"})
    assert response.status_code == 200, response.text
    assert response.json() == {"societe": SOCIETE, "compte": {"numero": "411000", "lib": "Clients"}}
    assert rows(http, "SELECT numero, lib FROM compte") == [["411000", "Clients"]]


def test_compte_accepts_three_characters(http: httpx.Client) -> None:
    assert http.post("/compte", json={"numero": "411", "lib": "Clients"}).status_code == 200


def test_compte_refuses_a_duplicate_and_keeps_the_first_label(http: httpx.Client) -> None:
    http.post("/compte", json={"numero": "411000", "lib": "Clients"})
    response = http.post("/compte", json={"numero": "411000", "lib": "Autre"})
    assert codes(response) == ["COMPTE_EXISTS"]
    assert messages(response) == ["compte 411000 already exists: Clients"]
    assert rows(http, "SELECT lib FROM compte") == [["Clients"]]


@pytest.mark.parametrize("numero", ["41", "  41  "])
def test_compte_refuses_a_numero_shorter_than_three_characters(
    http: httpx.Client, numero: str
) -> None:
    response = http.post("/compte", json={"numero": numero, "lib": "Clients"})
    assert codes(response) == ["INVALID_COMPTE"]
    assert messages(response) == [f"numero {numero!r} is shorter than three characters"]
    assert rows(http, "SELECT count(*) FROM compte") == [[0]]


@pytest.mark.parametrize(
    ("body", "reasons"),
    [
        ({"numero": "411000", "lib": ""}, ["lib: not a non-empty string"]),
        (
            {"numero": "", "lib": ""},
            ["numero: not a non-empty string", "lib: not a non-empty string"],
        ),
        ({"numero": "411000"}, ["lib: missing"]),
        ({"numero": "411000", "lib": "Clients", "x": 1}, ["x: unknown key"]),
        ({"numero": 411000, "lib": "Clients"}, ["numero: not a non-empty string"]),
    ],
)
def test_compte_refuses_a_malformed_document(
    http: httpx.Client, body: dict[str, Any], reasons: list[str]
) -> None:
    response = http.post("/compte", json=body)
    assert codes(response) == ["INVALID_SHAPE"] * len(reasons)
    assert messages(response) == reasons


# --- /journal -------------------------------------------------------------------


def test_journal_is_added_as_given(http: httpx.Client) -> None:
    response = http.post("/journal", json={"code": "VE", "lib": "Ventes"})
    assert response.status_code == 200, response.text
    assert response.json() == {"societe": SOCIETE, "journal": {"code": "VE", "lib": "Ventes"}}
    assert rows(http, "SELECT code, lib FROM journal") == [["VE", "Ventes"]]


def test_journal_refuses_a_duplicate_and_keeps_the_first_label(http: httpx.Client) -> None:
    http.post("/journal", json={"code": "VE", "lib": "Ventes"})
    response = http.post("/journal", json={"code": "VE", "lib": "Autre"})
    assert codes(response) == ["JOURNAL_EXISTS"]
    assert messages(response) == ["journal VE already exists: Ventes"]
    assert rows(http, "SELECT lib FROM journal") == [["Ventes"]]


def test_journal_refuses_a_blank_code_or_label(http: httpx.Client) -> None:
    response = http.post("/journal", json={"code": " ", "lib": ""})
    assert codes(response) == ["INVALID_SHAPE", "INVALID_SHAPE"]
    assert rows(http, "SELECT count(*) FROM journal") == [[0]]


# --- /exercice ------------------------------------------------------------------


def test_exercice_is_opened(http: httpx.Client) -> None:
    response = http.post("/exercice", json={"date_start": "2025-01-01", "date_end": "2025-12-31"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "societe": SOCIETE,
        "exercice": {"date_start": "2025-01-01", "date_end": "2025-12-31"},
    }
    assert rows(http, "SELECT date_start, date_end FROM exercice") == [["2025-01-01", "2025-12-31"]]


def test_exercice_accepts_a_single_day(http: httpx.Client) -> None:
    body = {"date_start": "2025-06-30", "date_end": "2025-06-30"}
    assert http.post("/exercice", json=body).status_code == 200


def test_exercice_refuses_a_reversed_range(http: httpx.Client) -> None:
    response = http.post("/exercice", json={"date_start": "2025-12-31", "date_end": "2025-01-01"})
    assert codes(response) == ["INVALID_EXERCICE"]
    assert messages(response) == ["date_end 2025-01-01 is before date_start 2025-12-31"]
    assert rows(http, "SELECT count(*) FROM exercice") == [[0]]


def test_exercice_refuses_malformed_dates_naming_each(http: httpx.Client) -> None:
    response = http.post("/exercice", json={"date_start": "01/01/2025", "date_end": "2025-02-30"})
    assert codes(response) == ["INVALID_SHAPE", "INVALID_SHAPE"]
    assert messages(response) == [
        "date_start: '01/01/2025' is not a date (YYYY-MM-DD)",
        "date_end: '2025-02-30' is not a calendar date",
    ]


def test_exercice_refuses_a_second_one(http: httpx.Client) -> None:
    http.post("/exercice", json={"date_start": "2025-01-01", "date_end": "2025-12-31"})
    response = http.post("/exercice", json={"date_start": "2026-01-01", "date_end": "2026-12-31"})
    assert codes(response) == ["EXERCICE_EXISTS"]
    assert messages(response) == ["an exercice already exists: 2025-01-01 → 2025-12-31"]
    assert rows(http, "SELECT date_start FROM exercice") == [["2025-01-01"]]


def test_exercice_reports_every_broken_rule_at_once(http: httpx.Client) -> None:
    http.post("/exercice", json={"date_start": "2025-01-01", "date_end": "2025-12-31"})
    response = http.post("/exercice", json={"date_start": "2026-12-31", "date_end": "2026-01-01"})
    assert codes(response) == ["INVALID_EXERCICE", "EXERCICE_EXISTS"]
