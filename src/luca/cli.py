"""``luca serve``: the only command (docs/spec/startup.md)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from luca import __version__, server
from luca.store import Store, StoreError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luca",
        description=(
            "luca keeps the books of one société, on one SQLite file that only luca writes."
        ),
    )
    parser.add_argument("--version", action="version", version=f"luca {__version__}")
    commands = parser.add_subparsers(title="commands", metavar="<command>", required=True)
    serve = commands.add_parser(
        "serve",
        help="serve one société: HTTP and MCP on one port",
        description="Serve one société: HTTP routes and MCP tools (/mcp) on one port."
        " Creates the store if the file does not exist.",
    )
    serve.add_argument("--db", required=True, type=Path, metavar="<path>", help="the store")
    serve.add_argument("--siren", metavar="<SIREN>", help="nine digits; required to create")
    serve.add_argument("--name", metavar="<name>", help="the société's name; required to create")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    return parser


def open_store(db: Path, *, siren: str | None, name: str | None) -> Store:
    """Open ``db``, or create it when it does not exist — which needs the identity."""
    if db.exists():
        return Store.open(db, siren=siren, name=name)
    if siren is None or name is None:
        raise StoreError(f"{db} does not exist: --siren and --name are required to create it")
    return Store.create(db, siren=siren, name=name)


def configure_logging() -> None:
    """luca's one line per request on stdout; everything else at WARNING on stderr."""
    logging.basicConfig(level=logging.WARNING)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    luca = logging.getLogger("luca")
    luca.addHandler(handler)
    luca.setLevel(logging.INFO)
    luca.propagate = False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        store = open_store(args.db, siren=args.siren, name=args.name)
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        uvicorn.run(server.build(store), host=args.host, port=args.port, access_log=False)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
