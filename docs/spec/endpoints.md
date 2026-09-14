# Endpoints

luca is one server on one port, reachable two ways with the same handlers and the same errors: HTTP routes, and MCP tools over streamable HTTP mounted on `/mcp`. The names below are a public API: fixed once, never renamed (ADR [0006](../decisions/0006-tool-names-are-frozen.md)).

| MCP tool | Route | Effect |
|---|---|---|
| `luca_add` | `POST /add` | one écriture, accepted or refused in one transaction |
| `luca_query` | `POST /query` | raw SQL with bound `params`, read only, rows as JSON ([query.md](query.md)) |
| `luca_add_compte` | `POST /compte` | adds a compte; refuses a duplicate |
| `luca_add_journal` | `POST /journal` | adds a journal; refuses a duplicate |
| `luca_open_exercice` | `POST /exercice` | opens the single exercice; refuses if one exists |

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

Each tool carries a `title` beginning with the société's name and the annotations a client may rely on: `readOnlyHint` true on `luca_query` only; `destructiveHint` false on every tool, since luca never modifies or deletes; `idempotentHint` true on `luca_add`, whose repeat is a replay ([canonical.md](canonical.md)), and on `luca_query`, false on the three others, whose repeat is a refusal; `openWorldHint` false everywhere, nothing reaches beyond the store.

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
| 7 | `date` lies in `[date_start, date_end]` | `DATE_OUTSIDE_EXERCICE` |
| 8 | The journal exists | `UNKNOWN_JOURNAL` |
| 9 | Every compte exists | `UNKNOWN_COMPTE`, one per ligne |
| 10 | `annule`, if present, names an existing écriture, not yet cancelled, of which this one is the exact inverse | `ANNULE_NOT_FOUND`, `ANNULE_ALREADY_USED`, `ANNULE_NOT_INVERSE` |

Rule 6 is the first error every new société meets: a société starts with no exercice, no journal, no compte (ADR [0004](../decisions/0004-a-societe-starts-empty.md)).

Accepted: `num` is `1 + max(num)` for the journal, or `1`; `valid_date` is the day of acceptance in Europe/Paris — the day the books are kept in, whatever the host's clock is set to. Response, `200`:

```json
{
  "societe": {"siren": "123456789", "name": "ACME"},
  "replay": false,
  "ecriture": {
    "id": 1, "journal": "VE", "num": 1, "date": "2025-01-15",
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

A replay returns the same object with `replay: true`. The `ecriture` object is the same wherever an écriture is returned: an optional input comes back as `null` when absent, a ligne carries the one side it has, `annule` is the `<journal>/<num>` reference or `null`.

## `POST /compte` — `luca_add_compte`

`{"numero": "411000", "lib": "Clients"}`. Refuses a `numero` shorter than three characters (`INVALID_COMPTE`) and an existing `numero` (`COMPTE_EXISTS`, the message gives the existing label). Response: `{"societe": …, "compte": {"numero", "lib"}}`. Stored as given.

## `POST /journal` — `luca_add_journal`

`{"code": "VE", "lib": "Ventes"}`. Refuses an existing `code` (`JOURNAL_EXISTS`). Response: `{"societe": …, "journal": {"code", "lib"}}`. Stored as given.

## `POST /exercice` — `luca_open_exercice`

`{"date_start": "2025-01-01", "date_end": "2025-12-31"}`. Refuses `date_end` before `date_start` (`INVALID_EXERCICE`) and a second exercice (`EXERCICE_EXISTS`, the message gives the existing one). A store holds one exercice; `/add` accepts only dates inside it. Response: `{"societe": …, "exercice": {"date_start", "date_end"}}`.

## Error codes

| Code | Endpoint | Meaning |
|---|---|---|
| `INVALID_JSON` | all, HTTP | the body is not a JSON object: malformed, not UTF-8, duplicate key, `NaN` |
| `INVALID_SHAPE` | all | a key is missing, unknown, of the wrong type, blank, or malformed; the message names it |
| `UNBALANCED` | `/add` | debits and credits differ |
| `REQUEST_ID_CONFLICT` | `/add` | same `request_id`, different content; carries `ecriture` |
| `NO_EXERCICE` | `/add` | no exercice yet; open one with `POST /exercice` |
| `DATE_OUTSIDE_EXERCICE` | `/add` | `date` is outside the exercice |
| `UNKNOWN_JOURNAL` | `/add` | the journal does not exist |
| `UNKNOWN_COMPTE` | `/add` | a compte does not exist; one error per ligne |
| `ANNULE_NOT_FOUND` | `/add` | `annule` names no écriture |
| `ANNULE_ALREADY_USED` | `/add` | that écriture is already cancelled |
| `ANNULE_NOT_INVERSE` | `/add` | this écriture is not the exact inverse |
| `INVALID_COMPTE` | `/compte` | `numero` shorter than three characters |
| `COMPTE_EXISTS` | `/compte` | the `numero` exists |
| `JOURNAL_EXISTS` | `/journal` | the `code` exists |
| `INVALID_EXERCICE` | `/exercice` | `date_end` before `date_start` |
| `EXERCICE_EXISTS` | `/exercice` | an exercice exists |
| `SQL_DENIED` | `/query` | the statement is not a read ([query.md](query.md)) |
| `SQL_BUDGET` | `/query` | the statement exceeded its opcode budget |
| `SQL_ERROR` | `/query` | SQLite refused the statement; the message is SQLite's |
| `INTERNAL_ERROR` | all | luca itself failed; the message names the exception, the traceback is on stderr; nothing was written |

## Log

luca does no authentication: it trusts whoever reaches it, and a tunnel or reverse proxy in front does the rest (ADR [0003](../decisions/0003-auth-is-delegated.md)). It logs one line per request on stdout, outside the store: the route or tool, the client identity if the proxy set an `X-Forwarded-User` header (`-` otherwise), the `request_id` for `/add`, and the result — `accepted VE/1`, `replay VE/1`, `refused NO_EXERCICE,UNKNOWN_JOURNAL`, `ok rows=12`, `failed OperationalError: disk I/O error`. Tracebacks go to stderr, never to stdout.
