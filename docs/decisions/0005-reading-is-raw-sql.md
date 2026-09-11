# 0005 — Reading is raw SQL, read only

Date: 2026-09-11
Status: accepted

## Context

What a client wants to read from the books is open-ended: a balance, a grand livre, one écriture, the last `num` of a journal, the list of comptes, a check that a pièce was already recorded. Each is a query; none is special.

## Decision

luca has one read endpoint, `/query` (`luca_query`), which takes a SQL statement and returns rows. The schema is the read API and is documented as such. The read path cannot write: a read-only connection, `query_only`, and a SQLite authorizer that refuses every action except reading — including `ATTACH` and `PRAGMA` — plus an opcode budget and a row cap. luca never inspects the SQL text.

## Alternatives considered

- **One endpoint per report.** Each is a projection of the same tables; the list would never be complete, and a language model can write the query faster than we can add the endpoint.
- **A query language of our own, or a filter object.** A subset of SQL with a new syntax to learn.
- **Filtering the SQL text — keywords, comments, statement prefixes.** It is bypassable by construction; the authorizer sees the compiled statement, which is not.

## Consequences

- The schema is public and stable; a column rename is an API change.
- Presentation — euros from centimes, sorting, formatting — is the client's.
- A client can discover the schema from `sqlite_master` without reading the spec.
