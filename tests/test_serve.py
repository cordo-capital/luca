"""``luca serve`` and the file it owns (docs/spec/startup.md, store.md)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from luca import __version__
from luca import store as luca_store
from luca.cli import main, open_store
from luca.store import APPLICATION_ID, Store, StoreError

from .conftest import NAME, SIREN, serving

# --- the command ----------------------------------------------------------------


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_serve_is_the_only_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["--help"])
    assert exit_.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out
    for absent in ("init", "add", "query", "export", "fec"):
        assert f"  {absent} " not in out


def test_serve_needs_the_identity_to_create(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "acme.db"
    assert main(["serve", "--db", str(db)]) == 1
    assert "--siren and --name are required" in capsys.readouterr().err
    assert not db.exists()


def test_serve_refuses_a_path_it_cannot_create_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "missing" / "acme.db"
    assert main(["serve", "--db", str(db), "--siren", SIREN, "--name", NAME]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"error: cannot open {db}: ")
    assert "Traceback" not in err
    assert not tmp_path.joinpath("missing").exists()


def _empty(path: Path) -> None:
    path.write_bytes(b"")


def _text(path: Path) -> None:
    path.write_text("hello\n", encoding="utf-8")


def _other_database(path: Path) -> None:
    other = sqlite3.connect(path)
    other.executescript("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);")
    other.close()


def _other_application(path: Path) -> None:
    other = sqlite3.connect(path)
    other.executescript("PRAGMA application_id = 0x12345678; CREATE TABLE t (x INTEGER);")
    other.close()


@pytest.mark.parametrize("make", [_empty, _text, _other_database, _other_application])
def test_serve_refuses_a_file_that_is_not_a_luca_store(
    tmp_path: Path, make: Callable[[Path], None], capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "file.db"
    make(db)
    before = db.read_bytes()
    assert main(["serve", "--db", str(db), "--siren", SIREN, "--name", NAME]) == 1
    assert "not a luca store" in capsys.readouterr().err
    assert db.read_bytes() == before


def test_serve_refuses_a_store_from_a_newer_luca(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "acme.db"
    Store.create(db, siren=SIREN, name=NAME).close()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    assert main(["serve", "--db", str(db)]) == 1
    assert "upgrade luca" in capsys.readouterr().err


def test_serve_refuses_an_identity_that_does_not_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "acme.db"
    Store.create(db, siren=SIREN, name=NAME).close()
    assert main(["serve", "--db", str(db), "--siren", "987654321"]) == 1
    assert "SIREN 123456789, not 987654321" in capsys.readouterr().err
    assert main(["serve", "--db", str(db), "--name", "OTHER"]) == 1
    assert "'ACME', not 'OTHER'" in capsys.readouterr().err


def test_stopping_the_server_closes_the_store_and_leaves_one_whole_file(tmp_path: Path) -> None:
    db = tmp_path / "acme.db"
    store = Store.create(db, siren=SIREN, name=NAME)
    with serving(store) as url:
        response = httpx.post(f"{url}/journal", json={"code": "VE", "lib": "Ventes"})
        assert response.status_code == 200, response.text
        assert (tmp_path / "acme.db-wal").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["acme.db"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT code, lib FROM journal").fetchall() == [("VE", "Ventes")]
    conn.close()


# --- the file -------------------------------------------------------------------


def test_a_new_store_holds_the_identity_and_nothing_else(tmp_path: Path) -> None:
    db = tmp_path / "acme.db"
    store = open_store(db, siren=SIREN, name=NAME)
    store.close()
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA application_id").fetchone() == (APPLICATION_ID,)
    assert conn.execute("PRAGMA user_version").fetchone() == (luca_store.migrations()[-1][0],)
    assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert conn.execute("SELECT id, siren, name FROM societe").fetchall() == [(1, SIREN, NAME)]
    for table in ("exercice", "journal", "compte", "ecriture", "ligne"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    conn.close()


def test_an_existing_store_is_opened_with_or_without_its_identity(tmp_path: Path) -> None:
    db = tmp_path / "acme.db"
    open_store(db, siren=SIREN, name=NAME).close()
    for siren, name in ((None, None), (SIREN, None), (SIREN, NAME)):
        store = open_store(db, siren=siren, name=name)
        assert (store.siren, store.name) == (SIREN, NAME)
        store.close()


@pytest.mark.parametrize(
    ("siren", "name"), [("12345", "ACME"), ("12345678a", "ACME"), (SIREN, " ")]
)
def test_create_refuses_a_bad_identity_and_leaves_no_file(
    tmp_path: Path, siren: str, name: str
) -> None:
    db = tmp_path / "acme.db"
    with pytest.raises(StoreError):
        Store.create(db, siren=siren, name=name)
    assert list(tmp_path.iterdir()) == []


def test_create_refuses_an_existing_path(tmp_path: Path) -> None:
    db = tmp_path / "acme.db"
    db.write_bytes(b"not a store")
    with pytest.raises(StoreError, match="already exists"):
        Store.create(db, siren=SIREN, name=NAME)
    assert db.read_bytes() == b"not a store"


def test_migrations_are_numbered_from_one_without_gaps() -> None:
    versions = [version for version, _ in luca_store.migrations()]
    assert versions == list(range(1, len(versions) + 1))
    conn = sqlite3.connect(":memory:", isolation_level=None)
    assert luca_store.migrate(conn) == versions
    assert luca_store.migrate(conn) == []
    conn.close()


# --- what the schema enforces, against a direct connection ------------------------


@pytest.fixture
def raw(store: Store) -> sqlite3.Connection:
    conn = sqlite3.connect(store.path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        INSERT INTO journal (code, lib) VALUES ('VE', 'Ventes');
        INSERT INTO compte (numero, lib) VALUES ('411000', 'Clients'), ('706000', 'Prestations');
        INSERT INTO ecriture (id, journal_code, num, date, piece_ref, piece_date, lib, valid_date,
                              request_id, request_hash)
        VALUES (1, 'VE', 1, '2025-01-15', 'F1', '2025-01-15', 'Facture F1', '2025-01-20', 'r1',
                'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
        INSERT INTO ligne (ecriture_id, idx, compte, debit, credit)
        VALUES (1, 0, '411000', 120000, 0), (1, 1, '706000', 0, 120000);
        """
    )
    return conn


