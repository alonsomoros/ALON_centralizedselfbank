"""Paso 3: descarga saldos y movimientos y muestra solo el ESQUEMA.

Uso:  python fetch_probe.py --profile sandbox
      python fetch_probe.py --profile prod --strategy longest

Los datos completos se guardan en bruto en ~/.selfbank/raw/<perfil>/ con
permisos 600, fuera del repositorio. Lo que se imprime son nombres de campos,
tipos y recuentos: nunca importes, descripciones ni IBAN.
"""

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import eb_common as eb


def fetch_all_transactions(jwt: str, uid: str, params: dict, max_pages: int):
    pages, key = [], None
    for _ in range(max_pages):
        query = dict(params)
        if key:
            query["continuation_key"] = key
        page = eb.request("GET", f"/accounts/{uid}/transactions", jwt, params=query)
        pages.append(page)
        key = page.get("continuation_key")
        if not key:
            return pages, False
        time.sleep(0.5)  # amabilidad con la API
    return pages, True  # se alcanzó el tope de páginas


def summarize(transactions: list) -> None:
    dates = [t.get("booking_date") or t.get("value_date") or t.get("transaction_date") for t in transactions]
    dates = sorted(d for d in dates if d)
    print(f"    movimientos : {len(transactions)}")
    if dates:
        print(f"    fechas      : {dates[0]} -> {dates[-1]}")
    print(f"    estados     : {dict(Counter(t.get('status') for t in transactions))}")
    print(f"    CRDT/DBIT   : {dict(Counter(t.get('credit_debit_indicator') for t in transactions))}")
    schema = {}
    for transaction in transactions:
        eb.collect_schema(transaction, out=schema)
    print("    campos de cada movimiento (nombre: tipos):")
    eb.print_schema(schema, indent="      ")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=eb.PROFILES, required=True)
    parser.add_argument("--strategy", default="longest", help="Estrategia de histórico (por defecto: longest)")
    parser.add_argument("--no-strategy", action="store_true", help="No enviar el parámetro strategy")
    parser.add_argument("--date-from", default=None, help="AAAA-MM-DD (opcional)")
    parser.add_argument("--max-pages", type=int, default=100)
    args = parser.parse_args(argv)

    prof = eb.profile(args.profile)
    try:
        session = eb.load_json(eb.session_path(args.profile))
        jwt = eb.sign_jwt(prof["app_id"], prof["key"])
        params = {}
        if not args.no_strategy:
            params["strategy"] = args.strategy
        if args.date_from:
            params["date_from"] = args.date_from

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for index, account in enumerate(session["accounts"], start=1):
            uid = account["uid"]
            print(f"\nCuenta {index} (uid {eb.mask_tail(uid)}, {account.get('currency')})")

            balances = eb.request("GET", f"/accounts/{uid}/balances", jwt)
            eb.secure_write_json(eb.raw_dir(args.profile) / f"{stamp}-{index}-balances.json", balances)
            items = balances.get("balances", [])
            print(f"    saldos      : {len(items)} (tipos: {sorted({b.get('balance_type') for b in items})})")

            pages, truncated = fetch_all_transactions(jwt, uid, params, args.max_pages)
            eb.secure_write_json(eb.raw_dir(args.profile) / f"{stamp}-{index}-transactions.json", pages)
            transactions = [t for page in pages for t in page.get("transactions", [])]
            print(f"    páginas     : {len(pages)}{'  (TOPE ALCANZADO)' if truncated else ''}")
            summarize(transactions)
    except eb.EbError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"\nDatos en bruto guardados en {eb.raw_dir(args.profile)} (permisos 600, fuera del repo).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
