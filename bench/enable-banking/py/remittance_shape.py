"""M5c: radiografía del FORMATO de `remittance_information`, sin revelar su contenido.

Uso (en la Pi):  python3 remittance_shape.py --profile prod

Cada palabra se sustituye por `A`, salvo una lista cerrada de términos genéricos
de banco, que se muestran tal cual. Cada número pasa a `9{n}` (solo su longitud).
Solo se imprimen las formas que aparecen al menos K veces (k-anonimato): lo raro
o único se agrupa en "otras". Nunca se imprime un nombre de comercio.
"""

import argparse
import json
import re
import statistics
import sys
import unicodedata
from collections import Counter

import eb_common as eb

# Términos de banco, sin nombres propios ni datos personales.
GENERIC = frozenset(
    """
    COMPRA COMPRAS TARJETA TARJ TRANSFERENCIA TRANSF BIZUM RECIBO RECIBOS ADEUDO DOMICILIACION
    NOMINA COMISION COMISIONES INTERESES TRASPASO REINTEGRO CAJERO EFECTIVO INGRESO DEVOLUCION
    PAGO PAGOS CONCEPTO REF REFERENCIA FECHA TPV SEPA ATM CON DE DEL EN EL LA POR A Y
    """.split()
)

TOKEN = re.compile(r"[^\W\d_]+|\d+|\s+|[^\w\s]|_")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def shape(text: str) -> str:
    """Forma de un texto: letras -> A, números -> 9{n}, términos genéricos y puntuación intactos."""
    pieces = []
    for token in TOKEN.findall(text):
        if token.isspace():
            piece = " "
        elif token.isdigit():
            piece = f"9{{{len(token)}}}"
        elif token[0].isalpha():
            word = strip_accents(token).upper()
            piece = word if word in GENERIC else "A"
        else:
            piece = token
        if pieces and pieces[-1] == " " and piece == " ":
            continue
        pieces.append(piece)
    result = "".join(pieces).strip()
    return re.sub(r"\bA( A)+\b", "A+", result)


def items_of(transaction: dict) -> list:
    return [str(item) for item in (transaction.get("remittance_information") or []) if str(item).strip()]


def transaction_shape(transaction: dict) -> str:
    items = items_of(transaction)
    return " | ".join(shape(item) for item in items) if items else "(sin concepto)"


def generic_words(transaction: dict) -> list:
    found = []
    for item in items_of(transaction):
        for token in TOKEN.findall(item):
            if token[0].isalpha():
                word = strip_accents(token).upper()
                if word in GENERIC:
                    found.append(word)
    return found


def build_report(transactions: list, k: int = 5, top: int = 25) -> str:
    lines = [f"Movimientos analizados: {len(transactions)}  |  umbral de anonimato k = {k}"]

    counts = Counter(len(items_of(t)) for t in transactions)
    lines.append(f"Elementos de concepto por movimiento: {dict(sorted(counts.items()))}")
    lengths = sorted(len(" ".join(items_of(t))) for t in transactions)
    if lengths:
        p90 = lengths[min(len(lengths) - 1, int(len(lengths) * 0.9))]
        lines.append(f"Longitud del concepto (caracteres): mín {lengths[0]}, mediana {int(statistics.median(lengths))}, p90 {p90}, máx {lengths[-1]}")

    for direction in ("DBIT", "CRDT"):
        subset = [t for t in transactions if t.get("credit_debit_indicator") == direction]
        shapes = Counter(transaction_shape(t) for t in subset)
        lines.append(f"\n== {direction}: {len(subset)} movimientos, {len(shapes)} formas distintas ==")
        shown = 0
        hidden = 0
        for text, count in shapes.most_common():
            if count >= k and shown < top:
                lines.append(f"  {count:5d}  {text}")
                shown += 1
            else:
                hidden += count
        if hidden:
            lines.append(f"  {hidden:5d}  (otras formas, ocultas por privacidad o fuera del top)")

    words = Counter(w for t in transactions for w in generic_words(t))
    visible = {w: c for w, c in words.most_common() if c >= k}
    lines.append(f"\nTérminos genéricos frecuentes: {visible}")
    return "\n".join(lines)


def load_transactions(profile: str, file=None) -> list:
    if file:
        paths = [file]
    else:
        files = sorted(eb.raw_dir(profile).glob("*-transactions.json"))
        if not files:
            raise eb.EbError(f"No hay ficheros *-transactions.json en {eb.raw_dir(profile)}")
        stamp = files[-1].name.split("-")[0]
        paths = [f for f in files if f.name.startswith(stamp)]
    transactions = []
    for path in paths:
        for page in eb.load_json(path):
            transactions.extend(page.get("transactions", []))
    return transactions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=eb.PROFILES, required=True)
    parser.add_argument("--file", default=None, help="Fichero concreto en vez del último")
    parser.add_argument("--k", type=int, default=5, help="Mínimo de repeticiones para mostrar una forma")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)
    try:
        print(build_report(load_transactions(args.profile, args.file), k=args.k, top=args.top))
    except eb.EbError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
