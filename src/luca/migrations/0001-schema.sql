-- Schema version 1: the whole store. A shipped migration is never edited: add a new one.
-- docs/spec/store.md explains every table and column. application_id and journal_mode
-- are set by the server at creation, outside any transaction.

CREATE TABLE societe (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    siren TEXT    NOT NULL CHECK (siren GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    name  TEXT    NOT NULL CHECK (name <> '')
) STRICT;

CREATE TABLE exercice (
    id         INTEGER PRIMARY KEY CHECK (id = 1),  -- one exercice per file
    date_start TEXT NOT NULL CHECK (date_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    date_end   TEXT NOT NULL CHECK (date_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    CHECK (date_start <= date_end)
) STRICT;

CREATE TABLE journal (
    code TEXT PRIMARY KEY CHECK (code <> ''),
    lib  TEXT NOT NULL CHECK (lib <> '')
) STRICT;

CREATE TABLE compte (
    numero TEXT PRIMARY KEY CHECK (length(numero) >= 3),
    lib    TEXT NOT NULL CHECK (lib <> '')
) STRICT;

CREATE TABLE ecriture (
    id           INTEGER PRIMARY KEY,                    -- technical, stable, never reused
    journal_code TEXT    NOT NULL REFERENCES journal (code),
    num          INTEGER NOT NULL CHECK (num >= 1),      -- EcritureNum: continuous per journal
    date         TEXT    NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    piece_ref    TEXT    NOT NULL CHECK (piece_ref <> ''),
    piece_date   TEXT    NOT NULL CHECK (piece_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    lib          TEXT    NOT NULL CHECK (lib <> ''),
    valid_date   TEXT    NOT NULL CHECK (valid_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),  -- ValidDate: the day of acceptance, Europe/Paris
    request_id   TEXT    NOT NULL UNIQUE CHECK (request_id <> ''),   -- chosen by the caller
    request_hash TEXT    NOT NULL CHECK (length(request_hash) = 64), -- SHA-256 of the canonical content
    annule_id    INTEGER UNIQUE REFERENCES ecriture (id),            -- the écriture this one cancels
    UNIQUE (journal_code, num)
) STRICT;

CREATE TABLE ligne (
    ecriture_id INTEGER NOT NULL REFERENCES ecriture (id),
    idx         INTEGER NOT NULL CHECK (idx >= 0),
    compte      TEXT    NOT NULL REFERENCES compte (numero),
    lib         TEXT    CHECK (lib IS NULL OR lib <> ''),
    -- EUR amounts in centimes. Exactly one of the two is strictly positive.
    debit       INTEGER NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit      INTEGER NOT NULL DEFAULT 0 CHECK (credit >= 0),
    PRIMARY KEY (ecriture_id, idx),
    CHECK ((debit > 0) <> (credit > 0))
) STRICT;

-- Accepted écritures are immutable.
CREATE TRIGGER ecriture_no_update BEFORE UPDATE ON ecriture
BEGIN SELECT RAISE(ABORT, 'ecriture is immutable'); END;

CREATE TRIGGER ecriture_no_delete BEFORE DELETE ON ecriture
BEGIN SELECT RAISE(ABORT, 'ecriture is immutable'); END;

CREATE TRIGGER ligne_no_update BEFORE UPDATE ON ligne
BEGIN SELECT RAISE(ABORT, 'ligne is immutable'); END;

CREATE TRIGGER ligne_no_delete BEFORE DELETE ON ligne
BEGIN SELECT RAISE(ABORT, 'ligne is immutable'); END;
