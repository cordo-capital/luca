# Store

One server, one société, one SQLite file. The server is the only process that writes to the file; nothing else opens it for writing (ADR [0001](../decisions/0001-one-server-writes-one-file.md), [0002](../decisions/0002-one-file-per-societe.md)). This page describes the file `luca serve` creates and the guarantees the schema itself enforces. The migration is the schema — `src/luca/migrations/0001-schema.sql` — and this page explains it.

## Principles

1. **One file, one société, every exercice.** Journaux and comptes are société-wide. Exercices are contiguous, opened in order, and open until closed; closing is the one irreversible act ([endpoints.md](endpoints.md)). Several may be open at once.
2. **The écriture is the unit.** It is accepted whole — balanced, numbered, dated — in one transaction, or refused with nothing written. There is no brouillard.
3. **Accepted is immutable.** Triggers refuse every `UPDATE` and `DELETE` on `ecriture` and `ligne`, every `DELETE` on `exercice`, and every `UPDATE` on `exercice` but the one that closes it. The only correction is an inverse écriture linked to the one it cancels ([annule.md](annule.md)).
4. **Two identities.** `ecriture.id` is technical: stable, never reused, and what `annule` names. `num` is accounting: `EcritureNum`, continuous per journal within an exercice — the FEC is per exercice, and a gap in its sequence reads as a deletion — assigned inside the accepting transaction.
5. **Exact amounts.** EUR in centimes, `INTEGER`. No floating point anywhere: not in the store, not in the server, not on the wire.
6. **Days, never instants.** Every date in the store is a day written `YYYY-MM-DD`. No column holds a timestamp; the store has no creation time and an écriture no recording time. `valid_date` is the day of acceptance in Europe/Paris ([endpoints.md](endpoints.md)). The chronological order of validation is `valid_date`, then `id`.
7. **The server guarantees balance, not the schema.** No SQL constraint checks that an écriture's debits equal its credits, because a row-level constraint cannot see the whole écriture and a trigger would only re-check what the single writer already checked. The server is the only writer; it accepts an écriture only when balanced. The same goes for the contiguity of exercices and for `ecriture.exercice_id`, derived from `date` under the write lock.
8. **The schema is versioned.** `PRAGMA user_version` holds the version; migrations are numbered SQL files shipped with luca.

## The file

`luca serve --db <path>` creates the file when it does not exist and needs `--siren` and `--name` to do so ([startup.md](startup.md)). Creation sets, in this order: `PRAGMA application_id = 0x4C554341` (`LUCA`), `PRAGMA journal_mode = WAL`, the schema, `PRAGMA user_version = 1`, and the single `societe` row. Nothing else: no compte, no journal, no exercice (ADR [0004](../decisions/0004-a-societe-starts-empty.md)). A failed creation leaves no file behind.

The file name carries no meaning. The identity of the société is inside the file.

Opening an existing file checks, before anything else, that `application_id` is `0x4C554341` and that `user_version` is not above the latest migration luca ships. A file failing either check is refused, untouched, and `serve` exits. Pending migrations are then applied, each in one transaction, and the file is put in WAL mode if it is not — a copy made by `VACUUM INTO` is in rollback-journal mode ([startup.md](startup.md#backup-and-restore)).

The file is in WAL mode: readers never wait for the writer and the writer never waits for readers. The `-wal` and `-shm` files next to the database belong to it. A file copy is a backup only when the server is stopped; a live store is copied with `VACUUM INTO` or the SQLite backup API.

Tables are `STRICT`. Foreign keys are enforced on the write connection.

## Connections

**One write connection, one worker.** The server holds a single connection opened with `isolation_level=None` and takes a process-level lock around every write. Each write is one explicit `BEGIN IMMEDIATE` … `COMMIT`. This is deliberate:

- SQLite allows one writer at a time. A single connection behind a lock never meets `SQLITE_BUSY`, never waits, never retries.
- `BEGIN IMMEDIATE` takes the write lock at the start of the transaction. A deferred transaction would start as a read and try to upgrade at the first write, which is the one situation in which SQLite can refuse a transaction midway. The immediate form removes it.
- `num` is `1 + max(num)` for the journal within the exercice, read and written inside the same transaction under the same lock. Two concurrent `/add` cannot read the same maximum.
- `isolation_level=None` stops Python's `sqlite3` from opening implicit transactions, so the transaction boundaries are exactly the ones written in the code.
- A write that fails — a rule, the disk, `COMMIT` itself — is rolled back before the lock is released, so the next write always starts on a clean connection.

**One read connection per query.** `/query` opens a fresh connection in `?mode=ro` for each request, with `PRAGMA query_only = 1`, an authorizer and an opcode budget ([query.md](query.md)), and closes it after. Nothing on the read path can write, and a slow query cannot hold the write connection.

## Tables

| Table | Columns | Written by |
|---|---|---|
| `societe` | one row, `id = 1`: `siren` (nine digits), `name` | `serve`, at creation |
| `exercice` | `id` (in opening order), `date_start`, `date_end` (not before `date_start`), `closed` (`0` or `1`) | `/exercice`, `/close` |
| `journal` | `code`, `lib` | `/journal` |
| `compte` | `numero` (three characters or more), `lib` | `/compte` |
| `ecriture` | `id`, `exercice_id`, `journal_code`, `num`, `date`, `piece_ref`, `piece_date`, `lib`, `valid_date`, `request_id`, `request_hash`, `annule_id` | `/add` |
| `ligne` | `(ecriture_id, idx)`, `compte`, `lib` (optional), `debit`, `credit` | `/add` |

Constraints that matter: `UNIQUE (exercice_id, journal_code, num)`; `date_start` and `date_end` are unique among exercices; every `exercice_id`, `journal_code`, `compte`, `annule_id` is a foreign key; every date is `YYYY-MM-DD`; `request_id` is unique and non-empty; `request_hash` is a 64-character hex digest ([canonical.md](canonical.md)); `annule_id` is unique, so an écriture is cancelled at most once; labels are non-empty; `debit` and `credit` are non-negative integers and exactly one of the two is strictly positive.

Every column is written by an endpoint. There is no other table.

## Schema versioning

- `src/luca/migrations/NNNN-<name>.sql`, numbered from `0001` without gaps; luca sets `user_version = NNNN` after each.
- A shipped migration is never edited. Any schema change is a new migration.
- `PRAGMA user_version` is readable through `/query` ([query.md](query.md)).
