# Endpoints

luca is one server on one port, reachable two ways with the same handlers and the same errors: HTTP routes, and MCP tools over streamable HTTP mounted on `/mcp`. The names below are a public API: fixed once, never renamed (ADR [0006](../decisions/0006-tool-names-are-frozen.md)).

| MCP tool | Route | Effect |
|---|---|---|
| `luca_add` | `POST /add` | one écriture, accepted or refused in one transaction |
| `luca_query` | `POST /query` | raw SQL with bound `params`, read only, rows as JSON ([query.md](query.md)) |
| `luca_add_compte` | `POST /compte` | adds a compte; refuses a duplicate |
| `luca_add_journal` | `POST /journal` | adds a journal; refuses a duplicate |
| `luca_open_exercice` | `POST /exercice` | opens the next exercice; refuses a gap or an overlap |
| `luca_lock` | `POST /lock` | locks an exercice through a day, or unlocks it; moves while the exercice is open |
| `luca_close_exercice` | `POST /close` | closes an exercice, for good; refuses out of order |

There is nothing else: no other route, no other tool, no CLI beyond `luca serve` ([startup.md](startup.md)).

## Shape of every exchange

**Request.** One JSON object. Over HTTP it is the request body, decoded as UTF-8, and parsed strictly: a duplicate key, `NaN`, `Infinity`, or anything that is not an object is refused. Over MCP it is the tool's arguments; the schema of each tool is the set of keys of the corresponding route. Unknown keys are refused everywhere.

**Response.** One JSON object, always carrying `societe`:

```json
"societe": {"siren": "123456789", "name": "ACME"}
```

The server carries the identity of the société everywhere a client can see it: `societe` in every response, `serverInfo.name` equal to `--name`, every tool title beginning with the name, and every tool description beginning with the name and the SIREN. Two luca servers side by side in one client are distinguishable by the model without help from the client.

**Refusal.** HTTP status `400` and a list of errors, every rule broken at once:

```json
{"societe": {...}, "errors": [{"code": "UNKNOWN_JOURNAL", "message": "journal XX does not exist"}]}
```

Each error carries a stable `code` and a human `message`. Clients branch on the code; the message may change, the code never. Some errors carry more (`ecriture` on a conflict). Over MCP the same object is the tool result's `structuredContent`, its text content is the same JSON, and `isError` is true. Success is HTTP `200`, or `isError` false.

A refusal writes nothing. There is no partial acceptance.

**Failure.** When luca itself fails — the disk, SQLite, a bug — the response is HTTP `500` (over MCP, `isError` true) with one error, `INTERNAL_ERROR`, whose message names the exception. Nothing was written: the transaction, if one was open, is rolled back. The traceback is on stderr. The next request is handled normally.

The MCP SDK validates nothing: a tool's arguments reach the handler as they came, and the handler refuses them exactly as it refuses a request body — same codes, same messages. Calling a tool that does not exist is a JSON-RPC error (`-32602`), not a luca refusal.

Each tool carries a `title` beginning with the société's name and the annotations a client may rely on: `readOnlyHint` true on `luca_query` only; `destructiveHint` true on `luca_close_exercice`, the one irreversible act, and on `luca_lock`, whose move back reopens days a client may want to confirm — false elsewhere, luca never deletes; `idempotentHint` true on `luca_add`, whose repeat is a replay ([canonical.md](canonical.md)), on `luca_query`, and on `luca_lock`, whose repeat sets the same lock, false on the three others, whose repeat is a refusal; `openWorldHint` false everywhere, nothing reaches beyond the store.

**Amounts** on the wire are decimal strings: `"1200.00"`. Never a JSON number, in or out.

**Dates** are `YYYY-MM-DD` and must be real calendar days.

## `POST /add` — `luca_add`

```json
{
  "request_id": "2025-01-15-F2025-001",
  "journal": "VE",
  "date": "2025-01-15",
  "piece": {"ref": "F2025-001", "date": "2025-01-15"},
  "lib": "Facture F2025-001",
  "lignes": [
    {"compte": "411000", "debit": "1200.00"},
    {"compte": "706000", "credit": "1000.00"},
    {"compte": "445710", "lib": "TVA 20 %", "credit": "200.00"}
  ]
}
```

Keys: `request_id`, `journal`, `date`, `piece` `{ref, date}`, `lib`, `lignes`, optional `annule` ([annule.md](annule.md)). Two to 1000 lignes — the bound keeps one écriture from holding the write lock for long. Each ligne: `compte`, optional `lib`, and exactly one of `debit` or `credit`. Strings are non-empty. An amount is a string of digits with at most two decimals after a dot — `"1200"`, `"1200.5"`, `"1200.50"` — strictly positive and at most `92233720368547758.07`, the largest integer SQLite stores in centimes. `"1.005"` is refused, not rounded. A JSON number is refused.

Rules, in order. Document rules are checked first and reported together; if they pass, store rules are checked together inside the accepting transaction.

