"""HTTP routes and MCP tools: the same handlers, the same errors (docs/spec/endpoints.md).

``build`` returns the ASGI application of one société: five routes, five
tools on ``/mcp``, and the identity of the société everywhere a client can
see it. The MCP SDK validates nothing: a tool's arguments reach the handler
as they came, and the handler refuses them exactly as it refuses a request
body. Handlers run in worker threads; the store serialises writes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from luca import __version__, ledger, query
from luca.store import Store

log = logging.getLogger("luca")

IDENTITY_HEADER = "x-forwarded-user"  # set by the proxy in front, if any; logged, never stored

Handler = Callable[[Store, Any], dict[str, Any]]


# --- one dispatch for both surfaces ---------------------------------------------


def parse_body(data: bytes) -> Any:
    """The JSON object of a request body, strictly: UTF-8, no duplicate key, no NaN."""

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen[key] = value
        return seen

    def no_constant(name: str) -> Any:
        raise ValueError(f"{name} is not a number")

    try:
        document = json.loads(
            data.decode("utf-8"), parse_constant=no_constant, object_pairs_hook=no_duplicates
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ledger.Refused(
            [ledger.error("INVALID_JSON", f"not a JSON document: {exc}")]
        ) from None
    if not isinstance(document, dict):
        raise ledger.Refused([ledger.error("INVALID_JSON", "not a JSON object")])
    return document


def _summary(result: dict[str, Any]) -> str:
    if "ecriture" in result:
        ecriture = result["ecriture"]
        verb = "replay" if result["replay"] else "accepted"
        return f"{verb} {ecriture['journal']}/{ecriture['num']}"
    if "compte" in result:
        return f"added {result['compte']['numero']}"
    if "journal" in result:
        return f"added {result['journal']['code']}"
    if "exercice" in result:
        return f"opened {result['exercice']['date_start']} → {result['exercice']['date_end']}"
    return f"ok rows={len(result['rows'])}" + (" truncated" if result["truncated"] else "")


def handle(
    store: Store, name: str, load: Callable[[], Any], handler: Handler, client: str
) -> tuple[int, dict[str, Any]]:
    """Run one request: (HTTP status, response). Logs one line on stdout.

    200 is accepted, 400 refused. 500 is luca itself failing — the disk, SQLite,
    a bug: one ``INTERNAL_ERROR`` naming the exception, nothing written, and the
    traceback logged at ERROR, which ``luca serve`` sends to stderr.
    """
    societe = {"siren": store.siren, "name": store.name}
    document: Any = None
    try:
        document = load()
        result = handler(store, document)
    except ledger.Refused as exc:
        status = 400
        outcome = "refused " + ",".join(e["code"] for e in exc.errors)
        body: dict[str, Any] = {"societe": societe, "errors": exc.errors}
    except Exception as exc:
        status = 500
        failure = f"{type(exc).__name__}: {exc}"
        outcome = f"failed {failure}"
        body = {"societe": societe, "errors": [ledger.error("INTERNAL_ERROR", failure)]}
        log.error("%s client=%s %s", name, client, outcome, exc_info=exc)
    else:
        status = 200
        outcome = _summary(result)
        body = {"societe": societe, **result}
    request_id = document.get("request_id") if isinstance(document, dict) else None
    tag = f" request_id={request_id}" if isinstance(request_id, str) else ""
    log.info("%s client=%s%s %s", name, client, tag, outcome)
    return status, body


# --- the five endpoints ----------------------------------------------------------
# The JSON schemas describe the documents to clients; the handlers enforce them.

_AMOUNT = {
    "type": "string",
    "pattern": r"^[0-9]+(\.[0-9]{1,2})?$",
    "description": "A decimal string such as '1200.00'; never a number",
}
_DATE = {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", "description": "YYYY-MM-DD"}
_LIB = {"type": "string", "description": "Label"}


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class Endpoint:
    tool: str
    route: str
    handler: Handler
    description: str  # after "<name> (SIREN <siren>): "
    schema: dict[str, Any]


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        "luca_add",
        "/add",
        ledger.add,
        "record one écriture in the books, in double entry. Accepted whole — numbered, dated,"
        " immutable — or refused with nothing written and one error code per rule broken."
        " Same request_id and same content replays the original result. Optional annule"
        " '<journal>/<num>' cancels an écriture with its exact inverse.",
        _object(
            {
                "request_id": {
                    "type": "string",
                    "description": "Chosen by the caller, unique per écriture",
                },
                "journal": {"type": "string", "description": "Journal code"},
                "date": {**_DATE, "description": "YYYY-MM-DD, inside the exercice"},
                "piece": _object({"ref": {"type": "string"}, "date": _DATE}, ["ref", "date"]),
                "lib": _LIB,
                "lignes": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": ledger.MAX_LIGNES,
                    "description": f"Two to {ledger.MAX_LIGNES}; debits must equal credits",
                    "items": _object(
                        {
                            "compte": {"type": "string"},
                            "lib": _LIB,
                            "debit": _AMOUNT,
                            "credit": _AMOUNT,
                        },
                        ["compte"],
                    ),
                },
                "annule": {
                    "type": "string",
                    "pattern": r"^.+/[1-9][0-9]*$",
                    "description": "'<journal>/<num>' of the écriture this one cancels",
                },
            },
            ["request_id", "journal", "date", "piece", "lib", "lignes"],
        ),
    ),
    Endpoint(
        "luca_query",
        "/query",
        query.query,
        "read the books with one SQL statement on a read-only connection. Tables: societe,"
        " exercice, journal, compte, ecriture (journal_code, num, date, piece_ref, piece_date,"
        " lib, valid_date, request_id, annule_id), ligne (ecriture_id, idx, compte, lib, debit,"
        " credit). Amounts are integer centimes. `SELECT name, sql FROM sqlite_master` gives"
        " the schema. Values go in params, bound to the ? of the statement. At most 1000 rows.",
        _object(
            {
                "sql": {"type": "string", "description": "One SQL statement, ? for each value"},
                "params": {
                    "type": "array",
                    "items": {"type": ["string", "integer", "null"]},
                    "description": "The values bound to the ?, in order; never a float",
                },
            },
            ["sql"],
        ),
    ),
    Endpoint(
        "luca_add_compte",
        "/compte",
        ledger.add_compte,
        "add a compte to the plan comptable. Refuses an existing numero.",
        _object(
            {
                "numero": {"type": "string", "description": "Three characters or more"},
                "lib": _LIB,
            },
            ["numero", "lib"],
        ),
    ),
    Endpoint(
        "luca_add_journal",
        "/journal",
        ledger.add_journal,
        "add a journal. Refuses an existing code.",
        _object(
            {"code": {"type": "string", "description": "e.g. VE"}, "lib": _LIB}, ["code", "lib"]
        ),
    ),
    Endpoint(
        "luca_open_exercice",
        "/exercice",
        ledger.open_exercice,
        "open the exercice [date_start, date_end]. One per société; luca_add accepts only"
        " dates inside it. Refuses a second exercice.",
        _object({"date_start": _DATE, "date_end": _DATE}, ["date_start", "date_end"]),
    ),
)


# --- the application -------------------------------------------------------------


def build(store: Store) -> Starlette:
    """The ASGI application of one société: HTTP routes and MCP tools on the same port."""
    who = f"{store.name} (SIREN {store.siren})"
    by_tool = {endpoint.tool: endpoint for endpoint in ENDPOINTS}
    tools = [
        Tool(
            name=endpoint.tool,
            description=f"{who}: {endpoint.description}",
            input_schema=endpoint.schema,
        )
        for endpoint in ENDPOINTS
    ]

    async def list_tools(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def call_tool(
        ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        endpoint = by_tool.get(params.name)
        if endpoint is None:
            raise MCPError(INVALID_PARAMS, f"unknown tool {params.name!r}")
        headers = getattr(ctx.request, "headers", None)
        client = headers.get(IDENTITY_HEADER, "-") if headers is not None else "-"
        arguments = params.arguments or {}
        status, body = await run_in_threadpool(
            handle, store, endpoint.tool, lambda: arguments, endpoint.handler, client
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
            structured_content=body,
            is_error=status != 200,
        )

    def route(endpoint: Endpoint) -> Route:
        async def respond(request: Request) -> Response:
            client = request.headers.get(IDENTITY_HEADER, "-")
            data = await request.body()
            status, body = await run_in_threadpool(
                handle,
                store,
                f"POST {endpoint.route}",
                lambda: parse_body(data),
                endpoint.handler,
                client,
            )
            return JSONResponse(body, status_code=status)

        return Route(endpoint.route, respond, methods=["POST"])

    server = Server(
        name=store.name,
        version=__version__,
        instructions=(
            f"luca keeps the books of {who}: écritures in journaux, in double entry,"
            " one exercice. A société starts empty: open the exercice, add journaux and"
            " comptes, then add écritures. Read anything with luca_query."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        # Host and Origin are the proxy's business (ADR 0003); one rule for both surfaces.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=[route(endpoint) for endpoint in ENDPOINTS],
    )
