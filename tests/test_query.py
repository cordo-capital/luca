"""POST /query: raw SQL, read only (docs/spec/query.md)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from luca import query

from .conftest import SOCIETE, codes, document, messages, rows


def test_query_returns_columns_and_rows(books: httpx.Client) -> None:
    books.post("/add", json=document())
    response = books.post(
        "/query", json={"sql": "SELECT journal_code, num, date, lib FROM ecriture ORDER BY id"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "societe": SOCIETE,
        "columns": ["journal_code", "num", "date", "lib"],
        "rows": [["VE", 1, "2025-01-15", "Facture F2025-001"]],
        "truncated": False,
    }


def test_query_binds_params_to_the_placeholders(books: httpx.Client) -> None:
    books.post("/add", json=document())
    sql = "SELECT num, lib FROM ecriture WHERE journal_code = ? AND num >= ? AND ? IS NULL"
    response = books.post("/query", json={"sql": sql, "params": ["VE", 1, None]})
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [[1, "Facture F2025-001"]]
    response = books.post("/query", json={"sql": sql, "params": ["AC", 1, None]})
    assert response.json()["rows"] == []


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ("VE", "params: not a JSON array"),
        ({"a": "VE"}, "params: not a JSON array"),
        ([1.5], "params[0]: not a string, an integer or null"),
        ([True], "params[0]: not a string, an integer or null"),
        ([["VE"]], "params[0]: not a string, an integer or null"),
        ([2**63], "params[0]: 9223372036854775808 does not fit a SQLite integer"),
        (["VE", -(2**63) - 1], "params[1]: -9223372036854775809 does not fit a SQLite integer"),
    ],
)
def test_query_refuses_params_that_are_not_scalars(
    http: httpx.Client, params: Any, reason: str
) -> None:
    response = http.post("/query", json={"sql": "SELECT ?", "params": params})
    assert codes(response) == ["INVALID_SHAPE"]
    assert messages(response) == [reason]


def test_query_reports_a_wrong_number_of_params_as_sqlite_does(http: httpx.Client) -> None:
    response = http.post("/query", json={"sql": "SELECT ?, ?", "params": ["VE"]})
    assert codes(response) == ["SQL_ERROR"]
    assert "bindings" in messages(response)[0]


def test_query_reads_the_schema_version_and_the_schema(http: httpx.Client) -> None:
    assert rows(http, "PRAGMA user_version") == [[1]]
    assert rows(http, "SELECT * FROM pragma_application_id") == [[0x4C554341]]
    tables = rows(http, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    assert tables == [["compte"], ["ecriture"], ["exercice"], ["journal"], ["ligne"], ["societe"]]


def test_query_returns_a_blob_as_hex_and_null_as_null(http: httpx.Client) -> None:
    assert rows(http, "SELECT x'0aff', NULL, 1.5") == [["0aff", None, 1.5]]


def test_query_caps_the_rows_and_says_so(http: httpx.Client) -> None:
    sql = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < ?)"
        " SELECT x FROM c"
    )
    response = http.post("/query", json={"sql": sql.replace("?", str(query.MAX_ROWS + 1))})
    body = response.json()
    assert (len(body["rows"]), body["truncated"]) == (query.MAX_ROWS, True)
    response = http.post("/query", json={"sql": sql.replace("?", str(query.MAX_ROWS))})
    body = response.json()
    assert (len(body["rows"]), body["truncated"]) == (query.MAX_ROWS, False)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO journal (code, lib) VALUES ('VE', 'Ventes')",
        "UPDATE journal SET lib = 'x'",
        "DELETE FROM journal",
        "CREATE TABLE notes (id INTEGER)",
        "DROP TABLE journal",
        "ATTACH ':memory:' AS other",
        "PRAGMA journal_mode",
        "PRAGMA journal_mode = DELETE",
        "PRAGMA user_version = 9",
        "PRAGMA foreign_keys",
        "BEGIN",
        "VACUUM",
    ],
)
def test_query_refuses_anything_but_a_read(http: httpx.Client, sql: str) -> None:
    response = http.post("/query", json={"sql": sql})
    assert codes(response) == ["SQL_DENIED"], response.text
    assert rows(http, "SELECT count(*) FROM journal") == [[0]]
    assert rows(http, "PRAGMA user_version") == [[1]]


def test_a_denied_query_leaves_the_write_path_intact(books: httpx.Client) -> None:
    books.post("/query", json={"sql": "DELETE FROM compte"})
    assert books.post("/add", json=document()).status_code == 200
    assert rows(books, "SELECT count(*) FROM compte") == [[3]]


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        ("SELEC 1", "syntax error"),
        ("SELECT * FROM missing", "no such table: missing"),
        ("SELECT 1; SELECT 2", "one statement at a time"),
    ],
)
def test_query_returns_sqlite_errors_as_they_are(
    http: httpx.Client, sql: str, fragment: str
) -> None:
    response = http.post("/query", json={"sql": sql})
    assert codes(response) == ["SQL_ERROR"]
    assert fragment in messages(response)[0]


def test_query_interrupts_a_statement_over_budget(http: httpx.Client) -> None:
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    response = http.post("/query", json={"sql": sql})
    assert codes(response) == ["SQL_BUDGET"]
    assert rows(http, "SELECT 1") == [[1]]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "sql: missing"),
        ({"sql": ""}, "sql: not a non-empty string"),
        ({"sql": 1}, "sql: not a non-empty string"),
        ({"sql": "SELECT 1", "limit": 10}, "limit: unknown key"),
    ],
)
def test_query_refuses_a_malformed_document(
    http: httpx.Client, body: dict[str, Any], reason: str
) -> None:
    response = http.post("/query", json=body)
    assert codes(response) == ["INVALID_SHAPE"]
    assert messages(response) == [reason]


def test_query_refuses_what_is_not_json(http: httpx.Client) -> None:
    assert codes(http.post("/query", content=b"SELECT 1")) == ["INVALID_JSON"]
