# Glossary

French regulatory terms are identifiers in luca: they name tables, columns, JSON keys, tools and error codes, and are used as-is in code, documentation and messages. This page gives, for each term the repository uses, its meaning and the closest English notion — for understanding, never for renaming.

| Term | Meaning | Closest English |
|---|---|---|
| **société** | The company whose books a luca server keeps. One server, one société, one file. `societe` is the table holding its `siren` and `name`, and the key carrying them in every response. | company |
| **écriture** | An accounting entry: a balanced set of lignes on one journal, one `EcritureNum`. In luca, accepted in one transaction, then immutable. | journal entry |
| **ligne** | One line of an écriture: one compte, one debit or one credit. | posting |
| **journal** | A book in which écritures are numbered sequentially; identified by its code, e.g. `AC` purchases, `VE` sales, `BQ` bank, `OD` miscellaneous. | journal / daybook |
| **partie double** | Every écriture debits and credits equal amounts. | double entry |
| **EcritureNum** | Entry number, unbroken sequence within a journal, assigned when the écriture is accepted. `num` in the store. Distinct from luca's technical `id`. | entry number |
| **ValidDate** | Day on which an écriture became definitive. In luca, the day of acceptance in Europe/Paris. `valid_date` in the store. | posting date |
| **validation** | The act that makes an écriture definitive and assigns its number. In luca it is acceptance itself. | posting (in the definitive sense) |
| **brouillard** | Provisional entries not yet validated, in most packages. luca has none. | draft journal |
| **exercice** | Fiscal year, from `date_start` to `date_end`. One per file. | fiscal year |
| **plan comptable** | The chart of accounts: the comptes an écriture may use. In luca, exactly the comptes added through `/compte`. | chart of accounts |
| **compte** | An account of the plan comptable, identified by its `numero`. | account |
| **pièce** | The supporting document of an écriture: `piece.ref`, `piece.date`. | voucher / source document |
| **lib** | *Libellé*: the label of an écriture, a ligne, a compte or a journal. | label |
| **annule** | *Annule*: the link from an écriture to the one it cancels, of which it is the exact inverse. The only correction ([annule.md](annule.md)). | reversal |
| **centime** | Hundredth of a euro. The unit of every amount in the store. | cent |
| **SIREN** | Nine-digit company identifier. | company registration number |
| **FEC** | *Fichier des Écritures Comptables*: the legal export of an exercice's écritures. Not read or written by luca; its field names (`JournalCode`, `EcritureNum`, `PieceRef`, `ValidDate`, …) are why the store's columns are named as they are. | audit file |
| **lettrage** | Matching lignes on a third-party account that cancel out. luca has none. | reconciliation |
| **tiers** | A third party (customer, supplier). luca has none. | third party |
