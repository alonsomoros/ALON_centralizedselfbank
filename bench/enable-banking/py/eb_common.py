"""Utilidades compartidas de los scripts de sondeo de Fase 0 (Enable Banking).

Sin dependencias externas: solo biblioteca estándar y `openssl` para firmar.
Regla de oro: nada de lo que se imprime contiene importes, textos de movimientos
ni IBAN. Solo nombres de campos, tipos y recuentos.
"""

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.enablebanking.com"

# Los ID de aplicación no son secretos. Las claves viven fuera del repo.
PROFILES = {
    "sandbox": {
        "app_id": "b239298e-83c6-4e7d-94f0-f8f6050da48c",
        "key_name": "b239298e-83c6-4e7d-94f0-f8f6050da48c.pem",
        "redirect_url": "http://localhost:8765/callback",
    },
    "prod": {
        "app_id": "6bab54bb-4a48-491f-b026-0d82661114c7",
        "key_name": "selfbank-prod.pem",
        "redirect_url": "https://localhost:8765/callback",
    },
}


class EbError(Exception):
    """Error esperado: se muestra el mensaje, sin traza."""


def home_dir() -> Path:
    return Path(os.environ.get("SELFBANK_HOME", Path.home() / ".selfbank"))


def profile(name: str) -> dict:
    base = PROFILES[name]
    return {**base, "key": home_dir() / base["key_name"], "label": name}


def pending_path(label: str) -> Path:
    return home_dir() / f"pending-{label}.json"


def session_path(label: str) -> Path:
    return home_dir() / f"session-{label}.json"


def raw_dir(label: str) -> Path:
    return home_dir() / "raw" / label


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def find_openssl() -> str:
    found = os.environ.get("OPENSSL") or shutil.which("openssl")
    if found:
        return found
    git = Path(r"C:\Program Files\Git\usr\bin\openssl.exe")
    if git.exists():
        return str(git)
    raise EbError("No se encuentra openssl. Indica su ruta en la variable OPENSSL.")


def sign_jwt(app_id: str, key_path, ttl: int = 3600, now=None) -> str:
    """JWT RS256. La clave se lee con openssl y no se imprime nunca."""
    iat = int(now if now is not None else time.time())
    header = {"typ": "JWT", "alg": "RS256", "kid": app_id}
    payload = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": iat, "exp": iat + ttl}
    enc = lambda obj: b64url(json.dumps(obj, separators=(",", ":")).encode())
    signing_input = f"{enc(header)}.{enc(payload)}"
    result = subprocess.run(
        [find_openssl(), "dgst", "-sha256", "-sign", str(key_path)],
        input=signing_input.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise EbError("openssl no pudo firmar (¿ruta de la clave correcta?): " + result.stderr.decode(errors="replace")[:200])
    return f"{signing_input}.{b64url(result.stdout)}"


def request(method: str, path: str, jwt: str, body=None, params=None) -> dict:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise EbError(f"HTTP {error.code} en {method} {path}: {error.read().decode(errors='replace')[:300]}") from None
    except urllib.error.URLError as error:
        raise EbError(f"No se pudo conectar con la API: {error.reason}") from None


def valid_until(days: int = 179, now=None) -> str:
    """RFC 3339 con zona horaria, como exige la API."""
    moment = (now or datetime.now(timezone.utc)) + timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00")


def secure_write_json(path: Path, obj) -> None:
    """Escribe JSON con permisos 600 (en Windows el modo se ignora)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EbError(f"No existe {path}. ¿Has ejecutado el paso anterior?") from None


def extract_code(callback_url: str, expected_state: str) -> str:
    """Saca el `code` de la URL pegada y valida `state`. Falla si algo no cuadra."""
    text = callback_url.strip()
    query = urllib.parse.urlparse(text).query if "://" in text else text.lstrip("?")
    params = urllib.parse.parse_qs(query)
    if "error" in params:
        raise EbError(f"El banco devolvió un error: {params['error'][0]}")
    if params.get("state", [None])[0] != expected_state:
        raise EbError("El parámetro state no coincide con el de esta autorización. No se usa el code.")
    code = params.get("code", [None])[0]
    if not code:
        raise EbError("La URL pegada no contiene el parámetro code.")
    return code


def collect_schema(value, path: str = "", out=None) -> dict:
    """Mapa campo -> tipos observados. No guarda ningún valor."""
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, item in value.items():
            collect_schema(item, f"{path}.{key}" if path else key, out)
    elif isinstance(value, list):
        if not value:
            out.setdefault(path + "[]", set()).add("vacía")
        for item in value:
            collect_schema(item, path + "[]", out)
    else:
        out.setdefault(path, set()).add(type(value).__name__)
    return out


def print_schema(schema: dict, indent: str = "    ") -> None:
    for field in sorted(schema):
        print(f"{indent}{field}: {'/'.join(sorted(schema[field]))}")


def mask_tail(text: str, keep: int = 4) -> str:
    return "..." + text[-keep:] if text else "(vacío)"
