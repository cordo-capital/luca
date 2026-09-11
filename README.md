# luca

luca keeps the books of one société: écritures in journaux, in double entry, on one SQLite file that only luca writes.

It is a server. A script, a human in a chat, or a language model talks to it over HTTP or MCP on one port, sends one écriture at a time, and gets it accepted — numbered, dated, immutable — or refused with nothing written and a stable error code. Reading is raw SQL, read only.

```sh
luca serve --db acme.db --siren 123456789 --name ACME
```

That is the whole command line. A missing file is created with the identity of the société and nothing else; the société grows through the API.

## The API

| MCP tool | Route | Effect |
|---|---|---|
| `luca_add` | `POST /add` | one écriture, accepted or refused in one transaction |
| `luca_query` | `POST /query` | raw SQL, read only, rows as JSON |
| `luca_add_compte` | `POST /compte` | adds a compte; refuses a duplicate |
| `luca_add_journal` | `POST /journal` | adds a journal; refuses a duplicate |
| `luca_open_exercice` | `POST /exercice` | opens the single exercice; refuses if one exists |

MCP is streamable HTTP on `/mcp`, same port, same handlers, same errors. The names are frozen.

```sh
curl -s localhost:8000/exercice -d '{"date_start":"2025-01-01","date_end":"2025-12-31"}'
curl -s localhost:8000/journal  -d '{"code":"VE","lib":"Ventes"}'
curl -s localhost:8000/compte   -d '{"numero":"411000","lib":"Clients"}'
curl -s localhost:8000/compte   -d '{"numero":"706000","lib":"Prestations"}'
curl -s localhost:8000/add -d '{"request_id":"F2025-001","journal":"VE","date":"2025-01-15",
  "piece":{"ref":"F2025-001","date":"2025-01-15"},"lib":"Facture F2025-001",
  "lignes":[{"compte":"411000","debit":"1200.00"},{"compte":"706000","credit":"1200.00"}]}'
curl -s localhost:8000/query -d '{"sql":"SELECT journal_code, num, lib FROM ecriture"}'
```

Every response carries `societe`. A refusal is a `400` with a list of errors, each with a stable `code`. The first error every new société meets is `NO_EXERCICE`.

## How it works

- **One server, one société, one file.** luca is the only writer: one connection, one lock, one immediate transaction per écriture. N sociétés are N processes.
- **Replay-safe.** Every écriture carries a `request_id`. Same id and same canonical content is a replay of the original result; same id and different content is a conflict that returns the existing écriture.
- **Exact amounts.** Decimal strings on the wire, integer centimes in the store, never a float.
- **Immutable.** Accepted écritures never change; the only correction is an inverse écriture linked by `annule`.
- **Read only means read only.** `/query` runs on a read-only connection with a SQLite authorizer, an opcode budget and a row cap. The SQL text is never inspected.
- **No auth.** luca trusts whoever reaches it and logs one line per request on stdout. A tunnel or reverse proxy in front is the boundary.

## What luca does not do

Collecting documents, interpreting them, choosing comptes or tax treatment, holding a brouillard, reviewing, lettrage, FEC, reports, filing, authentication, a user interface. Those belong to tools built on top of luca. Acceptance means the rules passed, nothing more.

## Run

The image is published on GHCR for `linux/amd64` and `linux/arm64` at every `v*` tag. It runs as uid 1000 and keeps the store under `/data`: give it a volume and publish the port.

With Docker, a named volume:

```sh
docker run --rm -v luca:/data -p 8000:8000 ghcr.io/cordo-capital/luca --db /data/luca.db --siren 123456789 --name ACME
```

With Apple Containers, a host directory, because named volumes are mounted as root there:

```sh
mkdir -p data
container run --rm -v "$PWD/data:/data" -p 8000:8000 ghcr.io/cordo-capital/luca --db /data/luca.db --siren 123456789 --name ACME
```

`--siren` and `--name` create the store on the first run. Afterwards the image's default arguments, `--db /data/luca.db`, are enough: stop after the image name.

### From source

For working on luca: Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/cordo-capital/luca
cd luca
uv sync
uv run luca serve --db acme.db --siren 123456789 --name ACME
```

Runtime dependencies: the MCP Python SDK, uvicorn, and what they pull in.

## Documentation

- [`docs/spec/`](docs/spec/) — [the store](docs/spec/store.md), [the endpoints and their rules](docs/spec/endpoints.md), [canonical content, replay and conflict](docs/spec/canonical.md), [`annule`](docs/spec/annule.md), [reading](docs/spec/query.md), [starting a société](docs/spec/startup.md), [the glossary](docs/spec/glossary.md).
- [`docs/decisions/`](docs/decisions/) — architecture decision records.
- [`AGENTS.md`](AGENTS.md) — the invariants any contributor, human or model, must respect.

French regulatory terms (société, écriture, journal, compte, exercice, pièce, EcritureNum, ValidDate, SIREN, …) are kept in French throughout. See the [glossary](docs/spec/glossary.md).

## How this project is built

The code in this repository is written by language models, reviewed and tested by a human. Specifications and tests are the source of truth; the code is what passes them. Something went wrong while using luca? Open an issue with what you sent, what you expected and what happened, with synthetic data. The issue tracker holds no roadmap.

## License

[MIT](LICENSE) — © Cordo Capital.
