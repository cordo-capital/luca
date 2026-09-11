# Starting a société

```
luca serve --db <path> [--siren <SIREN> --name <name>] [--host 127.0.0.1] [--port 8000]
```

`serve` is the only command. It opens or creates the store, then listens for HTTP and MCP on one port ([endpoints.md](endpoints.md)) until stopped.

## A new file

If `<path>` does not exist, `serve` creates it: schema, `application_id`, `user_version`, and the identity of the société from `--siren` (nine digits) and `--name`. Both are required to create; without them `serve` refuses, prints one line on stderr, exits `1`, and no file is created. Nothing else is written: no plan comptable, no journal, no exercice.

A société starts empty and grows through the API. The first `/add` on a new société is refused with `NO_EXERCICE`, whose message says what to do; a journal and comptes are needed next. Pre-filling a plan comptable or a set of journaux is a choice luca does not make (ADR [0004](../decisions/0004-a-societe-starts-empty.md)): an onboarding tool built on top of luca does it, by calling `/exercice`, `/journal` and `/compte`.

## An existing file

If `<path>` exists, `serve` checks before anything else that it is a luca store — `PRAGMA application_id` is `0x4C554341` — and that its `PRAGMA user_version` is not above the latest migration this luca ships. Otherwise it refuses to open it, changes nothing, prints one line on stderr and exits `1`. Pending migrations are then applied.

`--siren` and `--name` are not needed to open an existing file; if given, they must match the identity in the file, else `serve` refuses. The file name means nothing; the identity is inside.

## One process, one société

One server is one société is one file. There is no multi-tenant mode and no switch between sociétés (ADR [0002](../decisions/0002-one-file-per-societe.md)). N sociétés are N processes on N ports, run by whatever supervises processes on the host; that lives outside luca.

## Trust

luca does no authentication and no authorisation: it trusts whoever reaches it (ADR [0003](../decisions/0003-auth-is-delegated.md)). Bind it to `127.0.0.1` and put a tunnel or a reverse proxy in front for anything beyond the local machine. luca does not validate the `Host` or `Origin` header either; the proxy is the boundary. It logs one line per request on stdout, with the `X-Forwarded-User` header when the proxy sets one.

## The clock

luca knows days, not instants. The one place it reads the clock is `valid_date`, the day of acceptance of an écriture, taken in Europe/Paris whatever the host's time zone. The host must have the IANA time zone database, as Linux and macOS do; `serve` fails at startup otherwise.

## Runtime

Python 3.12 or later. Runtime dependencies: the MCP Python SDK, an ASGI server (uvicorn), and what they pull in. Nothing else.
