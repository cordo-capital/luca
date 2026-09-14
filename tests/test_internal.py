"""luca itself fails: one code, nothing written, the trace on stderr (docs/spec/endpoints.md)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from luca.store import Store

from .conftest import SOCIETE, mcp_call, rows

FAILURE = {"code": "INTERNAL_ERROR", "message": "OperationalError: database is locked"}


class Flaky:
    """The write connection, whose ``COMMIT`` fails while ``failing`` is set."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.failing = True

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        if sql == "COMMIT" and self.failing:
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


@pytest.fixture
def flaky(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flaky]:
    wrapped = Flaky(store._conn)
    monkeypatch.setattr(store, "_conn", wrapped)
    yield wrapped
    wrapped.failing = False


def test_a_failure_is_one_code_and_nothing_written_and_the_next_write_works(
    http: httpx.Client, flaky: Flaky, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="luca")
    response = http.post("/journal", json={"code": "VE", "lib": "Ventes"})
    assert response.status_code == 500, response.text
    assert response.json() == {"societe": SOCIETE, "errors": [FAILURE]}
    assert rows(http, "SELECT count(*) FROM journal") == [[0]]

    flaky.failing = False
    assert http.post("/journal", json={"code": "VE", "lib": "Ventes"}).status_code == 200
    assert rows(http, "SELECT code FROM journal") == [["VE"]]

    trace = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(trace) == 1 and trace[0].exc_info is not None
    assert "database is locked" in caplog.text
    assert [m for m in caplog.messages if m.startswith("POST /journal client=- ")] == [
        "POST /journal client=- failed OperationalError: database is locked",
        "POST /journal client=- failed OperationalError: database is locked",
        "POST /journal client=- added VE",
    ]


def test_a_failure_over_mcp_is_the_same_object(url: str, http: httpx.Client, flaky: Flaky) -> None:
    result = mcp_call(url, "luca_add_journal", {"code": "VE", "lib": "Ventes"})
    assert result.is_error
    assert result.structured_content == {"societe": SOCIETE, "errors": [FAILURE]}
    assert rows(http, "SELECT count(*) FROM journal") == [[0]]