| # | Rule | Code |
|---|---|---|
| 1 | The body is a JSON object (HTTP) | `INVALID_JSON` |
| 2 | Every key present, none unknown, every value of the right shape; two to 1000 lignes; exactly one side per ligne | `INVALID_SHAPE`, one per fault |
| 3 | Sum of debits equals sum of credits | `UNBALANCED` |
| 4 | Same `request_id` already accepted with the same canonical content: nothing is written, the original écriture is returned with `replay: true` ([canonical.md](canonical.md)) | — |
| 5 | Same `request_id`, different content | `REQUEST_ID_CONFLICT`, with the existing `ecriture` |
| 6 | An exercice exists | `NO_EXERCICE` — the message says to open one with `POST /exercice` (`luca_open_exercice`) |
| 7 | `date` lies in an exercice, that exercice is open, and `date` is after its lock, if any | `DATE_OUTSIDE_EXERCICE`, the message lists the open exercices and their locks; `EXERCICE_CLOSED`; `DATE_LOCKED`, the message gives the lock |
| 8 | The journal exists | `UNKNOWN_JOURNAL` |
| 9 | Every compte exists | `UNKNOWN_COMPTE`, one per ligne |
| 10 | `annule`, if present, is the `id` of an existing écriture, not yet cancelled, of which this one is the exact inverse | `ANNULE_NOT_FOUND`, `ANNULE_ALREADY_USED`, `ANNULE_NOT_INVERSE` |

Rule 6 is the first error every new société meets: a société starts with no exercice, no journal, no compte (ADR [0004](../decisions/0004-a-societe-starts-empty.md)).

Accepted: `num` is `1 + max(num)` for the journal within the exercice holding `date`, or `1`; `valid_date` is the day of acceptance in Europe/Paris — the day the books are kept in, whatever the host's clock is set to. Response, `200`:

```json
{
  "societe": {"siren": "123456789", "name": "ACME"},
  "replay": false,
  "ecriture": {
    "id": 1, "journal": "VE", "num": 1,
    "exercice": {"date_start": "2025-01-01", "date_end": "2025-12-31"},
    "date": "2025-01-15",
    "piece": {"ref": "F2025-001", "date": "2025-01-15"},
    "lib": "Facture F2025-001", "valid_date": "2025-01-20",
    "request_id": "2025-01-15-F2025-001", "annule": null,
    "lignes": [
      {"compte": "411000", "lib": null, "debit": "1200.00"},
      {"compte": "706000", "lib": null, "credit": "1000.00"},
      {"compte": "445710", "lib": "TVA 20 %", "credit": "200.00"}
    ]
  }
}
```

A replay returns the same object with `replay: true`. The `ecriture` object is the same wherever an écriture is returned: an optional input comes back as `null` when absent, a ligne carries the one side it has, `exercice` is the one holding `date`, `annule` is the `id` of the écriture cancelled or `null`.

## `POST /compte` — `luca_add_compte`

`{"numero": "411000", "lib": "Clients"}`. Refuses a `numero` shorter than three characters (`INVALID_COMPTE`) and an existing `numero` (`COMPTE_EXISTS`, the message gives the existing label). Response: `{"societe": …, "compte": {"numero", "lib"}}`. Stored as given.

## `POST /journal` — `luca_add_journal`

`{"code": "VE", "lib": "Ventes"}`. Refuses an existing `code` (`JOURNAL_EXISTS`). Response: `{"societe": …, "journal": {"code", "lib"}}`. Stored as given.

## `POST /exercice` — `luca_open_exercice`

`{"date_start": "2025-01-01", "date_end": "2025-12-31"}` opens the next exercice. The first is free; after it, `date_start` must be the day after the last exercice's `date_end` — no gap, no overlap, no going back (`EXERCICE_NOT_CONTIGUOUS`, the message gives the expected day). Refuses `date_end` before `date_start` (`INVALID_EXERCICE`). No rule on the length. Several exercices may be open at once — the year turns before the previous one is done — and `/add` accepts a date inside any open one. Response: `{"societe": …, "exercice": {"date_start", "date_end", "closed": false, "locked_through": null}}`.

## `POST /lock` — `luca_lock`

`{"date_end": "2026-12-31", "locked_through": "2026-08-31"}` names the exercice by its last day, as `/close` does, and locks it through a day: from then on no écriture dated on or before `locked_through` is accepted in it (`DATE_LOCKED` on `/add`). A month whose books are validated and whose TVA is declared does not move by accident.

The lock moves while the exercice is open: forward as months are validated, back to reopen days, `null` to unlock. The same lock again is accepted and changes nothing. It is a guard against a mis-dated écriture, not a seal: luca does no authorisation (ADR [0003](../decisions/0003-auth-is-delegated.md)), and a client that can lock can unlock. The seal is the clôture, `/close`, and it is final (ADR [0008](../decisions/0008-an-exercice-is-locked-through-a-day.md)).

