"""Datos SINTÉTICOS con las mismas formas que Enable Banking/Santander. Nunca datos reales.

`synthetic_pages()` genera ~14 meses de movimientos deterministas (nómina, alquiler, suscripciones, supermercados,
restaurantes, transporte, Bizums, efectivo, devoluciones, duplicados exactos y tarjetas completas en el texto).
"""

import random
from datetime import date, timedelta

TODAY = date(2026, 11, 15)
CARD16 = "4111111111111234"  # número de prueba estándar


def tx(booking: str, amount: str, indicator: str, text: str, ref: str = None, status: str = "BOOK") -> dict:
    return {
        "entry_reference": ref, "booking_date": booking, "value_date": booking, "transaction_date": None,
        "transaction_amount": {"currency": "EUR", "amount": amount}, "credit_debit_indicator": indicator,
        "status": status, "remittance_information": [text],
        "creditor": None, "debtor": None, "transaction_id": None, "merchant_category_code": None, "note": None,
    }


def pages(transactions: list, per_page: int = 100) -> list:
    out = []
    for i in range(0, len(transactions), per_page):
        chunk = transactions[i:i + per_page]
        out.append({"transactions": chunk, "continuation_key": "k" if i + per_page < len(transactions) else None})
    return out or [{"transactions": [], "continuation_key": None}]


def card_purchase(merchant: str, place: str = "MADRID", digits: str = "123456") -> str:
    return f"PAGO MOVIL EN {merchant}, {place}, TARJ. :*{digits}"


def money(value: float) -> str:
    return f"{value:.2f}"


def synthetic_transactions(seed: int = 7) -> list:
    rng = random.Random(seed)
    rows = []
    start = date(2025, 10, 1)
    day = start
    ref = 0

    def add(d, amount, indicator, text):
        nonlocal ref
        ref += 1
        rows.append(tx(d.isoformat(), money(amount), indicator, text, ref=f"{d.isoformat()}.{ref}"))

    while day <= TODAY - timedelta(days=1):
        if day.day == 28 and day.month != 11 or (day.day == 28 and day.year == 2026 and day.month < 11):
            add(day, 2000.00, "CRDT", "TRANSFERENCIA DE EMPRESA EJEMPLO SL, CONCEPTO NOMINA " + day.strftime("%B").upper())
        if day.day == 1:
            add(day, 650.00, "DBIT", "RECIBO ALQUILER VIVIENDA")
        if day.day == 10:
            add(day, 12.99, "DBIT", card_purchase("NETFLIX.COM", "MADRID"))
        if day.day == 5:
            add(day, 9.99, "DBIT", card_purchase("SPOTIFY", "ESTOCOLMO"))
        if day.day == 3:
            add(day, 29.90, "DBIT", card_purchase("GIMNASIO EJEMPLO", "MADRID"))
        if day.weekday() in (1, 4):
            add(day, round(rng.uniform(8, 90), 2), "DBIT", card_purchase(rng.choice(["MERCADONA", "LIDL", "DIA SUPERMERCADOS"])))
        if day.weekday() in (2, 5) and rng.random() < 0.8:
            add(day, round(rng.uniform(6, 45), 2), "DBIT", card_purchase(rng.choice(["BAR LA ESQUINA", "GLOVO", "RESTAURANTE DEMO"])))
        if day.weekday() == 0 and rng.random() < 0.5:
            add(day, round(rng.uniform(25, 60), 2), "DBIT", card_purchase("REPSOL", "TOLEDO"))
        if day.weekday() == 6 and rng.random() < 0.4:
            add(day, round(rng.choice([20, 40, 60]), 2), "DBIT", card_purchase("CAJERO BANCO", "MADRID"))
        if rng.random() < 0.06:
            add(day, round(rng.uniform(5, 60), 2), "CRDT", f"BIZUM DE ANA GARCIA LOPEZ CONCEPTO cena")
        if rng.random() < 0.05:
            add(day, round(rng.uniform(5, 40), 2), "DBIT", f"BIZUM A LUIS DE MARTA RUIZ SOTO CONCEPTO: regalo")
        if rng.random() < 0.015:
            add(day, round(rng.uniform(15, 120), 2), "DBIT", f"COMPRA ZARA HOME, LISBOA, TARJETA {CARD16} , COMISION 1,20")
        day += timedelta(days=1)

    # duplicados exactos el mismo día (dos cafés idénticos) y una devolución
    add(date(2026, 10, 20), 1.50, "DBIT", card_purchase("BAR LA ESQUINA"))
    add(date(2026, 10, 20), 1.50, "DBIT", card_purchase("BAR LA ESQUINA"))
    add(date(2026, 10, 21), 49.90, "DBIT", card_purchase("ZARA"))
    add(date(2026, 10, 25), 49.90, "CRDT", "ANUL PAGO EN ZARA, MADRID, TARJ. :*123456")
    add(date(2026, 11, 2), 3.00, "DBIT", "COMISION MANTENIMIENTO CUENTA")
    rows.sort(key=lambda r: (r["booking_date"], r["entry_reference"] or ""), reverse=True)  # el banco devuelve lo más reciente primero
    return rows


def synthetic_pages(seed: int = 7) -> list:
    return pages(synthetic_transactions(seed))
