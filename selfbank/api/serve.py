"""Servicio real de la API: motor con SQLite y, si está disponible, el modelo local de Ollama.

  python -m selfbank.api.serve --db ~/.selfbank/selfbank.db --token-file ~/.selfbank/api-token --port 8789

Escucha solo en 127.0.0.1 por defecto. Para publicarlo en la tailnet usa `tailscale serve` (nunca Funnel).
"""

import argparse
import os
import sys
import threading
from datetime import date

from selfbank.api.server import make_server, read_token_file
from selfbank.engine import Engine
from selfbank.llm import OllamaClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Servicio real de selfbank")
    parser.add_argument("--db", default=os.path.expanduser("~/.selfbank/selfbank.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--token-file", default=os.environ.get("SELFBANK_TOKEN_FILE"), help="fichero 600 con el token (obligatorio)")
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--no-llm", action="store_true", help="sin modelo: solo la ruta rápida por reglas")
    parser.add_argument("--cors-origin", action="append", default=[])
    args = parser.parse_args(argv)

    if not args.token_file:
        print("ERROR: indica --token-file (el servicio real no admite un token por la línea de comandos).", file=sys.stderr)
        return 2
    if not os.path.exists(args.db):
        print(f"ERROR: no existe la base de datos {args.db}. Importa primero los datos (python -m selfbank.tools.ingest_cli).", file=sys.stderr)
        return 2
    token = read_token_file(args.token_file)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"AVISO: escuchando en {args.host}: cualquiera en esa red podrá intentar conectar. Solo el token lo protege.", file=sys.stderr)

    llm = None if args.no_llm else OllamaClient(model=args.model)
    engine = Engine(args.db, llm=llm)
    server = make_server(engine, token, args.host, args.port, args.cors_origin)
    if llm:
        threading.Thread(target=llm.warm_up, args=(date.today(),), daemon=True).start()  # evita el arranque en frío de la primera pregunta
    print(f"Servicio REAL en http://{args.host}:{args.port}  (modelo: {llm.model if llm else 'ninguno'}; Ctrl+C para parar)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nParado.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
