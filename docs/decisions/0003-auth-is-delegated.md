# 0003 — Authentication is not luca's job

Date: 2026-09-11
Status: accepted

## Context

luca is reached by scripts, chat clients and language models, on a local machine or through a tunnel. Each deployment has its own way of saying who a caller is: a tunnel with an identity layer, a reverse proxy with OAuth, an SSH port forward, nothing at all on a laptop.

## Decision

luca trusts whoever reaches it. It does no authentication, no authorisation, and no validation of the `Host` or `Origin` header. A tunnel or a reverse proxy in front is the boundary, and it is the deployment's choice. luca only logs, on stdout and outside the store: the route or tool, the `X-Forwarded-User` header when the proxy sets one, the `request_id`, and the result.

## Alternatives considered

- **A token or password in luca.** A second credential store to manage per société, and a scheme that fits none of the deployments above better than the proxy they already have.
- **Keeping the MCP SDK's DNS-rebinding protection on the MCP route.** It validates `Host` on `/mcp` only, would leave the HTTP routes unprotected, and breaks behind a proxy that forwards the public host name. One rule for both surfaces is worth more than a half-protection on one.
- **Storing the caller's identity with each écriture.** The identity comes from a header luca cannot verify; recording it as a fact in the books would give it a weight it does not have.

## Consequences

- Bind to `127.0.0.1` unless a proxy is in front. Anything reachable from a browser without a proxy is exposed to whoever can reach it.
- Who did what is in the proxy's logs and in luca's stdout, not in the store.