def test_accepted_ecritures_and_lignes_are_immutable(raw: sqlite3.Connection) -> None:
    for statement in (
        "UPDATE ecriture SET lib = 'changed' WHERE id = 1",
        "DELETE FROM ecriture WHERE id = 1",
        "UPDATE ligne SET debit = 1 WHERE ecriture_id = 1",
        "DELETE FROM ligne WHERE ecriture_id = 1",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            raw.execute(statement)


@pytest.mark.parametrize(("debit", "credit"), [(0, 0), (100, 100), (-1, 0), (0, -1), ("12.5", 0)])
def test_a_ligne_has_exactly_one_positive_integer_side(
    raw: sqlite3.Connection, debit: object, credit: object
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO ligne (ecriture_id, idx, compte, debit, credit)"
            " VALUES (1, 2, '411000', ?, ?)",
            (debit, credit),
        )


def test_the_schema_holds_one_exercice_and_cancels_an_ecriture_once(
    raw: sqlite3.Connection,
) -> None:
    raw.execute("INSERT INTO exercice (date_start, date_end) VALUES ('2025-01-01', '2025-12-31')")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO exercice (date_start, date_end) VALUES ('2026-01-01', '2026-12-31')"
        )
    ecriture = (
        "INSERT INTO ecriture (journal_code, num, date, piece_ref, piece_date, lib, valid_date,"
        " request_id, request_hash, annule_id) VALUES ('VE', ?, '2025-01-20', 'F1', '2025-01-15',"
        " 'Annulation', '2025-01-20', ?, '" + "0" * 64 + "', 1)"
    )
    raw.execute(ecriture, (2, "r2"))
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(ecriture, (3, "r3"))
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(ecriture.replace(", 1)", ", 99)"), (4, "r4"))