A mistake in a locked month is corrected either by an inverse écriture dated after the lock ([annule.md](annule.md)) or by moving the lock; the tool on top chooses. Each exercice has its own lock: exercice N stays open, unlocked, for its bilan while the months of N+1 are locked one by one.

Refuses a `date_end` that ends no exercice (`EXERCICE_NOT_FOUND`), a closed exercice (`EXERCICE_CLOSED`), and a `locked_through` that is not a day of that exercice (`INVALID_LOCK`). Response: `{"societe": …, "exercice": {"date_start", "date_end", "closed": false, "locked_through"}}`.

## `POST /close` — `luca_close_exercice`

`{"date_end": "2025-12-31"}` names the exercice by its last day and closes it, for good: no écriture is accepted in it afterwards, and a mistake in it is corrected by an inverse écriture dated in an open exercice ([annule.md](annule.md)). There is no reopening. Its lock, if any, stays where it was: a closed exercice refuses every écriture anyway, and its lock no longer moves. Refuses a `date_end` that ends no exercice (`EXERCICE_NOT_FOUND`), an exercice already closed (`EXERCICE_CLOSED`), and closing out of order — an older exercice still open (`EXERCICE_ORDER`, the message names it). Response: `{"societe": …, "exercice": {"date_start", "date_end", "closed": true, "locked_through"}}`.

## Error codes

| Code | Endpoint | Meaning |
|---|---|---|
| `INVALID_JSON` | all, HTTP | the body is not a JSON object: malformed, not UTF-8, duplicate key, `NaN` |
| `INVALID_SHAPE` | all | a key is missing, unknown, of the wrong type, blank, or malformed; the message names it |
| `UNBALANCED` | `/add` | debits and credits differ |
| `REQUEST_ID_CONFLICT` | `/add` | same `request_id`, different content; carries `ecriture` |
| `NO_EXERCICE` | `/add` | no exercice yet; open one with `POST /exercice` |
| `DATE_OUTSIDE_EXERCICE` | `/add` | `date` is in no exercice; the message lists the open ones |
| `EXERCICE_CLOSED` | `/add`, `/lock`, `/close` | the exercice holding `date` is closed; the exercice is closed, its lock does not move; the exercice is already closed |
| `DATE_LOCKED` | `/add` | `date` is on or before the lock of its exercice; the message gives the lock |
| `UNKNOWN_JOURNAL` | `/add` | the journal does not exist |
| `UNKNOWN_COMPTE` | `/add` | a compte does not exist; one error per ligne |
| `ANNULE_NOT_FOUND` | `/add` | `annule` names no écriture |
| `ANNULE_ALREADY_USED` | `/add` | that écriture is already cancelled |
| `ANNULE_NOT_INVERSE` | `/add` | this écriture is not the exact inverse |
| `INVALID_COMPTE` | `/compte` | `numero` shorter than three characters |
| `COMPTE_EXISTS` | `/compte` | the `numero` exists |
| `JOURNAL_EXISTS` | `/journal` | the `code` exists |
| `INVALID_EXERCICE` | `/exercice` | `date_end` before `date_start` |
| `EXERCICE_NOT_CONTIGUOUS` | `/exercice` | `date_start` is not the day after the last exercice ends |
| `INVALID_LOCK` | `/lock` | `locked_through` is not a day of the exercice ending on `date_end` |
| `EXERCICE_NOT_FOUND` | `/lock`, `/close` | no exercice ends on `date_end` |
| `EXERCICE_ORDER` | `/close` | an older exercice is still open |
| `SQL_DENIED` | `/query` | the statement is not a read ([query.md](query.md)) |
| `SQL_BUDGET` | `/query` | the statement exceeded its opcode budget |
| `SQL_ERROR` | `/query` | SQLite refused the statement; the message is SQLite's |
| `INTERNAL_ERROR` | all | luca itself failed; the message names the exception, the traceback is on stderr; nothing was written |

## Log

luca does no authentication: it trusts whoever reaches it, and a tunnel or reverse proxy in front does the rest (ADR [0003](../decisions/0003-auth-is-delegated.md)). It logs one line per request on stdout, outside the store: the route or tool, the client identity if the proxy set an `X-Forwarded-User` header (`-` otherwise), the `request_id` for `/add`, and the result — `accepted VE/1`, `replay VE/1`, `refused NO_EXERCICE,UNKNOWN_JOURNAL`, `opened 2026-01-01 → 2026-12-31`, `locked 2026-01-01 → 2026-12-31 through 2026-08-31`, `unlocked 2026-01-01 → 2026-12-31`, `closed 2025-01-01 → 2025-12-31`, `ok rows=12`, `failed OperationalError: disk I/O error`. Tracebacks go to stderr, never to stdout. A line is one line: a control character in a value the client chose — the identity, the `request_id`, a journal code — is escaped, so a client cannot forge a line.
