"""POST /compte, /journal, /exercice, /lock, /close (docs/spec/endpoints.md)."""

from __future__ import annotations

import logging
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


EXERCICE_2025 = {"date_start": "2025-01-01", "date_end": "2025-12-31"}
EXERCICE_2026 = {"date_start": "2026-01-01", "date_end": "2026-12-31"}
OPEN = {"closed": False, "locked_through": None}


def test_exercice_is_opened(http: httpx.Client) -> None:
    response = http.post("/exercice", json=EXERCICE_2025)
    assert response.status_code == 200, response.text
    assert response.json() == {"societe": SOCIETE, "exercice": {**EXERCICE_2025, **OPEN}}
    assert rows(http, "SELECT id, date_start, date_end, closed, locked_through FROM exercice") == [
        [1, "2025-01-01", "2025-12-31", 0, None]
    ]


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


def test_the_next_exercice_starts_the_day_after_the_last_one_ends(http: httpx.Client) -> None:
    assert http.post("/exercice", json=EXERCICE_2025).status_code == 200
    response = http.post("/exercice", json=EXERCICE_2026)
    assert response.status_code == 200, response.text
    assert response.json()["exercice"] == {**EXERCICE_2026, **OPEN}
    for start, end in (
        ("2027-01-02", "2027-12-31"),  # a gap
        ("2026-07-01", "2027-06-30"),  # an overlap
        ("2024-01-01", "2024-12-31"),  # the past
    ):
        response = http.post("/exercice", json={"date_start": start, "date_end": end})
        assert codes(response) == ["EXERCICE_NOT_CONTIGUOUS"], (start, end)
        assert messages(response) == [
            f"date_start {start} is not the day after the last exercice"
            " 2026-01-01 → 2026-12-31: expected 2027-01-01"
        ]
    assert rows(http, "SELECT id, date_start FROM exercice ORDER BY id") == [
        [1, "2025-01-01"],
        [2, "2026-01-01"],
    ]


