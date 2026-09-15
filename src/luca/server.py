"""HTTP routes and MCP tools: the same handlers, the same errors (docs/spec/endpoints.md).

``build`` returns the ASGI application of one société: seven routes, seven
tools on ``/mcp``, and the identity of the société everywhere a client can
see it. The MCP SDK validates nothing: a tool's arguments reach the handler
as they came, and the handler refuses them exactly as it refuses a request
body. Handlers run in worker threads; the store serialises writes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
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
    ToolAnnotations,
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


def _one_line(value: str) -> str:
    """A log line stays one line: control characters, from a client value, are escaped."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in value)


def _summary(tool: str, result: dict[str, Any]) -> str:
    """The outcome of an accepted request, as the log line says it."""
    if tool == "luca_add":
        ecriture = result["ecriture"]
        verb = "replay" if result["replay"] else "accepted"
        return f"{verb} {ecriture['journal']}/{ecriture['num']}"
    if tool == "luca_query":
        return f"ok rows={len(result['rows'])}" + (" truncated" if result["truncated"] else "")
    if tool == "luca_add_compte":
        return f"added {result['compte']['numero']}"
    if tool == "luca_add_journal":
        return f"added {result['journal']['code']}"
    exercice = result["exercice"]
    span = f"{exercice['date_start']} → {exercice['date_end']}"
    if tool == "luca_open_exercice":
        return f"opened {span}"
    if tool == "luca_close_exercice":
        return f"closed {span}"
    if exercice["locked_through"] is None:
        return f"unlocked {span}"
    return f"locked {span} through {exercice['locked_through']}"


