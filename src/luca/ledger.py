"""The write rules: /add, /compte, /journal, /exercice (docs/spec/endpoints.md).

Each handler takes the store and one parsed JSON document, checks every rule
it can, and either writes in one immediate transaction or raises ``Refused``
with every broken rule, each carrying a stable code. Nothing is written on a
refusal.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from luca.store import Store

Error = dict[str, Any]

PARIS = ZoneInfo("Europe/Paris")
MAX_CENTIMES = 2**63 - 1  # the largest INTEGER SQLite stores
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT = re.compile(r"^(\d+)(?:\.(\d{1,2}))?$")
_ANNULE = re.compile(r"^(.+)/([1-9]\d*)$")


class Refused(Exception):  # noqa: N818 — "raise Refused(errors)" is the sentence we want
    """The request was refused and nothing was written. One error per rule broken."""

    def __init__(self, errors: list[Error]) -> None:
        super().__init__(", ".join(e["code"] for e in errors))
        self.errors = errors


def error(code: str, message: str, **extra: Any) -> Error:
    return {"code": code, "message": message, **extra}


def today() -> str:
    """The day of acceptance: today in Europe/Paris, whatever the host's clock says."""
    return datetime.now(PARIS).date().isoformat()


def centimes(amount: int) -> str:
    """An amount on the wire: a decimal string with two decimals."""
    return f"{amount // 100}.{amount % 100:02d}"


# --- the shape of a document ---------------------------------------------------
# Every fault is one INVALID_SHAPE error naming the key at fault.