def test_exercice_reports_every_broken_rule_at_once(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    response = http.post("/exercice", json={"date_start": "2026-12-31", "date_end": "2026-01-01"})
    assert codes(response) == ["INVALID_EXERCICE", "EXERCICE_NOT_CONTIGUOUS"]


# --- /lock ----------------------------------------------------------------------


def lock(http: httpx.Client, date_end: str, through: str | None) -> httpx.Response:
    return http.post("/lock", json={"date_end": date_end, "locked_through": through})


def test_lock_sets_the_last_locked_day_and_moves_freely_while_open(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    response = lock(http, "2025-12-31", "2025-08-31")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "societe": SOCIETE,
        "exercice": {**EXERCICE_2025, "closed": False, "locked_through": "2025-08-31"},
    }
    assert rows(http, "SELECT locked_through FROM exercice") == [["2025-08-31"]]
    for through in ("2025-09-30", "2025-09-30", "2025-01-01", "2025-12-31", None, "2025-03-31"):
        response = lock(http, "2025-12-31", through)
        assert response.status_code == 200, (through, response.text)
        assert response.json()["exercice"]["locked_through"] == through
        assert rows(http, "SELECT locked_through FROM exercice") == [[through]]


def test_lock_refuses_a_date_that_ends_no_exercice(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    response = lock(http, "2025-08-31", "2025-08-31")
    assert codes(response) == ["EXERCICE_NOT_FOUND"]
    assert messages(response) == ["no exercice ends on 2025-08-31"]
    assert rows(http, "SELECT locked_through FROM exercice") == [[None]]


@pytest.mark.parametrize("through", ["2024-12-31", "2026-01-01"])
def test_lock_refuses_a_day_outside_the_exercice(http: httpx.Client, through: str) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    response = lock(http, "2025-12-31", through)
    assert codes(response) == ["INVALID_LOCK"]
    assert messages(response) == [
        f"locked_through {through} is not a day of the exercice 2025-01-01 → 2025-12-31"
    ]
    assert rows(http, "SELECT locked_through FROM exercice") == [[None]]


def test_a_closed_exercice_keeps_its_lock(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    assert lock(http, "2025-12-31", "2025-06-30").status_code == 200
    response = http.post("/close", json={"date_end": "2025-12-31"})
    assert response.status_code == 200, response.text
    assert response.json()["exercice"] == {
        **EXERCICE_2025,
        "closed": True,
        "locked_through": "2025-06-30",
    }
    response = lock(http, "2025-12-31", None)
    assert codes(response) == ["EXERCICE_CLOSED"]
    assert messages(response) == [
        "the exercice 2025-01-01 → 2025-12-31 is closed: its lock does not move"
    ]
    assert codes(lock(http, "2025-12-31", "2026-01-01")) == ["EXERCICE_CLOSED", "INVALID_LOCK"]
    assert rows(http, "SELECT closed, locked_through FROM exercice") == [[1, "2025-06-30"]]


def test_lock_logs_one_line(http: httpx.Client, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="luca")
    http.post("/exercice", json=EXERCICE_2025)
    lock(http, "2025-12-31", "2025-08-31")
    lock(http, "2025-12-31", None)
    assert [m for m in caplog.messages if m.startswith("POST /lock")] == [
        "POST /lock client=- locked 2025-01-01 → 2025-12-31 through 2025-08-31",
        "POST /lock client=- unlocked 2025-01-01 → 2025-12-31",
    ]


def test_exercices_are_locked_independently(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    http.post("/exercice", json=EXERCICE_2026)
    # 2026 is validated month by month while 2025 stays open, unlocked, for its bilan
    assert lock(http, "2026-12-31", "2026-03-31").status_code == 200
    assert rows(http, "SELECT date_end, locked_through FROM exercice ORDER BY id") == [
        ["2025-12-31", None],
        ["2026-12-31", "2026-03-31"],
    ]


@pytest.mark.parametrize(
    ("body", "reasons"),
    [
        ({}, ["date_end: missing", "locked_through: missing"]),
        ({"date_end": "2025-12-31"}, ["locked_through: missing"]),
        (
            {"date_end": "2025-12-31", "locked_through": "31/08/2025"},
            ["locked_through: '31/08/2025' is not a date (YYYY-MM-DD)"],
        ),
        ({"date_end": "2025-12-31", "locked_through": True}, ["locked_through: not a string"]),
        (
            {"date_end": "2025-12-31", "locked_through": "2025-08-31", "force": True},
            ["force: unknown key"],
        ),
    ],
)
def test_lock_refuses_a_malformed_document(
    http: httpx.Client, body: dict[str, Any], reasons: list[str]
) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    response = http.post("/lock", json=body)
    assert codes(response) == ["INVALID_SHAPE"] * len(reasons)
    assert messages(response) == reasons
    assert rows(http, "SELECT locked_through FROM exercice") == [[None]]


# --- /close ---------------------------------------------------------------------


def test_close_marks_the_exercice_closed_for_good(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    response = http.post("/close", json={"date_end": "2025-12-31"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "societe": SOCIETE,
        "exercice": {**EXERCICE_2025, "closed": True, "locked_through": None},
    }
    assert rows(http, "SELECT closed FROM exercice") == [[1]]
    response = http.post("/close", json={"date_end": "2025-12-31"})
    assert codes(response) == ["EXERCICE_CLOSED"]
    assert messages(response) == ["the exercice 2025-01-01 → 2025-12-31 is already closed"]


def test_close_refuses_a_date_that_ends_no_exercice(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    response = http.post("/close", json={"date_end": "2025-12-30"})
    assert codes(response) == ["EXERCICE_NOT_FOUND"]
    assert messages(response) == ["no exercice ends on 2025-12-30"]
    assert rows(http, "SELECT closed FROM exercice") == [[0]]


def test_exercices_are_closed_oldest_first(http: httpx.Client) -> None:
    http.post("/exercice", json=EXERCICE_2025)
    http.post("/exercice", json=EXERCICE_2026)
    response = http.post("/close", json={"date_end": "2026-12-31"})
    assert codes(response) == ["EXERCICE_ORDER"]
    assert messages(response) == [
        "the exercice 2025-01-01 → 2025-12-31 is still open: close it first"
    ]
    assert http.post("/close", json={"date_end": "2025-12-31"}).status_code == 200
    assert http.post("/close", json={"date_end": "2026-12-31"}).status_code == 200
    assert rows(http, "SELECT date_end, closed FROM exercice ORDER BY id") == [
        ["2025-12-31", 1],
        ["2026-12-31", 1],
    ]


@pytest.mark.parametrize(
    ("body", "reasons"),
    [
        ({}, ["date_end: missing"]),
        ({"date_end": "31/12/2025"}, ["date_end: '31/12/2025' is not a date (YYYY-MM-DD)"]),
        ({"date_end": "2025-12-31", "force": True}, ["force: unknown key"]),
    ],
)
def test_close_refuses_a_malformed_document(
    http: httpx.Client, body: dict[str, Any], reasons: list[str]
) -> None:
    response = http.post("/close", json=body)
    assert codes(response) == ["INVALID_SHAPE"] * len(reasons)
    assert messages(response) == reasons
