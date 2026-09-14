# 0002 — One file per société, one process per file

Date: 2026-09-11
Status: accepted

## Context

luca keeps the books of French companies, each with its own SIREN, its own plan comptable, its exercices. Several sociétés may be kept on one host, by one person or one tool.

## Decision

A luca server is one société: one process, one file, one port. The file holds the identity of its société (`siren`, `name`) and the server carries that identity everywhere a client can see it. There is no multi-tenant mode, no société identifier in requests, no switch between files. Running N sociétés is running N processes; that lives outside luca.

## Alternatives considered

- **One server, several files, a société key in each request.** Every request, every log line and every tool description would have to carry the key, and a missing or wrong key would post an écriture on the wrong books. The failure mode is the worst one a ledger can have.
- **One file holding several sociétés.** Every table gains a column, every query a clause, every backup and restore becomes partial.

## Consequences

- No multi-tenant code, ever.
- A client connected to two luca servers tells them apart by `serverInfo.name`, the tool descriptions, and `societe` in every response, without help from the client.
- Backups, moves and deletions are file operations.
