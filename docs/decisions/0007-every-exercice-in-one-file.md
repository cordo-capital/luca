# 0007 — A file holds every exercice of its société; closing is explicit

Date: 2026-09-14
Status: accepted

## Context

ADR [0002](0002-one-file-per-societe.md) decided one file per société. "One exercice per file" had followed with no decision behind it. A société lives many years, and French practice is: exercice N stays open for months after its last day, while N+1 already receives écritures; then N is closed once the bilan is done, and never reopened. With one exercice per file, every year is a new file, a new process, a new port, a plan comptable and journaux re-created by the tool on top, and no query across two years.

## Decision

**The file holds every exercice of its société**, contiguous: each `date_start` is the day after the previous `date_end`, opened in order, no gap, no overlap, no rule on the length. Journaux and comptes stay société-wide.

**An exercice is open until it is closed.** Several may be open at once. Closing is explicit — `POST /close` (`luca_close_exercice`) names the exercice by its `date_end`, so the wrong one is never closed by accident — in order, oldest first, and irreversible: a closed exercice accepts no écriture, ever. A mistake in it is corrected the only way there is, an inverse écriture linked by `annule` and dated in an open exercice.

**`EcritureNum` restarts with every exercice.** With two exercices open, a sequence continuous across them has holes in each: VE/101 dated 2026, then VE/102 dated 2025. The FEC is per exercice, and a hole in its sequence reads as a deletion. So `num` is continuous per journal *and* exercice; `ecriture.exercice_id`, derived from `date` under the write lock, carries the exercice, and `UNIQUE (exercice_id, journal_code, num)` holds it.

**`annule` names an écriture by its `id`.** `<journal>/<num>` no longer identifies one. The technical `id` is stable, never reused, returned at acceptance and readable through `/query`: it becomes the reference. Every `ecriture` object also carries its `exercice`, since a `num` means nothing without it.

**`closed` is a flag, not a day.** In French, *date de clôture de l'exercice* already means its last day, `date_end`; a `date_closed` column would read as that. The day the close was requested is on stdout, like every request.

**Not luca's**: the à-nouveaux, the résultat, the `AN` journal. The tool on top reads the balances with `luca_query` and posts the opening écriture of N+1 with `luca_add`, like any other.

## Alternatives considered

- **One exercice per file.** Each year a file, a process, a port; the plan comptable re-created; no query across years; freezing year N is "stop its server", which is not a rule luca enforces.
- **Only the latest exercice open** — opening N+1 closes N. No column, no endpoint, but wrong: N is open for months while N+1 receives écritures.
- **Numbering continuous across exercices.** Simpler, keeps `<journal>/<num>` unique, and leaves holes in every exercice's FEC.
- **A reference with the exercice in it**, `"2025-12-31/VE/1"`. Readable, and one more format to parse; the `id` already exists.
- **Closing by opening**, a flag on `/exercice`. Two actions in one request; a refusal of one is a refusal of both.

## Consequences

- Six names, not five, and the tool that closes is the one with `destructiveHint`: the one irreversible act.
- Codes: `EXERCICE_CLOSED`, `EXERCICE_NOT_CONTIGUOUS`, `EXERCICE_NOT_FOUND`, `EXERCICE_ORDER`; `EXERCICE_EXISTS` no longer exists.
- The schema, edited in place: nobody had opened a store yet. From here on, a schema change is a migration.
- Rolling into a new year is one call to `/exercice`, and the plan comptable is where it was.
