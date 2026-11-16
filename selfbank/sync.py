"""Sincronización periódica: pide al banco los movimientos nuevos y los guarda en SQLite. Sin supervisión.

- Primera vez (la cuenta no tiene movimientos): todo el histórico disponible (`strategy=longest`).
- Después: desde el último movimiento guardado menos unos días de solape, porque un movimiento pendiente pasa a
  contabilizado más tarde. La importación es idempotente (`ingest.ingest_pages`), así que solapar no duplica nada.
- El consentimiento (PSD2) caduca: caducado no se hace ninguna petición; con 14 días o menos se avisa.
- Una cuenta que falla no bloquea a las demás. Los datos no se guardan en ficheros en bruto: del banco a SQLite.
Devuelve y registra solo contadores, nunca el contenido de los movimientos.
"""

import time
from datetime import date, timedelta

from . import db, ingest
from .bank import BankError
from .categorize import Categorizer

WARN_DAYS = 14
DEFAULT_OVERLAP_DAYS = 5
DEFAULT_MAX_PAGES = 100
PAGE_PAUSE_SECONDS = 0.5  # amabilidad con la API
ERROR_CHARS = 160


def _last_booking(conn, account_key: str):
    row = conn.execute(
        "SELECT MAX(t.booking_date) FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.account_key = ?", (account_key,)
    ).fetchone()
    return row[0] if row else None


def _fetch_pages(fetch, uid: str, params: dict, max_pages: int, sleep):
    """(páginas, truncado): sigue `continuation_key` hasta agotarla o llegar al tope."""
    pages, key = [], None
    for _ in range(max_pages):
        query = dict(params)
        if key:
            query["continuation_key"] = key
        page = fetch(f"/accounts/{uid}/transactions", query)
        pages.append(page)
        key = page.get("continuation_key")
        if not key:
            return pages, False
        sleep(PAGE_PAUSE_SECONDS)
    return pages, True


def run_sync(conn, session: dict, fetch, today: date, categorizer: Categorizer = None, overlap_days: int = DEFAULT_OVERLAP_DAYS,
             max_pages: int = DEFAULT_MAX_PAGES, sleep=time.sleep) -> dict:
    """Sincroniza todas las cuentas de la sesión. `fetch(path, params)` hace el GET autenticado y lanza `BankError`."""
    categorizer = categorizer or Categorizer()
    report = {"status": "ok", "inserted": 0, "already_present": 0, "rejected": 0, "balances": 0, "truncated": 0,
              "accounts_ok": 0, "accounts_failed": 0, "accounts_skipped": 0, "consent_days_left": None, "warnings": [], "errors": []}

    valid_until = str(session.get("valid_until") or "")[:10]
    if valid_until:
        days_left = (date.fromisoformat(valid_until) - today).days
        report["consent_days_left"] = days_left
        db.set_meta(conn, "consent_valid_until", valid_until)
        if days_left < 0:
            report["status"] = "consent_expired"
            return report
        if days_left <= WARN_DAYS:
            report["warnings"].append("consent_expiring")

    for index, account in enumerate(session.get("accounts", []), start=1):
        key = account.get("identification_hash")  # el `uid` cambia en cada autorización: usarlo duplicaría la cuenta
        uid = account.get("uid")
        if not key or not uid:
            report["accounts_skipped"] += 1
            if "account_without_stable_id" not in report["warnings"]:
                report["warnings"].append("account_without_stable_id")
            continue
        currency = account.get("currency") or "EUR"
        last = _last_booking(conn, key)
        params = {"date_from": (date.fromisoformat(last) - timedelta(days=overlap_days)).isoformat()} if last else {"strategy": "longest"}
        try:
            pages, truncated = _fetch_pages(fetch, uid, params, max_pages, sleep)
            stats = ingest.ingest_pages(conn, pages, key, categorizer, currency)
            report["balances"] += ingest.ingest_balances(conn, fetch(f"/accounts/{uid}/balances"), key, currency)
        except BankError as error:
            report["accounts_failed"] += 1
            report["errors"].append(f"cuenta {index}: {str(error)[:ERROR_CHARS]}")
            continue
        report["accounts_ok"] += 1
        report["inserted"] += stats.get("inserted", 0)
        report["already_present"] += stats.get("already_present", 0)
        report["rejected"] += stats.get("rejected", 0)
        if truncated:
            report["truncated"] += 1

    if report["accounts_ok"] == 0:
        report["status"] = "failed"
    elif report["accounts_failed"] or report["truncated"]:
        report["status"] = "partial"
    return report
