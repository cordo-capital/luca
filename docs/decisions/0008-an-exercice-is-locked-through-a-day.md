# 0008 — An open exercice is locked through a day, and the lock moves

Date: 2026-09-15
Status: accepted

## Context

The books are kept month by month: a month is collected, proposed, validated, and its TVA is declared on the figures of that validation. After that, an écriture dated inside the month — posted by a script or a language model that got a date wrong — changes a figure that was declared. Between the opening of an exercice and its clôture, luca refused nothing of the kind, and the window is long: the exercice stays open for months, the year turns, the bilan takes until spring.

Every accounting package has a period lock for this, and in most it is reversible by an authorised user: QuickBooks' closing date, Xero's lock dates, Odoo's lock dates, Pennylane's verrouillage. What is irreversible is the validation of the écriture, which the PCG requires to make the record intangible, and the clôture of the exercice. luca already has both: acceptance is validation ([endpoints.md](../spec/endpoints.md)), `/close` is the clôture (ADR [0007](0007-every-exercice-in-one-file.md)).

## Decision

**An exercice carries `locked_through`, a day of the exercice or null.** `/add` refuses an écriture dated on or before it: `DATE_LOCKED`. The exercice is named by its `date_end`, as for `/close`, so the wrong one is never locked by accident.

**The lock moves.** `POST /lock` (`luca_lock`) sets it: forward as months are validated, back to reopen days, null to unlock; the same lock again changes nothing. It guards against an écriture that slips into a validated month by mistake, not against a decision to reopen it — luca has no authorisation (ADR [0003](0003-auth-is-delegated.md)) and could not tell the two apart.

**A closed exercice does not move**, lock included. `closed` is the hard lock, final; `locked_through` the soft one. Both exist in the field.

**One lock per exercice.** Exercice N stays open for its bilan, with écritures dated its last day, while the months of N+1 are validated and locked one by one. One lock for the whole société would force a choice between the two.

**No day of the lock in the store**, as for the clôture (ADR 0007): it is on stdout.

**Replay wins over the lock.** A retried écriture accepted before the lock is replayed, not refused, like with the other store rules ([canonical.md](../spec/canonical.md)).

**The schema, edited in place once more.** Nobody, the author included, has opened a store with the schema of 0.2.0. The migration mechanism is there for the day someone has.

## Alternatives considered

- **Enforcing it in the tool on top**, the one holding the brouillard and running the monthly ritual. Any other client — a script, a human in a chat — could still post into a validated month; the rules of acceptance live in the single writer (ADR [0001](0001-one-server-writes-one-file.md)).
- **An irreversible lock**, on the model of the clôture. It conflates the lock with validation, which the écriture already has at acceptance, and a lock set one day too far would push every late pièce of the month into the next one, for good.
- **One lock date for the société**, as in Odoo. It blocks the bilan of N while N+1 is being validated, or the reverse.
- **A `periode` table with months.** luca has no rule on the length of an exercice and none on the granularity of a lock; the month is the rhythm of the tool on top.
- **A separate `/unlock`.** Two names for one setting.

## Consequences

- Seven names, not six. `luca_lock` carries `destructiveHint` with `luca_close_exercice`: moving a lock back reopens days, and a client may want to confirm it. `idempotentHint` true: same request, same lock.
- Codes: `DATE_LOCKED` on `/add`, `INVALID_LOCK` on `/lock`; `EXERCICE_NOT_FOUND` and `EXERCICE_CLOSED` are shared with `/close`.
- Every `exercice` object carries `locked_through`.
- A mistake in a locked month is corrected by an inverse écriture dated after the lock ([annule.md](../spec/annule.md)), or by moving the lock: the tool on top chooses.