def handle(
    store: Store, name: str, load: Callable[[], Any], endpoint: Endpoint, client: str
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
        result = endpoint.handler(store, document)
    except ledger.Refused as exc:
        status = 400
        outcome = "refused " + ",".join(e["code"] for e in exc.errors)
        body: dict[str, Any] = {"societe": societe, "errors": exc.errors}
    except Exception as exc:
        status = 500
        failure = f"{type(exc).__name__}: {exc}"
        outcome = f"failed {failure}"
        body = {"societe": societe, "errors": [ledger.error("INTERNAL_ERROR", failure)]}
        log.error("%s", _one_line(f"{name} client={client} {outcome}"), exc_info=exc)
    else:
        status = 200
        outcome = _summary(endpoint.tool, result)
        body = {"societe": societe, **result}
    request_id = document.get("request_id") if isinstance(document, dict) else None
    tag = f" request_id={request_id}" if isinstance(request_id, str) else ""
    log.info("%s", _one_line(f"{name} client={client}{tag} {outcome}"))
    return status, body


# --- the seven endpoints ---------------------------------------------------------
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
    title: str  # after "<name>: "
    annotations: ToolAnnotations
    description: str  # after "<name> (SIREN <siren>): "
    schema: dict[str, Any]


def _hints(
    *, read_only: bool = False, idempotent: bool = False, destructive: bool = False
) -> ToolAnnotations:
    """What a client may assume: nothing is ever deleted, and nothing is reached beyond the
    store. A repeat of luca_add is a replay, of luca_lock the same lock, of the others a
    refusal. Closing an exercice is the one irreversible act, and moving a lock back reopens
    days: the two a client may want to confirm."""
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        "luca_add",
        "/add",
        ledger.add,
        "add an écriture",
        _hints(idempotent=True),
        "record one écriture in the books, in double entry. Accepted whole — numbered, dated,"
        " immutable — or refused with nothing written and one error code per rule broken."
        " Same request_id and same content replays the original result. Optional annule,"
        " the id of an écriture, cancels it with its exact inverse — the only correction, and"
        " the way to correct a closed exercice from an open one.",
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
                    "type": "integer",
                    "minimum": 1,
                    "description": "The id of the écriture this one cancels",
                },
            },
            ["request_id", "journal", "date", "piece", "lib", "lignes"],
        ),
    ),
    Endpoint(
        "luca_query",
        "/query",
        query.query,
        "read the books",
        _hints(read_only=True, idempotent=True),
        "read the books with one SQL statement on a read-only connection. Tables: societe,"
        " exercice (id, date_start, date_end, closed, locked_through), journal, compte,"
        " ecriture (id, exercice_id, journal_code, num, date, piece_ref, piece_date, lib,"
        " valid_date, request_id, annule_id), ligne (ecriture_id, idx, compte, lib, debit,"
        " credit)."
        " Amounts are integer centimes. `SELECT name, sql FROM sqlite_master` gives"
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
        "add a compte",
        _hints(),
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
        "add a journal",
        _hints(),
        "add a journal. Refuses an existing code.",
        _object(
            {"code": {"type": "string", "description": "e.g. VE"}, "lib": _LIB}, ["code", "lib"]
        ),
    ),
    Endpoint(
        "luca_open_exercice",
        "/exercice",
        ledger.open_exercice,
        "open the next exercice",
        _hints(),
        "open the next exercice [date_start, date_end]: the first is free, the next starts"
        " the day after the last one ends. Several may be open at once; luca_add accepts"
        " only dates inside an open one.",
        _object({"date_start": _DATE, "date_end": _DATE}, ["date_start", "date_end"]),
    ),
    Endpoint(
        "luca_lock",
        "/lock",
        ledger.lock,
        "lock an exercice through a day",
        _hints(idempotent=True, destructive=True),
        "lock the exercice ending on date_end through locked_through: no écriture dated on or"
        " before that day is accepted afterwards, so a validated month does not move. Move the"
        " lock forward as months are validated, back to reopen days, null to unlock; the same"
        " lock again is a no-op. A closed exercice does not move.",
        _object(
            {
                "date_end": {**_DATE, "description": "The last day of the exercice to lock"},
                "locked_through": {
                    "type": ["string", "null"],
                    "pattern": _DATE["pattern"],
                    "description": "The last locked day, YYYY-MM-DD, or null to unlock",
                },
            },
            ["date_end", "locked_through"],
        ),
    ),
    Endpoint(
        "luca_close_exercice",
        "/close",
        ledger.close_exercice,
        "close an exercice",
        _hints(destructive=True),
        "close the exercice ending on date_end, for good: no écriture is accepted in it"
        " afterwards, and a mistake in it is corrected by an inverse écriture (annule) dated in"
        " an open exercice. Oldest open exercice first.",
        _object(
            {"date_end": {**_DATE, "description": "The last day of the exercice to close"}},
            ["date_end"],
        ),
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
            title=f"{store.name}: {endpoint.title}",
            description=f"{who}: {endpoint.description}",
            input_schema=endpoint.schema,
            annotations=endpoint.annotations,
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
            handle, store, endpoint.tool, lambda: arguments, endpoint, client
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
                endpoint,
                client,
            )
            return JSONResponse(body, status_code=status)

        return Route(endpoint.route, respond, methods=["POST"])

    server = Server(
        name=store.name,
        version=__version__,
        instructions=(
            f"luca keeps the books of {who}: écritures in journaux, in double entry,"
            " exercice after exercice. A société starts empty: open the first exercice, add"
            " journaux and comptes, then add écritures. Open the next exercice when the year"
            " turns. Lock an exercice through the last validated day, month after month; close"
            " it once its books are done, for good. Read anything with luca_query."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        # Host and Origin are the proxy's business (ADR 0003); one rule for both surfaces.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=[route(endpoint) for endpoint in ENDPOINTS],
    )

    # The store lives as long as the application. On shutdown — a signal, a stop — it is
    # closed here, inside the ASGI lifespan, because uvicorn re-raises the signal
    # afterwards and nothing after `uvicorn.run` gets to run: the WAL is checkpointed
    # into the file, which is then whole and alone (docs/spec/startup.md).
    sessions = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with sessions(app):
            try:
                yield
            finally:
                store.close()

    app.router.lifespan_context = lifespan
    return app
