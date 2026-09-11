"""Every test talks to a real luca server, on a store in tmp_path, over HTTP or MCP."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp_types import CallToolResult, Implementation, Tool

from luca import server
from luca.store import Store

SIREN = "123456789"
NAME = "ACME"
SOCIETE = {"siren": SIREN, "name": NAME}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    """A fresh store: identity and nothing else."""
    s = Store.create(tmp_path / "acme.db", siren=SIREN, name=NAME)
    yield s
    s.close()


@pytest.fixture
def url(store: Store) -> Iterator[str]:
    """A luca server on that store, on a free port."""
    config = uvicorn.Config(
        server.build(store), host="127.0.0.1", port=0, log_level="warning", access_log=False
    )
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert thread.is_alive() and time.monotonic() < deadline, "server did not start"
        time.sleep(0.005)
    port = srv.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.should_exit = True
        thread.join(5)


@pytest.fixture
def http(url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=url) as client:
        yield client


@pytest.fixture
def books(http: httpx.Client) -> httpx.Client:
    """A société with an exercice, two journaux and three comptes."""
    assert (
        http.post(
            "/exercice", json={"date_start": "2025-01-01", "date_end": "2025-12-31"}
        ).status_code
        == 200
    )
    for code, lib in (("VE", "Ventes"), ("AC", "Achats")):
        assert http.post("/journal", json={"code": code, "lib": lib}).status_code == 200
    for numero, lib in (("411000", "Clients"), ("706000", "Prestations"), ("445710", "TVA")):
        assert http.post("/compte", json={"numero": numero, "lib": lib}).status_code == 200
    return http


def document(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "request_id": "2025-01-15-F2025-001",
        "journal": "VE",
        "date": "2025-01-15",
        "piece": {"ref": "F2025-001", "date": "2025-01-15"},
        "lib": "Facture F2025-001",
        "lignes": [
            {"compte": "411000", "debit": "1200.00"},
            {"compte": "706000", "credit": "1000.00"},
            {"compte": "445710", "lib": "TVA 20 %", "credit": "200.00"},
        ],
    }
    doc.update(overrides)
    return doc


def codes(response: httpx.Response) -> list[str]:
    """The error codes of a refusal; asserts it is one."""
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["societe"] == SOCIETE
    return [error["code"] for error in body["errors"]]


def messages(response: httpx.Response) -> list[str]:
    assert response.status_code == 400, response.text
    return [error["message"] for error in response.json()["errors"]]


def rows(http: httpx.Client, sql: str) -> list[list[Any]]:
    response = http.post("/query", json={"sql": sql})
    assert response.status_code == 200, response.text
    result: list[list[Any]] = response.json()["rows"]
    return result


def mcp_call(url: str, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    async def go() -> CallToolResult:
        async with Client(f"{url}/mcp") as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(go())


def mcp_tools(url: str) -> tuple[Implementation | None, str | None, list[Tool]]:
    async def go() -> tuple[Implementation | None, str | None, list[Tool]]:
        async with Client(f"{url}/mcp") as client:
            listed = await client.list_tools()
            return client.server_info, client.instructions, listed.tools

    return asyncio.run(go())
