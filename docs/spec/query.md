# Reading: raw SQL, read only

luca has no read endpoint per subject — no balance, no grand livre, no list of comptes. It has one: `POST /query` (`luca_query`) takes a SQL statement and returns rows (ADR [0005](../decisions/0005-reading-is-raw-sql.md)). The schema is the API for reading ([store.md](store.md)), and `sqlite_master` describes it to a client that has not read the spec.

## Request and response

```json
{"sql": "SELECT journal_code, num, date, lib FROM ecriture ORDER BY valid_date, id"}
```

```json
{
  "societe": {"siren": "123456789", "name": "ACME"},
  "columns": ["journal_code", "num", "date", "lib"],
  "rows": [["VE", 1, "2025-01-15", "Facture F2025-001"]],
  "truncated": false
}
```

One statement per request. Rows are arrays in column order; values are JSON numbers, strings or `null`; a blob comes back as lowercase hex. At most 1000 rows are returned; `truncated` is true when the statement produced more.

Amounts in the store are integer centimes, and `/query` returns them as such. Presenting them in euros is the client's job.

## How the read path cannot write

Every request opens its own connection and closes it after:

1. The connection is opened with `?mode=ro`: SQLite itself refuses to write through it.
2. `PRAGMA query_only = 1`: the connection refuses any statement that would modify the database.
3. An **authorizer** — SQLite's `set_authorizer` — refuses every action except reading: `SQLITE_SELECT`, `SQLITE_READ`, `SQLITE_FUNCTION`, `SQLITE_RECURSIVE`, and `SQLITE_PRAGMA` only for reading `user_version` and `application_id`. `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `ATTACH`, `DETACH`, `PRAGMA journal_mode`, transactions, everything else: refused with `SQL_DENIED`.

luca never inspects the SQL text. It does not look for keywords, does not strip comments, does not parse. The three layers above are SQLite's own, they see the compiled statement, and a statement that gets past them is a read.

## Budget

A progress handler counts virtual-machine opcodes and interrupts the statement past 10 million of them. A cartesian join or a runaway recursive CTE ends with `SQL_BUDGET`, and the write connection was never involved.

## Errors

| Code | When |
|---|---|
| `INVALID_SHAPE` | `sql` missing or not a string |
| `SQL_DENIED` | the authorizer refused an action |
| `SQL_BUDGET` | the opcode budget was exceeded |
| `SQL_ERROR` | any other SQLite error — a syntax error, an unknown table, two statements in one request; the message is SQLite's, unchanged |

## Examples

```sql
-- the schema version
PRAGMA user_version

-- the tables and their definitions
SELECT name, sql FROM sqlite_master WHERE type = 'table'

-- balance per compte, in centimes
SELECT compte, sum(debit) - sum(credit) AS solde FROM ligne GROUP BY compte ORDER BY compte

-- an écriture with its lignes
SELECT e.journal_code, e.num, e.date, e.lib, l.idx, l.compte, l.debit, l.credit
FROM ecriture e JOIN ligne l ON l.ecriture_id = e.id
WHERE e.journal_code = 'VE' AND e.num = 1 ORDER BY l.idx
```