def _at(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _shape(errors: list[Error], message: str) -> None:
    errors.append(error("INVALID_SHAPE", message))


def check_object(
    value: Any, path: str, keys: set[str], required: set[str], errors: list[Error]
) -> bool:
    """``value`` is an object with no unknown key and every required key. False if unusable."""
    if not isinstance(value, dict):
        _shape(errors, f"{path or 'document'}: not a JSON object")
        return False
    for key in sorted(set(value) - keys):
        _shape(errors, f"{_at(path, key)}: unknown key")
    missing = sorted(required - set(value))
    for key in missing:
        _shape(errors, f"{_at(path, key)}: missing")
    return not missing


def text(obj: dict[str, Any], key: str, path: str, errors: list[Error]) -> str | None:
    value = obj[key]
    if not isinstance(value, str) or not value.strip():
        _shape(errors, f"{_at(path, key)}: not a non-empty string")
        return None
    return value


def parse_date(value: str) -> date:
    """A real calendar date written exactly ``YYYY-MM-DD``."""
    if not _ISO_DATE.match(value):
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{value!r} is not a calendar date") from None


def day(obj: dict[str, Any], key: str, path: str, errors: list[Error]) -> str | None:
    value = obj[key]
    if not isinstance(value, str):
        _shape(errors, f"{_at(path, key)}: not a string")
        return None
    try:
        parse_date(value)
    except ValueError as exc:
        _shape(errors, f"{_at(path, key)}: {exc}")
        return None
    return value


def amount(value: Any, path: str, errors: list[Error]) -> int | None:
    """A decimal string, strictly positive, at most two decimals; returns centimes."""
    if not isinstance(value, str):
        _shape(errors, f"{path}: not a decimal string")
        return None
    match = _AMOUNT.match(value)
    if match is None:
        _shape(errors, f"{path}: {value!r} is not a decimal amount with at most two decimals")
        return None
    in_centimes = int(match.group(1)) * 100 + int((match.group(2) or "").ljust(2, "0"))
    if in_centimes == 0:
        _shape(errors, f"{path}: {value!r} is not strictly positive")
        return None
    if in_centimes > MAX_CENTIMES:
        _shape(errors, f"{path}: {value!r} is larger than {centimes(MAX_CENTIMES)}")
        return None
    return in_centimes


# --- /add ------------------------------------------------------------------------


@dataclass(frozen=True)
class Ligne:
    compte: str
    lib: str | None
    debit: int  # centimes
    credit: int  # centimes


@dataclass(frozen=True)
class Document:
    """One écriture as /add receives it, checked for shape and balance only."""

    request_id: str
    journal: str
    date: str
    piece_ref: str
    piece_date: str
    lib: str
    lignes: tuple[Ligne, ...]
    annule: tuple[str, int] | None  # (journal, num) of the écriture cancelled


_DOCUMENT_KEYS = {"request_id", "journal", "date", "piece", "lib", "lignes", "annule"}
_PIECE_KEYS = {"ref", "date"}
_LIGNE_KEYS = {"compte", "lib", "debit", "credit"}


def _ligne(value: Any, path: str, errors: list[Error]) -> Ligne | None:
    if not check_object(value, path, _LIGNE_KEYS, {"compte"}, errors):
        return None
    compte = text(value, "compte", path, errors)
    lib = text(value, "lib", path, errors) if "lib" in value else None
    sides = [side for side in ("debit", "credit") if side in value]
    if len(sides) != 1:
        _shape(errors, f"{path}: exactly one of debit or credit")
        return None
    cents = amount(value[sides[0]], _at(path, sides[0]), errors)
    if compte is None or cents is None or ("lib" in value and lib is None):
        return None
    debit, credit = (cents, 0) if sides[0] == "debit" else (0, cents)
    return Ligne(compte, lib, debit, credit)


def parse_document(raw: Any) -> Document:
    """The écriture of a document; refuses a malformed shape, then an unbalanced écriture."""
    errors: list[Error] = []
    if not check_object(raw, "", _DOCUMENT_KEYS, _DOCUMENT_KEYS - {"annule"}, errors):
        raise Refused(errors)
    request_id = text(raw, "request_id", "", errors)
    journal = text(raw, "journal", "", errors)
    date_ = day(raw, "date", "", errors)
    lib = text(raw, "lib", "", errors)
    piece_ref = piece_date = None
    if check_object(raw["piece"], "piece", _PIECE_KEYS, _PIECE_KEYS, errors):
        piece_ref = text(raw["piece"], "ref", "piece", errors)
        piece_date = day(raw["piece"], "date", "piece", errors)
    lignes: list[Ligne] = []
    if not isinstance(raw["lignes"], list):
        _shape(errors, "lignes: not a JSON array")
    else:
        if len(raw["lignes"]) < 2:
            _shape(errors, "lignes: at least two lignes")
        for i, value in enumerate(raw["lignes"]):
            if (ligne := _ligne(value, f"lignes[{i}]", errors)) is not None:
                lignes.append(ligne)
    annule = None
    if "annule" in raw:
        reference = text(raw, "annule", "", errors)
        match = _ANNULE.match(reference) if reference is not None else None
        if reference is not None and match is None:
            _shape(errors, f"annule: {reference!r} is not <journal>/<num>")
        elif match is not None:
            annule = (match.group(1), int(match.group(2)))
    if errors:
        raise Refused(errors)
    debits = sum(ligne.debit for ligne in lignes)
    credits = sum(ligne.credit for ligne in lignes)
    if debits != credits:
        raise Refused(
            [
                error(
                    "UNBALANCED",
                    f"debits {centimes(debits)} and credits {centimes(credits)} differ",
                )
            ]
        )
    assert request_id and journal and date_ and lib and piece_ref and piece_date
    return Document(request_id, journal, date_, piece_ref, piece_date, lib, tuple(lignes), annule)


def canonical(document: Document) -> bytes:
    """The canonical content of an écriture (docs/spec/canonical.md)."""
    lignes: list[dict[str, Any]] = []
    for ligne in document.lignes:
        entry: dict[str, Any] = {"compte": ligne.compte}
        if ligne.lib is not None:
            entry["lib"] = ligne.lib
        if ligne.debit:
            entry["debit"] = ligne.debit
        else:
            entry["credit"] = ligne.credit
        lignes.append(entry)
    content: dict[str, Any] = {
        "request_id": document.request_id,
        "journal": document.journal,
        "date": document.date,
        "piece": {"ref": document.piece_ref, "date": document.piece_date},
        "lib": document.lib,
        "lignes": lignes,
    }
    if document.annule is not None:
        content["annule"] = f"{document.annule[0]}/{document.annule[1]}"
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def ecriture_json(conn: sqlite3.Connection, ecriture_id: int) -> dict[str, Any]:
    """An écriture as every response returns it."""
    row = conn.execute(
        """
        SELECT e.id, e.journal_code, e.num, e.date, e.piece_ref, e.piece_date, e.lib,
               e.valid_date, e.request_id, a.journal_code, a.num
        FROM ecriture e LEFT JOIN ecriture a ON a.id = e.annule_id
        WHERE e.id = ?
        """,
        (ecriture_id,),
    ).fetchone()
    lignes = []
    for compte, lib, debit, credit in conn.execute(
        "SELECT compte, lib, debit, credit FROM ligne WHERE ecriture_id = ? ORDER BY idx",
        (ecriture_id,),
    ):
        ligne: dict[str, Any] = {"compte": compte, "lib": lib}
        if debit:
            ligne["debit"] = centimes(debit)
        else:
            ligne["credit"] = centimes(credit)
        lignes.append(ligne)
    return {
        "id": row[0],
        "journal": row[1],
        "num": row[2],
        "date": row[3],
        "piece": {"ref": row[4], "date": row[5]},
        "lib": row[6],
        "valid_date": row[7],
        "request_id": row[8],
        "annule": f"{row[9]}/{row[10]}" if row[9] is not None else None,
        "lignes": lignes,
    }


def _inverse(new: tuple[Ligne, ...], original: list[tuple[str, int, int]]) -> bool:
    return len(new) == len(original) and all(
        ligne.compte == compte and ligne.debit == credit and ligne.credit == debit
        for ligne, (compte, debit, credit) in zip(new, original, strict=True)
    )


def add(store: Store, raw: Any) -> dict[str, Any]:
    """Accept or refuse one écriture in one transaction; replay an identical request."""
    document = parse_document(raw)
    digest = hashlib.sha256(canonical(document)).hexdigest()
    errors: list[Error] = []
    with store.write() as conn:
        previous = conn.execute(
            "SELECT id, request_hash FROM ecriture WHERE request_id = ?", (document.request_id,)
        ).fetchone()
        if previous is not None and previous[1] == digest:
            return {"replay": True, "ecriture": ecriture_json(conn, previous[0])}
        if previous is not None:
            errors.append(
                error(
                    "REQUEST_ID_CONFLICT",
                    f"request_id {document.request_id!r} already used with different content",
                    ecriture=ecriture_json(conn, previous[0]),
                )
            )
        exercice = conn.execute("SELECT date_start, date_end FROM exercice").fetchone()
        if exercice is None:
            errors.append(
                error(
                    "NO_EXERCICE",
                    "no exercice yet: open one with POST /exercice (luca_open_exercice),"
                    " then add journaux with POST /journal and comptes with POST /compte",
                )
            )
        elif not exercice[0] <= document.date <= exercice[1]:
            errors.append(
                error(
                    "DATE_OUTSIDE_EXERCICE",
                    f"date {document.date} is outside the exercice {exercice[0]} → {exercice[1]}",
                )
            )
        if (
            conn.execute("SELECT 1 FROM journal WHERE code = ?", (document.journal,)).fetchone()
            is None
        ):
            errors.append(error("UNKNOWN_JOURNAL", f"journal {document.journal} does not exist"))
        for i, ligne in enumerate(document.lignes):
            if (
                conn.execute("SELECT 1 FROM compte WHERE numero = ?", (ligne.compte,)).fetchone()
                is None
            ):
                errors.append(
                    error("UNKNOWN_COMPTE", f"lignes[{i}].compte: {ligne.compte} does not exist")
                )
        annule_id = None
        if document.annule is not None:
            reference = f"{document.annule[0]}/{document.annule[1]}"
            target = conn.execute(
                "SELECT id FROM ecriture WHERE journal_code = ? AND num = ?", document.annule
            ).fetchone()
            if target is None:
                errors.append(error("ANNULE_NOT_FOUND", f"annule {reference}: no such écriture"))
            else:
                annule_id = int(target[0])
                cancelled_by = conn.execute(
                    "SELECT journal_code, num FROM ecriture WHERE annule_id = ?", (annule_id,)
                ).fetchone()
                if cancelled_by is not None:
                    errors.append(
                        error(
                            "ANNULE_ALREADY_USED",
                            f"annule {reference}: already cancelled by"
                            f" {cancelled_by[0]}/{cancelled_by[1]}",
                        )
                    )
                original = conn.execute(
                    "SELECT compte, debit, credit FROM ligne WHERE ecriture_id = ? ORDER BY idx",
                    (annule_id,),
                ).fetchall()
                if not _inverse(document.lignes, original):
                    errors.append(
                        error(
                            "ANNULE_NOT_INVERSE",
                            f"annule {reference}: the lignes are not its exact inverse",
                        )
                    )
        if errors:
            raise Refused(errors)

        num = conn.execute(
            "SELECT coalesce(max(num), 0) + 1 FROM ecriture WHERE journal_code = ?",
            (document.journal,),
        ).fetchone()[0]
        ecriture_id = conn.execute(
            """
            INSERT INTO ecriture (journal_code, num, date, piece_ref, piece_date, lib,
                                  valid_date, request_id, request_hash, annule_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                document.journal,
                num,
                document.date,
                document.piece_ref,
                document.piece_date,
                document.lib,
                today(),
                document.request_id,
                digest,
                annule_id,
            ),
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO ligne (ecriture_id, idx, compte, lib, debit, credit)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (ecriture_id, idx, ligne.compte, ligne.lib, ligne.debit, ligne.credit)
                for idx, ligne in enumerate(document.lignes)
            ],
        )
        return {"replay": False, "ecriture": ecriture_json(conn, ecriture_id)}


# --- /compte, /journal, /exercice ------------------------------------------------


def add_compte(store: Store, raw: Any) -> dict[str, Any]:
    """Add a compte to the plan comptable. Refuses a short numero and an existing one."""
    errors: list[Error] = []
    if not check_object(raw, "", {"numero", "lib"}, {"numero", "lib"}, errors):
        raise Refused(errors)
    numero = text(raw, "numero", "", errors)
    lib = text(raw, "lib", "", errors)
    if errors or numero is None or lib is None:
        raise Refused(errors)
    if len(numero.strip()) < 3:
        errors.append(
            error("INVALID_COMPTE", f"numero {numero!r} is shorter than three characters")
        )
    with store.write() as conn:
        existing = conn.execute("SELECT lib FROM compte WHERE numero = ?", (numero,)).fetchone()
        if existing is not None:
            errors.append(error("COMPTE_EXISTS", f"compte {numero} already exists: {existing[0]}"))
        if errors:
            raise Refused(errors)
        conn.execute("INSERT INTO compte (numero, lib) VALUES (?, ?)", (numero, lib))
    return {"compte": {"numero": numero, "lib": lib}}


def add_journal(store: Store, raw: Any) -> dict[str, Any]:
    """Add a journal. Refuses an existing code."""
    errors: list[Error] = []
    if not check_object(raw, "", {"code", "lib"}, {"code", "lib"}, errors):
        raise Refused(errors)
    code = text(raw, "code", "", errors)
    lib = text(raw, "lib", "", errors)
    if errors or code is None or lib is None:
        raise Refused(errors)
    with store.write() as conn:
        existing = conn.execute("SELECT lib FROM journal WHERE code = ?", (code,)).fetchone()
        if existing is not None:
            raise Refused(
                [error("JOURNAL_EXISTS", f"journal {code} already exists: {existing[0]}")]
            )
        conn.execute("INSERT INTO journal (code, lib) VALUES (?, ?)", (code, lib))
    return {"journal": {"code": code, "lib": lib}}


def open_exercice(store: Store, raw: Any) -> dict[str, Any]:
    """Open the single exercice ``[date_start, date_end]``. Refuses a second one."""
    errors: list[Error] = []
    keys = {"date_start", "date_end"}
    if not check_object(raw, "", keys, keys, errors):
        raise Refused(errors)
    start = day(raw, "date_start", "", errors)
    end = day(raw, "date_end", "", errors)
    if errors or start is None or end is None:
        raise Refused(errors)
    if end < start:
        errors.append(error("INVALID_EXERCICE", f"date_end {end} is before date_start {start}"))
    with store.write() as conn:
        existing = conn.execute("SELECT date_start, date_end FROM exercice").fetchone()
        if existing is not None:
            errors.append(
                error(
                    "EXERCICE_EXISTS",
                    f"an exercice already exists: {existing[0]} → {existing[1]}",
                )
            )
        if errors:
            raise Refused(errors)
        conn.execute(
            "INSERT INTO exercice (id, date_start, date_end) VALUES (1, ?, ?)", (start, end)
        )
    return {"exercice": {"date_start": start, "date_end": end}}
