# 0001 — One server is the only writer of its file

Date: 2026-09-11
Status: accepted

## Context

A ledger needs four guarantees from its store: a write is atomic, what is written does not move, amounts are exact, and the numbering of écritures is continuous. SQLite gives the first three to any process that opens the file. The fourth — and the replay and conflict rules on `request_id` — needs every check and every write of an écriture to happen under one lock, and needs nobody else to write in between.

## Decision

luca is a server that owns one SQLite file and is the only process that writes to it. Every client — a script, a human in a chat, a language model in someone else's loop — talks to the server over HTTP or MCP. Nobody else opens the file for writing.

Inside the server: one write connection, one worker, a process-level lock around every write, and one explicit `BEGIN IMMEDIATE` … `COMMIT` per écriture. Reads go through separate read-only connections. The reasons are in [store.md](../spec/store.md).

Because the server is the only writer, the schema does not check balance: the server does, on the whole écriture, before writing.

## Alternatives considered

- **A library or CLI that any process runs against the file.** Two processes can both compute `1 + max(num)` before either commits; SQLite's lock makes one of them wait or fail, and the retry logic has to live in every caller. Replay and conflict on `request_id` have the same problem.
- **Enforcing balance in SQL with triggers.** A trigger sees one row at a time and cannot tell an écriture in progress from an unbalanced one without a state column; it would only re-check what the single writer has already checked.

## Consequences

- The server is the contract. A direct SQL write to the file is outside it.
- The write path is serial by design; the read path is not.
- Multiple workers, connection pools, or a second process on the same file are bugs, not tuning.
