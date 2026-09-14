"""The MCP surface: same handlers, same errors, the société everywhere (docs/spec/endpoints.md)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from luca import __version__

from .conftest import NAME, SIREN, SOCIETE, document, mcp_call, mcp_tools

TOOLS = ["luca_add", "luca_query", "luca_add_compte", "luca_add_journal", "luca_open_exercice"]


def test_the_server_is_named_after_the_societe(url: str) -> None:
    info, instructions, _ = mcp_tools(url)
    assert info is not None
    assert (info.name, info.version) == (NAME, __version__)
    assert instructions is not None
    assert NAME in instructions and SIREN in instructions


def test_the_five_tools_and_only_them(url: str) -> None:
    _, _, tools = mcp_tools(url)
    assert [tool.name for tool in tools] == TOOLS


def test_every_tool_description_and_title_start_with_the_societe(url: str) -> None:
    _, _, tools = mcp_tools(url)
    for tool in tools:
        assert tool.description is not None
        assert tool.description.startswith(f"{NAME} (SIREN {SIREN}): "), tool.name
        assert tool.title is not None
        assert tool.title.startswith(f"{NAME}: "), tool.name


def test_every_tool_says_what_a_client_may_assume(url: str) -> None:
    _, _, tools = mcp_tools(url)
    hints = {}
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.annotations.open_world_hint is False, tool.name
        hints[tool.name] = (tool.annotations.read_only_hint, tool.annotations.idempotent_hint)
    assert hints == {
        "luca_add": (False, True),
        "luca_query": (True, True),
        "luca_add_compte": (False, False),
        "luca_add_journal": (False, False),
        "luca_open_exercice": (False, False),
    }


def test_luca_add_arguments_are_the_keys_of_post_add(url: str) -> None:
    _, _, tools = mcp_tools(url)
    add = next(tool for tool in tools if tool.name == "luca_add")
    assert set(add.input_schema["properties"]) == {
        "request_id",
        "journal",
        "date",
        "piece",
        "lib",
        "lignes",
        "annule",
    }
    assert set(add.input_schema["required"]) == {
        "request_id",
        "journal",
        "date",
        "piece",
        "lib",
        "lignes",
    }
    assert (
        add.input_schema["properties"]["lignes"]["items"]["properties"]["debit"]["type"] == "string"
    )
    assert add.input_schema["properties"]["lignes"]["maxItems"] == 1000
    read = next(tool for tool in tools if tool.name == "luca_query")
    assert set(read.input_schema["properties"]) == {"sql", "params"}
    assert read.input_schema["required"] == ["sql"]
    assert read.input_schema["properties"]["params"]["items"]["type"] == [
        "string",
        "integer",
        "null",
    ]


def test_a_societe_is_built_and_written_to_over_mcp(url: str) -> None:
    opened = mcp_call(
        url, "luca_open_exercice", {"date_start": "2025-01-01", "date_end": "2025-12-31"}
    )
    assert not opened.is_error
    assert opened.structured_content == {
        "societe": SOCIETE,
        "exercice": {"date_start": "2025-01-01", "date_end": "2025-12-31"},
    }
    assert not mcp_call(url, "luca_add_journal", {"code": "VE", "lib": "Ventes"}).is_error
    for numero, lib in (("411000", "Clients"), ("706000", "Prestations"), ("445710", "TVA")):
        assert not mcp_call(url, "luca_add_compte", {"numero": numero, "lib": lib}).is_error
    added = mcp_call(url, "luca_add", document())
    assert not added.is_error, added.content
    assert added.structured_content is not None
    assert added.structured_content["societe"] == SOCIETE
    assert added.structured_content["replay"] is False
    assert added.structured_content["ecriture"]["num"] == 1
    assert [block.text for block in added.content if block.type == "text"] == [
        json.dumps(added.structured_content, ensure_ascii=False)
    ]
    read = mcp_call(url, "luca_query", {"sql": "SELECT journal_code, num FROM ecriture"})
    assert read.structured_content == {
        "societe": SOCIETE,
        "columns": ["journal_code", "num"],
        "rows": [["VE", 1]],
        "truncated": False,
    }


def test_http_and_mcp_share_replay(books: httpx.Client, url: str) -> None:
    first = books.post("/add", json=document()).json()
    again = mcp_call(url, "luca_add", document())
    assert not again.is_error
    assert again.structured_content == {**first, "replay": True}


def test_http_and_mcp_refuse_with_the_same_errors(http: httpx.Client, url: str) -> None:
    over_http = http.post("/add", json=document()).json()
    over_mcp = mcp_call(url, "luca_add", document())
    assert over_mcp.is_error
    assert over_mcp.structured_content == over_http
    assert [error["code"] for error in over_http["errors"]][0] == "NO_EXERCICE"


def test_luca_query_refuses_a_write_with_the_same_code(url: str) -> None:
    result = mcp_call(url, "luca_query", {"sql": "DELETE FROM journal"})
    assert result.is_error
    assert result.structured_content is not None
    assert [error["code"] for error in result.structured_content["errors"]] == ["SQL_DENIED"]


@pytest.mark.parametrize(
    ("arguments", "reasons"),
    [
        ({"numero": 411000, "lib": "Clients"}, ["numero: not a non-empty string"]),
        ({"numero": "411000", "lib": "Clients", "extra": 1}, ["extra: unknown key"]),
        ({"numero": "411000"}, ["lib: missing"]),
        ({}, ["lib: missing", "numero: missing"]),
    ],
)
def test_the_sdk_validates_nothing_the_handler_refuses_as_over_http(
    url: str, http: httpx.Client, arguments: dict[str, Any], reasons: list[str]
) -> None:
    result = mcp_call(url, "luca_add_compte", arguments)
    assert result.is_error
    assert result.structured_content is not None
    assert [e["code"] for e in result.structured_content["errors"]] == ["INVALID_SHAPE"] * len(
        reasons
    )
    assert [e["message"] for e in result.structured_content["errors"]] == reasons
    assert result.structured_content == http.post("/compte", json=arguments).json()


def test_an_unknown_tool_is_a_protocol_error(url: str) -> None:
    with pytest.raises(ExceptionGroup) as caught:
        mcp_call(url, "luca_delete", {})
    assert caught.group_contains(MCPError, match="unknown tool 'luca_delete'")


def test_the_log_line_carries_the_forwarded_user(
    http: httpx.Client, url: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="luca")
    http.post(
        "/journal", json={"code": "VE", "lib": "Ventes"}, headers={"X-Forwarded-User": "alice"}
    )
    http.post("/add", json=document())

    async def over_mcp() -> None:
        async with (
            httpx2.AsyncClient(headers={"X-Forwarded-User": "bob"}) as client,
            Client(streamable_http_client(f"{url}/mcp", http_client=client)) as mcp,
        ):
            await mcp.call_tool("luca_query", {"sql": "SELECT 1"})

    asyncio.run(over_mcp())
    assert caplog.messages == [
        "POST /journal client=alice added VE",
        "POST /add client=- request_id=2025-01-15-F2025-001"
        " refused NO_EXERCICE,UNKNOWN_COMPTE,UNKNOWN_COMPTE,UNKNOWN_COMPTE",
        "luca_query client=bob ok rows=1",
    ]
