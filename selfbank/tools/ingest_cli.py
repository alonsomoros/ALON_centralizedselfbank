"""Importa a SQLite los datos en bruto de Enable Banking (los guardan los scripts de bench/enable-banking).

  python -m selfbank.tools.ingest_cli --raw-dir ~/.selfbank/raw/prod --session ~/.selfbank/session-prod.json --db ~/.selfbank/selfbank.db

Es idempotente: se puede ejecutar cuantas veces haga falta. Imprime solo contadores, nunca movimientos.
Los ficheros en bruto se llaman <fecha>-<n.º de cuenta>-transactions.json / -balances.json; se usa el más reciente de cada cuenta.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from selfbank import db, ingest
from selfbank.categorize import Categorizer

NAME = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)-(?P<index>\d+)-(?P<kind>transactions|balances)\.json$")


def latest_files(raw_dir: Path) -> dict:
    """{n.º de cuenta: {'transactions': Path, 'balances': Path}} con el fichero más reciente de cada tipo."""
    found = {}
    for path in sorted(raw_dir.iterdir()):
        match = NAME.match(path.name)
        if match:
            found.setdefault(int(match["index"]), {})[match["kind"]] = path  # el orden alfabético = cronológico
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--session", required=True, help="session-<perfil>.json (identificador estable de cada cuenta y caducidad)")
    parser.add_argument("--db", default=os.path.expanduser("~/.selfbank/selfbank.db"))
    parser.add_argument("--categories", default=os.path.expanduser("~/.selfbank/categories.local.json"), help="reglas de categoría propias (opcional)")
    args = parser.parse_args(argv)

    session = json.loads(Path(args.session).read_text(encoding="utf-8"))
    accounts = session.get("accounts", [])
    categorizer = Categorizer.from_file(args.categories)
    conn = db.connect(args.db)
    try:
        for index, files in sorted(latest_files(Path(args.raw_dir)).items()):
            if index > len(accounts):
                print(f"Cuenta {index}: no está en la sesión, se omite", file=sys.stderr)
                continue
            account = accounts[index - 1]
            key = account.get("identification_hash") or account.get("uid")
            if not key:
                print(f"Cuenta {index}: sin identificador estable, se omite", file=sys.stderr)
                continue
            currency = account.get("currency") or "EUR"
            if "transactions" in files:
                pages = json.loads(files["transactions"].read_text(encoding="utf-8"))
                stats = ingest.ingest_pages(conn, pages, key, categorizer, currency)
                print(f"Cuenta {index}: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))
            if "balances" in files:
                n = ingest.ingest_balances(conn, json.loads(files["balances"].read_text(encoding="utf-8")), key, currency)
                print(f"Cuenta {index}: saldos guardados={n}")
        valid_until = session.get("valid_until")
        if valid_until:
            db.set_meta(conn, "consent_valid_until", str(valid_until)[:10])
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        print(f"Total de movimientos en la base: {total}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
