"""The store: one SQLite file, one société, one writer (docs/spec/store.md).

This module owns the file, its schema, the single write connection and the
read-only connections. Migrations are the ``NNNN-*.sql`` files in
``migrations/``, applied in order inside one transaction each;
``PRAGMA user_version`` records the last one applied.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

APPLICATION_ID = 0x4C554341  # 'LUCA'
_MIGRATION_NAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.sql$")
_SIREN = re.compile(r"^\d{9}$")
_MIN_SQLITE = (3, 37, 0)  # STRICT tables
_BUSY_TIMEOUT = 5.0

# The read path: everything the authorizer lets through (docs/spec/query.md).
_READ_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)
_READ_PRAGMAS = frozenset({"user_version", "application_id"})


class StoreError(Exception):
    """The file cannot be used as a luca store."""


def migrations() -> list[tuple[int, str]]:
    """Every shipped migration as ``(version, sql)``, numbered 1..n without gaps."""
    found: dict[int, str] = {}
    for entry in resources.files("luca.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match is None:
            continue
        version = int(match.group(1))
        if version in found:
            raise StoreError(f"two migrations carry version {version}")
        found[version] = entry.read_text(encoding="utf-8")
    versions = sorted(found)
    if versions != list(range(1, len(versions) + 1)):
        raise StoreError(f"migrations are not numbered 1..n without gaps: {versions}")
    return [(v, found[v]) for v in versions]


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations; return the versions applied. Refuse a store from the future."""
    shipped = migrations()
    latest = shipped[-1][0]
    current = schema_version(conn)
    if current > latest:
        raise StoreError(
            f"store is at schema version {current}, this luca knows up to {latest}: upgrade luca"
        )
    applied: list[int] = []
    for version, sql in shipped:
        if version <= current:
            continue
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        applied.append(version)
    return applied


def _connect(path: Path, mode: str) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < _MIN_SQLITE:
        raise StoreError(f"SQLite {sqlite3.sqlite_version} is too old, need >= 3.37")
    try:
        return sqlite3.connect(
            path.resolve().as_uri() + f"?mode={mode}",
            uri=True,
            isolation_level=None,  # no implicit transactions: BEGIN and COMMIT are explicit
            check_same_thread=False,  # one connection, serialised by a lock, used from workers
            timeout=_BUSY_TIMEOUT,
        )
    except sqlite3.OperationalError as exc:
        # a missing directory, a directory luca cannot write to, a file it cannot read
        raise StoreError(f"cannot open {path}: {exc}") from None


def _authorize(
    action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None
) -> int:
    """Let reads through, refuse everything else — including ATTACH and PRAGMA writes."""
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 is not None and arg2 is None:
        return sqlite3.SQLITE_OK if arg1.lower() in _READ_PRAGMAS else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


class Store:
    """The file of one société and its single write connection."""

    def __init__(self, path: Path, conn: sqlite3.Connection, *, siren: str, name: str) -> None:
        self.path = path
        self.siren = siren
        self.name = name
        self._conn = conn
        self._lock = threading.Lock()

    @classmethod
    def create(cls, path: Path, *, siren: str, name: str) -> Store:
        """Create the store of a new société: schema and identity, nothing else."""
        if path.exists():
            raise StoreError(f"{path} already exists")
        if not _SIREN.match(siren):
            raise StoreError(f"SIREN {siren!r} is not nine digits")
        if not name.strip():
            raise StoreError("name is empty")
        conn = _connect(path, "rwc")
        try:
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            migrate(conn)
            conn.execute("INSERT INTO societe (id, siren, name) VALUES (1, ?, ?)", (siren, name))
        except sqlite3.DatabaseError as exc:
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)
            raise StoreError(f"cannot create {path}: {exc}") from exc
        return cls(path, conn, siren=siren, name=name)

    @classmethod
    def open(cls, path: Path, *, siren: str | None = None, name: str | None = None) -> Store:
        """Open an existing store, refusing a file that is not luca's or is from a newer luca."""
        if not path.is_file():
            raise StoreError(f"{path} is not a file")
        conn = _connect(path, "rw")
        try:
            try:
                application_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
            except sqlite3.DatabaseError as exc:
                raise StoreError(f"{path} is not a luca store: {exc}") from exc
            if application_id != APPLICATION_ID:
                raise StoreError(
                    f"{path} is not a luca store: application_id is {application_id:#x},"
                    f" luca's is {APPLICATION_ID:#x}"
                )
            migrate(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            row = conn.execute("SELECT siren, name FROM societe WHERE id = 1").fetchone()
            if row is None:
                raise StoreError(f"{path} holds no société")
            stored_siren, stored_name = str(row[0]), str(row[1])
            if siren is not None and siren != stored_siren:
                raise StoreError(f"{path} is the store of SIREN {stored_siren}, not {siren}")
            if name is not None and name != stored_name:
                raise StoreError(f"{path} is the store of {stored_name!r}, not {name!r}")
        except BaseException:
            conn.close()
            raise
        return cls(path, conn, siren=stored_siren, name=stored_name)

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One immediate transaction under the process lock: all of it is written, or nothing."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def read(self) -> sqlite3.Connection:
        """A fresh read-only connection: ``mode=ro``, ``query_only``, reads-only authorizer."""
        conn = _connect(self.path, "ro")
        conn.execute("PRAGMA query_only = 1")
        conn.set_authorizer(_authorize)
        return conn

    def close(self) -> None:
        self._conn.close()
