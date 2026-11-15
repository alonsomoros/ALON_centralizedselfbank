"""Base de datos SQLite (biblioteca estándar). Importes en céntimos enteros, siempre positivos; el sentido va aparte.

Dos tipos de conexión:
  - `connect(path)`          lectura y escritura: solo la usan la importación y el historial.
  - `connect(path, True)`    SOLO LECTURA (mode=ro): la usa el ejecutor de consultas, de modo que ni un fallo
                             ni una inyección podrían modificar los datos.
El fichero se crea con permisos 600.
"""

import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE accounts (
    id           INTEGER PRIMARY KEY,
    account_key  TEXT NOT NULL UNIQUE,          -- identificador estable (identification_hash de Enable Banking)
    currency     TEXT NOT NULL,
    label        TEXT
);

CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts(id),
    booking_date     TEXT NOT NULL,             -- AAAA-MM-DD
    value_date       TEXT,
    amount_cents     INTEGER NOT NULL CHECK (amount_cents >= 0),
    direction        TEXT NOT NULL CHECK (direction IN ('out', 'in')),
    currency         TEXT NOT NULL,
    description      TEXT NOT NULL,             -- ya enmascarado: sin números de tarjeta
    description_norm TEXT NOT NULL,             -- minúsculas y sin tildes, para buscar
    kind             TEXT NOT NULL,             -- purchase | bizum | transfer | refund | income | fee | other
    merchant         TEXT,                      -- comercio o contraparte tal cual aparece
    merchant_norm    TEXT,
    category         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'BOOK',
    entry_reference  TEXT,                      -- informativo: no es una clave fiable
    dedup_hash       TEXT NOT NULL,
    dedup_n          INTEGER NOT NULL,          -- n.º de aparición de movimientos idénticos
    UNIQUE (account_id, dedup_hash, dedup_n)
);
CREATE INDEX idx_tx_date     ON transactions (booking_date);
CREATE INDEX idx_tx_category ON transactions (category);
CREATE INDEX idx_tx_merchant ON transactions (merchant_norm);
CREATE INDEX idx_tx_dir_date ON transactions (direction, booking_date);

CREATE TABLE balances (
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    balance_type    TEXT NOT NULL,              -- CLBD (cierre) | OPBD (apertura)
    amount_cents    INTEGER NOT NULL,           -- con signo
    reference_date  TEXT,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (account_id, balance_type)
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE history (
    answer_id   TEXT PRIMARY KEY,
    asked_at    TEXT NOT NULL,
    question    TEXT NOT NULL,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL,
    query_json  TEXT NOT NULL,                  -- la consulta interpretada, sin cifras
    verdict     TEXT,                           -- ok | wrong (feedback)
    correction  TEXT
);
"""


def _prepare_file(path: str):
    if path == ":memory:":
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        fd = os.open(target, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)


def connect(path: str, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = "file:" + quote(str(path)) + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        _prepare_file(str(path))
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        init(conn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    """Crea el esquema si la base está vacía; falla si es de una versión posterior."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        with conn:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        raise RuntimeError(f"Versión de esquema {version} no soportada (esperada {SCHEMA_VERSION})")


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
