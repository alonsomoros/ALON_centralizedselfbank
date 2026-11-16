"""Sincroniza con el banco (Enable Banking) y guarda los movimientos nuevos. Pensado para el temporizador de systemd.

  python3 -m selfbank.tools.sync_cli --db ~/.selfbank/selfbank.db --session ~/.selfbank/session-prod.json

Imprime solo contadores, nunca movimientos. Códigos de salida: 0 bien · 1 fallo total o parcial · 2 falta la sesión o
la clave · 3 consentimiento caducado (hay que volver a autorizar con bench/enable-banking/py/auth_start.py).
"""

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

from selfbank import bank, db, sync
from selfbank.categorize import Categorizer

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_CONSENT = 0, 1, 2, 3


def main(argv=None, fetch=None, today: date = None, sleep=time.sleep) -> int:
    home = bank.home_dir()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(home / "selfbank.db"))
    parser.add_argument("--session", default=str(home / "session-prod.json"))
    parser.add_argument("--key", default=str(home / bank.PROD_KEY_NAME), help="clave privada de la aplicación (permisos 600)")
    parser.add_argument("--app-id", default=bank.PROD_APP_ID)
    parser.add_argument("--categories", default=str(home / "categories.local.json"), help="reglas de categoría propias (opcional)")
    parser.add_argument("--overlap-days", type=int, default=sync.DEFAULT_OVERLAP_DAYS)
    parser.add_argument("--max-pages", type=int, default=sync.DEFAULT_MAX_PAGES)
    args = parser.parse_args(argv)

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"ERROR: no existe la sesión {session_path}. Autoriza primero con bench/enable-banking/py/auth_start.py.", file=sys.stderr)
        return EXIT_CONFIG
    if fetch is None:
        if not Path(args.key).exists():
            print(f"ERROR: no existe la clave {args.key}.", file=sys.stderr)
            return EXIT_CONFIG
        fetch = bank.make_fetch(args.app_id, args.key)

    import json
    session = json.loads(session_path.read_text(encoding="utf-8"))
    conn = db.connect(args.db)
    try:
        report = sync.run_sync(conn, session, fetch, today or date.today(), Categorizer.from_file(args.categories),
                               args.overlap_days, args.max_pages, sleep)
    finally:
        conn.close()

    counters = ("inserted", "already_present", "rejected", "balances", "truncated", "accounts_ok", "accounts_failed", "accounts_skipped")
    print(f"status={report['status']} " + " ".join(f"{name}={report[name]}" for name in counters))
    if report["consent_days_left"] is not None:
        print(f"consentimiento: quedan {report['consent_days_left']} días")
    for warning in report["warnings"]:
        print(f"AVISO: {warning} (quedan 14 días o menos: vuelve a autorizar)", file=sys.stderr)
    for error in report["errors"]:
        print(f"ERROR: {error}", file=sys.stderr)

    if report["status"] == "consent_expired":
        print("ERROR: consentimiento caducado: no se ha hecho ninguna petición al banco. Vuelve a autorizar.", file=sys.stderr)
        return EXIT_CONSENT
    return EXIT_OK if report["status"] == "ok" else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
