"""Recalcula el tipo, el comercio y la categoría de los movimientos ya importados con las reglas actuales.

  python -m selfbank.tools.recategorize --db ~/.selfbank/selfbank.db [--categories ~/.selfbank/categories.local.json]

Úsalo tras editar categories.local.json o actualizar el código. No cambia importes, fechas ni claves de deduplicación.
Imprime solo contadores.
"""

import argparse
import os
import sys

from selfbank import db, ingest
from selfbank.categorize import Categorizer


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.path.expanduser("~/.selfbank/selfbank.db"))
    parser.add_argument("--categories", default=os.path.expanduser("~/.selfbank/categories.local.json"))
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        stats = ingest.recategorize(conn, Categorizer.from_file(args.categories))
    finally:
        conn.close()
    print(f"Movimientos revisados: {stats['total']} · reclasificados: {stats['changed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
