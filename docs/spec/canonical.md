# Canonical content, replay and conflict

luca is driven by programs that retry. A retry must never record an écriture twice, and reusing a `request_id` for different content must never be silently discarded. Both are decided on the *canonical content* of the écriture, not on the bytes the client happened to send.

## Canonical form

The canonical form of an écriture is the JSON serialisation of the validated document with:

- keys sorted, at every level;
- no whitespace: `,` and `:` as the only separators;
- UTF-8, non-ASCII characters kept as they are;
- amounts as integer centimes — JSON numbers — under their key: `"debit":120000`;
- optional keys absent when they were not given (`lib` on a ligne, `annule`);
- every other value exactly as received, including `request_id`.

```
{"date":"2025-01-15","journal":"VE","lib":"Facture F2025-001","lignes":[{"compte":"411000","debit":120000},{"compte":"706000","credit":100000},{"compte":"445710","credit":20000,"lib":"TVA 20 %"}],"piece":{"date":"2025-01-15","ref":"F2025-001"},"request_id":"2025-01-15-F2025-001"}
```

`ecriture.request_hash` is the SHA-256, lowercase hex, of those bytes.

Two documents are the same content when their canonical bytes are equal. Key order, whitespace, and the spelling of an amount (`"1200"`, `"1200.0"`, `"1200.00"`) do not matter. Everything else does: a label's spacing, a different `piece.ref`, the Unicode form of a string. luca never normalises text.

The canonical form is computed only once the document has passed the shape and balance rules ([endpoints.md](endpoints.md)); a malformed document has no canonical content and no replay.

## Replay

Same `request_id`, same hash: nothing is written. The response is the écriture recorded the first time, with `replay: true` — `num`, `valid_date` and `id` are those of the original acceptance. A client that re-serialises the same écriture, or resends it after a timeout, gets a replay, never a conflict.

Replay is checked first among the store rules, inside the transaction: a replayed request is not re-checked against the exercice, the journal or the comptes, which may not matter any more.

## Conflict

Same `request_id`, different hash: refused with `REQUEST_ID_CONFLICT`. The error carries the existing écriture in `ecriture`, in the same shape as an accepted response, so the client can compare what it sent with what the store holds:

```json
{
  "societe": {"siren": "123456789", "name": "ACME"},
  "errors": [
    {
      "code": "REQUEST_ID_CONFLICT",
      "message": "request_id '2025-01-15-F2025-001' already used with different content",
      "ecriture": {"id": 1, "journal": "VE", "num": 1, "...": "..."}
    }
  ]
}
```

A conflict is a client bug — a `request_id` reused for a different écriture — and the client is expected to pick a new `request_id`, not to alter the stored one. There is no way to alter it.
