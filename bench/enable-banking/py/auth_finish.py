"""Paso 2: canjea el `code` del callback por una sesión (POST /sessions).

Uso:  python auth_finish.py --profile sandbox

Te pedirá pegar la URL completa del callback. No se muestra al pegarla y no
queda en el historial de la terminal. El `code` es de un solo uso.
"""

import argparse
import getpass
import sys

import eb_common as eb


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=eb.PROFILES, required=True)
    args = parser.parse_args(argv)

    prof = eb.profile(args.profile)
    try:
        pending = eb.load_json(eb.pending_path(args.profile))
        pasted = getpass.getpass("Pega la URL completa del callback (no se mostrará) y pulsa Enter: ")
        code = eb.extract_code(pasted, pending["state"])
        jwt = eb.sign_jwt(prof["app_id"], prof["key"])
        response = eb.request("POST", "/sessions", jwt, {"code": code})
    except eb.EbError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    accounts = response.get("accounts", [])
    # Se guarda lo mínimo para consultar datos: nada de IBAN ni nombres.
    session = {
        "session_id": response.get("session_id"),
        "aspsp": response.get("aspsp"),
        "psu_type": response.get("psu_type"),
        "valid_until": (response.get("access") or {}).get("valid_until"),
        "accounts": [
            {
                "uid": account.get("uid"),
                "identification_hash": account.get("identification_hash"),
                "currency": account.get("currency"),
            }
            for account in accounts
        ],
    }
    eb.secure_write_json(eb.session_path(args.profile), session)
    eb.pending_path(args.profile).unlink(missing_ok=True)  # el code ya está usado

    print("\nSesión creada y guardada.")
    print(f"  banco       : {(session['aspsp'] or {}).get('name')}")
    print(f"  válida hasta: {session['valid_until']}")
    print(f"  cuentas     : {len(accounts)}")
    for index, account in enumerate(session["accounts"], start=1):
        print(f"    {index}. uid {eb.mask_tail(account['uid'] or '')}  moneda {account['currency']}")
    print("\nEstructura de la respuesta (solo nombres y tipos):")
    eb.print_schema(eb.collect_schema(response))
    print("\nSiguiente paso: python fetch_probe.py --profile", args.profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
