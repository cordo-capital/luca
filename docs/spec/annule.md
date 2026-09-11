# annule — the only correction

An accepted écriture never changes ([store.md](store.md)). A mistake is corrected by a second écriture that is the exact inverse of the first, linked to it. That is the whole correction model: no edit, no delete, no "unpost".

## The reference

`POST /add` takes an optional `annule` key holding `<journal>/<num>` — the journal code and `EcritureNum` of the écriture being cancelled, as returned when it was accepted:

```json
{
  "request_id": "annule-2025-01-15-F2025-001",
  "journal": "VE",
  "date": "2025-01-20",
  "piece": {"ref": "F2025-001", "date": "2025-01-15"},
  "lib": "Annulation facture F2025-001",
  "annule": "VE/1",
  "lignes": [
    {"compte": "411000", "credit": "1200.00"},
    {"compte": "706000", "debit": "1000.00"},
    {"compte": "445710", "debit": "200.00"}
  ]
}
```

`annule` is stored as a link: `ecriture.annule_id` references the cancelled écriture's `id`. It is returned as `"annule": "VE/1"` in every `ecriture` object, `null` when the écriture cancels nothing.

## Rules

Checked with the other store rules, inside the accepting transaction ([endpoints.md](endpoints.md)):

| Rule | Code |
|---|---|
| `annule` is `<journal>/<num>` with `num` a positive integer | `INVALID_SHAPE` |
| That écriture exists | `ANNULE_NOT_FOUND` |
| No écriture already cancels it — `annule_id` is unique in the schema | `ANNULE_ALREADY_USED` |
| The lignes are the exact inverse | `ANNULE_NOT_INVERSE` |

**Exact inverse**: the same number of lignes, in the same order, each with the same `compte`, and the debit of one equal to the credit of the other. Labels are free. The journal, the date, the pièce and the `lib` of the cancelling écriture are the client's choice; the date must be in the exercice like any other.

The cancelling écriture is an ordinary écriture: numbered in its journal, immutable, and itself cancellable — cancelling a cancellation restores the original amounts.
