"""Importación de los datos en bruto de Enable Banking a SQLite. Idempotente.

- Enmascara números de tarjeta y secuencias largas de dígitos ANTES de guardar o calcular nada.
- Clave de deduplicación compuesta (no hay identificador fiable): (cuenta, fechas, importe, sentido, texto
  enmascarado) más un contador de apariciones idénticas. Dos compras idénticas el mismo día se conservan; volver
  a importar lo mismo no añade nada.
- Solo se guardan movimientos `BOOK`; el resto se cuenta aparte.
Nunca imprime ni registra el contenido de los movimientos.
"""

import hashlib
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import db
from .categorize import Categorizer, parse
from .text import mask_sensitive, squash


class IngestError(ValueError):
    pass


def parse_cents(amount: str) -> int:
    try:
        value = Decimal(str(amount).strip())
    except InvalidOperation:
        raise IngestError("importe no numérico")
    cents = (value * 100).to_integral_value()
    if cents != value * 100:
        raise IngestError("importe con más de dos decimales")
    return int(cents)


def dedup_hash(account_key: str, booking_date: str, value_date, amount_cents: int, direction: str, description: str) -> str:
    raw = "\x1f".join([account_key, booking_date, value_date or "", str(amount_cents), direction, description])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_transaction(raw: dict, account_key: str, categorizer: Categorizer) -> dict:
    """Un movimiento de la API -> fila lista para insertar (sin id ni contador)."""
    indicator = raw.get("credit_debit_indicator")
    if indicator not in ("DBIT", "CRDT"):
        raise IngestError("sentido desconocido")
    amount = raw.get("transaction_amount") or {}
    cents = parse_cents(amount.get("amount", ""))
    if cents < 0:
        raise IngestError("importe negativo (se esperaba positivo con indicador)")
    booking = raw.get("booking_date") or raw.get("value_date") or raw.get("transaction_date")
    if not booking:
        raise IngestError("sin fecha")
    parts = raw.get("remittance_information") or []
    description = mask_sensitive(" ".join(" ".join(str(p) for p in parts).split())) or "(sin concepto)"
    direction = "out" if indicator == "DBIT" else "in"
    parsed = parse(description, categorizer)
    return {
        "booking_date": booking, "value_date": raw.get("value_date"), "amount_cents": cents, "direction": direction,
        "currency": amount.get("currency") or "EUR", "description": description, "description_norm": squash(description),
        "kind": parsed.kind, "merchant": parsed.merchant, "merchant_norm": squash(parsed.merchant) if parsed.merchant else None,
        "category": parsed.category, "status": raw.get("status") or "BOOK", "entry_reference": raw.get("entry_reference") or None,
    }


def get_or_create_account(conn, account_key: str, currency: str = "EUR", label: str = None) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE account_key = ?", (account_key,)).fetchone()
    if row:
        return row[0]
    with conn:
        cur = conn.execute("INSERT INTO accounts (account_key, currency, label) VALUES (?, ?, ?)", (account_key, currency, label))
    return cur.lastrowid


def ingest_pages(conn, pages: list, account_key: str, categorizer: Categorizer = None, currency: str = "EUR") -> dict:
    """Importa las páginas de /transactions. Devuelve solo contadores."""
    categorizer = categorizer or Categorizer()
    account_id = get_or_create_account(conn, account_key, currency)
    stats = Counter()
    batch = []
    for page in pages:
        for raw in page.get("transactions", []):
            stats["read"] += 1
            if (raw.get("status") or "BOOK") != "BOOK":
                stats["skipped_not_booked"] += 1
                continue
            try:
                row = normalize_transaction(raw, account_key, categorizer)
            except IngestError:
                stats["rejected"] += 1
                continue
            row["dedup_hash"] = dedup_hash(account_key, row["booking_date"], row["value_date"], row["amount_cents"], row["direction"], row["description"])
            batch.append(row)

    groups = Counter(row["dedup_hash"] for row in batch)
    existing = {h: n for h, n in conn.execute(
        "SELECT dedup_hash, COUNT(*) FROM transactions WHERE account_id = ? GROUP BY dedup_hash", (account_id,))}
    seen = Counter()
    to_insert = []
    for row in batch:
        h = row["dedup_hash"]
        seen[h] += 1
        # aparición n.º seen[h] en este lote; solo es nueva si supera lo que ya hay guardado
        if seen[h] > existing.get(h, 0):
            to_insert.append((account_id, row["booking_date"], row["value_date"], row["amount_cents"], row["direction"], row["currency"],
                              row["description"], row["description_norm"], row["kind"], row["merchant"], row["merchant_norm"],
                              row["category"], row["status"], row["entry_reference"], h, seen[h]))
        else:
            stats["already_present"] += 1
    with conn:
        conn.executemany(
            "INSERT INTO transactions (account_id, booking_date, value_date, amount_cents, direction, currency, description, "
            "description_norm, kind, merchant, merchant_norm, category, status, entry_reference, dedup_hash, dedup_n) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", to_insert)
    stats["inserted"] = len(to_insert)
    db.set_meta(conn, "last_sync", datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    return dict(stats)


def ingest_balances(conn, balances: dict, account_key: str, currency: str = "EUR") -> int:
    account_id = get_or_create_account(conn, account_key, currency)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    count = 0
    with conn:
        for item in balances.get("balances", []):
            kind = item.get("balance_type")
            amount = (item.get("balance_amount") or {}).get("amount")
            if not kind or amount is None:
                continue
            cents = parse_cents(amount)
            if (item.get("credit_debit_indicator") == "DBIT"):
                cents = -abs(cents)
            conn.execute(
                "INSERT INTO balances (account_id, balance_type, amount_cents, reference_date, fetched_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, balance_type) DO UPDATE SET amount_cents = excluded.amount_cents, "
                "reference_date = excluded.reference_date, fetched_at = excluded.fetched_at",
                (account_id, kind, cents, item.get("reference_date"), now))
            count += 1
    return count


def recategorize(conn, categorizer: Categorizer = None) -> dict:
    """Recalcula tipo, comercio y categoría de lo ya guardado con las reglas actuales (sin tocar importes ni claves).

    Se usa cuando cambian las reglas o el fichero de categorías propias. Idempotente. Devuelve contadores.
    """
    categorizer = categorizer or Categorizer()
    changes = []
    for row_id, description, kind, merchant, category in conn.execute("SELECT id, description, kind, merchant, category FROM transactions").fetchall():
        parsed = parse(description, categorizer)
        if (parsed.kind, parsed.merchant, parsed.category) != (kind, merchant, category):
            changes.append((parsed.kind, parsed.merchant, squash(parsed.merchant) if parsed.merchant else None, parsed.category, row_id))
    with conn:
        conn.executemany("UPDATE transactions SET kind = ?, merchant = ?, merchant_norm = ?, category = ? WHERE id = ?", changes)
    return {"changed": len(changes), "total": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]}
