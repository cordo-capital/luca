# annule — the only correction

An accepted écriture never changes ([store.md](store.md)). A mistake is corrected by a second écriture that is the exact inverse of the first, linked to it. That is the whole correction model: no edit, no delete, no "unpost" — and no reopening of a closed exercice: an écriture of a closed exercice is corrected the same way, by an inverse écriture dated in an open one.

## The reference

`POST /add` takes an optional `annule` key holding the `id` of the écriture being cancelled — the technical identifier returned when it was accepted and readable through `/query`. `num` does not identify an écriture: it restarts with every exercice.

```json
{
  "request_id": "annule-2025-01-15-F2025-001",
  "journal": "VE",
  "date": "2025-01-20",
  "piece": {"ref": "F2025-001", "date": "2025-01-15"},
  "lib": "Annulation facture F2025-001",
  "annule": 1,
  "lignes": [
    {"compte": "411000", "credit": "1200.00"},
    {"compte": "706000", "debit": "1000.00"},
    {"compte": "445710", "debit": "200.00"}
  ]
}
```

`annule` is stored as a link: `ecriture.annule_id` references the cancelled écriture's `id`. It is returned as `"annule": 1` in every `ecriture` object, `null` when the écriture cancels nothing.

## Rules

Checked with the other store rules, inside the accepting transaction ([endpoints.md](endpoints.md)):

| Rule | Code |
|---|---|
| `annule` is a positive integer | `INVALID_SHAPE` |
| That écriture exists | `ANNULE_NOT_FOUND` |
| No écriture already cancels it — `annule_id` is unique in the schema | `ANNULE_ALREADY_USED` |
| The lignes are the exact inverse | `ANNULE_NOT_INVERSE` |

**Exact inverse**: the same number of lignes, in the same order, each with the same `compte`, and the debit of one equal to the credit of the other. Labels are free. The journal, the date, the pièce and the `lib` of the cancelling écriture are the client's choice; the date must be in an open exercice like any other — which is how a closed exercice is corrected, from the open one, without touching it.

The cancelling écriture is an ordinary écriture: numbered in its journal and its exercice, immutable, and itself cancellable — cancelling a cancellation restores the original amounts.
