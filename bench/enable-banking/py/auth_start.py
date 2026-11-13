"""Paso 1: pide a Enable Banking una URL de autorización (POST /auth).

Uso:
  python auth_start.py --profile sandbox --bank BBVA --dry-run
  python auth_start.py --profile sandbox --bank BBVA
  python auth_start.py --profile prod --bank "Banco Santander"

IMPORTANTE: cada autorización nueva invalida la anterior (España: una sesión
activa por usuario). No lo ejecutes dos veces sin necesidad.
"""

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone

import eb_common as eb


def build_body(args, state: str) -> dict:
    return {
        "access": {"valid_until": eb.valid_until(args.days)},
        "aspsp": {"name": args.bank, "country": args.country},
        "state": state,
        "redirect_url": args.redirect_url,
        "psu_type": args.psu_type,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=eb.PROFILES, required=True)
    parser.add_argument("--bank", required=True, help='Nombre exacto del banco, p. ej. "Banco Santander"')
    parser.add_argument("--country", default="ES")
    parser.add_argument("--psu-type", default="personal", choices=["personal", "business"])
    parser.add_argument("--days", type=int, default=179, help="Validez del acceso (máximo del banco: 180)")
    parser.add_argument("--redirect-url", default=None, help="Por defecto, la registrada para el perfil")
    parser.add_argument("--dry-run", action="store_true", help="Muestra la petición sin enviarla")
    args = parser.parse_args(argv)

    prof = eb.profile(args.profile)
    args.redirect_url = args.redirect_url or prof["redirect_url"]
    state = secrets.token_urlsafe(24)
    body = build_body(args, state)

    if args.dry_run:
        shown = {**body, "state": "(aleatorio, se guarda en el fichero pendiente)"}
        print("DRY-RUN: no se envía nada. Petición POST /auth:")
        print(json.dumps(shown, indent=2, ensure_ascii=False))
        print(f"Aplicación: {prof['app_id']}  |  clave: {prof['key']}")
        return 0

    try:
        jwt = eb.sign_jwt(prof["app_id"], prof["key"])
        response = eb.request("POST", "/auth", jwt, body)
    except eb.EbError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    eb.secure_write_json(
        eb.pending_path(args.profile),
        {
            "state": state,
            "authorization_id": response.get("authorization_id"),
            "redirect_url": args.redirect_url,
            "aspsp": body["aspsp"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    print("\nAutorización creada. Abre esta URL en el navegador (enlace de un solo uso, no lo compartas):\n")
    print(response["url"])
    print(
        "\nTras entrar en el banco y confirmar, el navegador irá a la redirect URL y puede mostrar un error de conexión."
        "\nEs lo esperado: copia la URL COMPLETA de la barra de direcciones y ejecuta auth_finish.py."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
