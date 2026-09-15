# AGENTS.md

Instructions for anyone — human or model — working in this repository. Keep this file short; long-form material lives in `docs/`.

## What luca is

luca is a server that owns one SQLite file and is the only process that writes to it. It keeps the books of one société: écritures in journaux, in double entry, exercice after exercice in one file. Every client — a script, a human in a chat, a language model — talks to the server over HTTP or MCP, on one port, with seven names: `/add`, `/query`, `/compte`, `/journal`, `/exercice`, `/lock`, `/close` (`luca_add`, `luca_query`, `luca_add_compte`, `luca_add_journal`, `luca_open_exercice`, `luca_lock`, `luca_close_exercice`). The only command is `luca serve`.

It does **not** collect documents, interpret them, choose comptes, hold a brouillard, run a review, call a language model, do lettrage, read or write FEC files, authenticate callers, serve a UI, or hold more than one société. If a feature needs any of that, it belongs to a tool built on top of luca, not here.

## Invariants (never break these)

1. **One server, one société, one file, one writer.** No multi-tenant code, no second process on the file, no connection pool on the write path. One write connection, one lock, `BEGIN IMMEDIATE` per write. (ADR 0001, 0002)
2. **One écriture, one transaction, all or nothing.** `/add` accepts an écriture with its `num` and `valid_date` or writes nothing. Replay and conflict are decided on the canonical content, never on raw bytes (`docs/spec/canonical.md`).
3. **Amounts are integer centimes.** `INTEGER` in SQLite, `int` in Python, decimal strings on the wire. Never a `float`, never `REAL`, never a JSON number for an amount. More than two decimals is a refusal, not a rounding.
4. **What is accepted does not move.** Triggers refuse updates and deletes on `ecriture` and `ligne`. The only correction is an inverse écriture linked by `annule`. A closed exercice stays closed; it is corrected from an open one. The lock of an open exercice is the one thing that moves, and only while it is open. (ADR 0007, 0008)
5. **The read path cannot write.** `?mode=ro`, `query_only`, an authorizer allowing only reads, an opcode budget. Never inspect the SQL text.
6. **Every error has a stable code.** Clients branch on the code; the message may change, the code never. Same handlers and same errors over HTTP and MCP.
7. **The société's identity is everywhere a client can see it.** `societe` in every response, `serverInfo.name`, the start of every tool title and description.
8. **Days, never instants.** No timestamp in the store, no time zone but Europe/Paris for `valid_date`.
9. **The names are frozen.** Routes, tools and error codes are a public API (ADR 0006).
10. **French regulatory terms stay in French** and are identifiers: société, écriture, ligne, journal, compte, exercice, pièce, lib, annule, SIREN, EcritureNum, ValidDate. See `docs/spec/glossary.md`.

## How to work

- Read `docs/spec/` before touching the domain. If the spec is wrong or silent, fix the spec in the same change.
- Every accepted or refused case is a test against the HTTP endpoints on a store in `tmp_path`. Stores are built by tests, never committed.
- Decisions that constrain the future go in `docs/decisions/` as a new numbered ADR. Never edit the body of a past ADR.
- The repository describes what exists. No roadmap, no planned feature, no reserved command, no unused table or column. GitHub issues are for people reporting a problem while using luca, not for planning.
- Prefer removing to adding. Runtime dependencies are the MCP Python SDK, an ASGI server, and what they pull in. Nothing else.
- The `Dockerfile` and the release workflow are packaging, not deployment: no path, no société, no port choice, no tunnel, no replication in the repository. That is the infrastructure of whoever deploys.
- A release is a commit on `main` that raises `__version__` in `src/luca/__init__.py`. The workflow publishes the image and tags the commit; nobody pushes a tag by hand.
- English everywhere except the terms above. Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).

## Commands

```sh
uv sync                                          # install
uv run luca serve --db acme.db --siren 123456789 --name ACME
uv run pytest                                    # tests
uv run ruff check . && uv run ruff format .      # lint, format
uv run mypy src                                  # types
```

CI runs all of the above. A PR is mergeable only when CI is green.

## Layout

```
src/luca/             cli.py (serve), store.py (file, schema, connections), ledger.py (write rules),
                      query.py (read path), server.py (HTTP routes and MCP tools)
src/luca/migrations/  numbered SQL migrations; 0001 is the schema
tests/                pytest, against a server on a store in tmp_path
Dockerfile            the image: the virtualenv from uv.lock on python slim, non-root, /data
docs/spec/            the domain, one page per stable subject
docs/decisions/       ADRs, numbered, immutable
```
