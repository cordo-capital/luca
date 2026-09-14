# 0006 — Tool and route names are frozen

Date: 2026-09-11
Status: accepted

## Context

luca's clients are programs and prompts. A renamed tool breaks every script and every system prompt that named it, silently, and the breakage shows up as a language model inventing a name that used to exist.

## Decision

The six names are fixed once and never renamed: `luca_add`, `luca_query`, `luca_add_compte`, `luca_add_journal`, `luca_open_exercice`, `luca_close_exercice`, and their routes `/add`, `/query`, `/compte`, `/journal`, `/exercice`, `/close`. Error codes are part of the same contract: a code, once shipped, keeps its meaning. Messages may change.

The `luca_` prefix is there because a client may see tools from several servers at once.

## Alternatives considered

- **Versioned names (`luca_add_v2`).** A rename with a suffix.
- **Namespacing by société (`acme_add`).** The société is in the tool description and in every response; putting it in the name would make every client's configuration specific to one société.

## Consequences

- Adding a tool is possible; renaming or removing one is not.
- A change that would need a rename is a new server.
