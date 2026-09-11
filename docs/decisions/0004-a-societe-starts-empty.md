# 0004 — A société starts empty

Date: 2026-09-11
Status: accepted

## Context

Every French company uses the plan comptable général, but no two use the same subset of it, the same labels, or the same journaux. A default is right for nobody and would have to be edited by everybody — and luca has no edit.

## Decision

`luca serve` creates a store with the identity of the société and nothing else: no compte, no journal, no exercice. The société grows through `/exercice`, `/journal` and `/compte`. Pre-filling is the job of an onboarding tool built on top of luca, which talks to those endpoints and knows the company.

The first `/add` on a new société is refused with `NO_EXERCICE`, a dedicated code whose message says what to do.

## Alternatives considered

- **Shipping the plan comptable général and standard journaux.** Hundreds of comptes nobody uses in every store, labels that do not match the company's, and a table that can only be added to.
- **A `--plan` or `--template` option on `serve`.** A second input format to specify, version and test, for what five calls to the API do.

## Consequences

- Onboarding is a client concern; luca's contract for it is the three endpoints.
- A test that needs books builds them through the API.
