"""The read path: raw SQL on a read-only connection (docs/spec/query.md)."""

from __future__ import annotations

import sqlite3
from typing import Any

from luca.ledger import Refused, check_object, error, text
from luca.store import Store

MAX_ROWS = 1000
OPCODE_BUDGET = 10_000_000
_PROGRESS_EVERY = 10_000  # opcodes between two calls of the progress handler


def _value(value: Any) -> Any:
    return value.hex() if isinstance(value, bytes) else value


def _refusal(exc: sqlite3.Error) -> Refused:
    code = getattr(exc, "sqlite_errorcode", None)
    if code == sqlite3.SQLITE_AUTH:
        return Refused([error("SQL_DENIED", f"not a read: {exc}")])
    if code == sqlite3.SQLITE_INTERRUPT:
        return Refused([error("SQL_BUDGET", f"statement exceeded {OPCODE_BUDGET} opcodes")])
    return Refused([error("SQL_ERROR", str(exc))])


def query(store: Store, raw: Any) -> dict[str, Any]:
    """Run one statement on a fresh read-only connection; at most ``MAX_ROWS`` rows."""
    errors: list[dict[str, Any]] = []
    if not check_object(raw, "", {"sql"}, {"sql"}, errors):
        raise Refused(errors)
    sql = text(raw, "sql", "", errors)
    if errors or sql is None:
        raise Refused(errors)

    ticks = 0

    def over_budget() -> bool:
        nonlocal ticks
        ticks += 1
        return ticks * _PROGRESS_EVERY > OPCODE_BUDGET

    conn = store.read()
    try:
        conn.set_progress_handler(over_budget, _PROGRESS_EVERY)
        try:
            cursor = conn.execute(sql)
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as exc:
            raise _refusal(exc) from None
        columns = [column[0] for column in cursor.description] if cursor.description else []
    finally:
        conn.close()
    return {
        "columns": columns,
        "rows": [[_value(v) for v in row] for row in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
    }
