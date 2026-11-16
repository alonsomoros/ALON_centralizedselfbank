"""Cliente mínimo de Enable Banking para la sincronización sin supervisión. Solo biblioteca estándar y `openssl`.

La clave privada se lee con `openssl` al firmar y nunca se imprime ni se guarda. Los mensajes de error llevan el código
HTTP y como mucho un fragmento corto del cuerpo (nunca datos bancarios: los errores de la API no los incluyen).
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
from pathlib import Path

API = "https://api.enablebanking.com"
PROD_APP_ID = "6bab54bb-4a48-491f-b026-0d82661114c7"  # identificador público, no es secreto
PROD_KEY_NAME = "selfbank-prod.pem"
TIMEOUT_SECONDS = 30
ERROR_BODY_CHARS = 120


class BankError(Exception):
    """Error esperado al hablar con el banco: se muestra su mensaje, sin traza."""


def home_dir() -> Path:
    return Path(os.environ.get("SELFBANK_HOME", Path.home() / ".selfbank"))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_jwt(app_id: str, key_path, ttl: int = 3600, now=None) -> str:
    """JWT RS256 firmado con `openssl dgst`."""
    iat = int(now if now is not None else time.time())
    header = {"typ": "JWT", "alg": "RS256", "kid": app_id}
    payload = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": iat, "exp": iat + ttl}
    encode = lambda obj: _b64url(json.dumps(obj, separators=(",", ":")).encode())
    signing_input = f"{encode(header)}.{encode(payload)}"
    openssl = os.environ.get("OPENSSL") or shutil.which("openssl")
    if not openssl:
        raise BankError("No se encuentra openssl.")
    result = subprocess.run([openssl, "dgst", "-sha256", "-sign", str(key_path)], input=signing_input.encode(), capture_output=True)
    if result.returncode != 0:
        raise BankError("openssl no pudo firmar (¿ruta o permisos de la clave?)")
    return f"{signing_input}.{_b64url(result.stdout)}"


def make_fetch(app_id: str, key_path, sign=sign_jwt, opener=urllib.request.urlopen):
    """Devuelve `fetch(path, params=None) -> dict` (GET autenticado). El JWT se firma una vez, al primer uso."""
    state = {}

    def fetch(path: str, params: dict = None) -> dict:
        if "jwt" not in state:
            state["jwt"] = sign(app_id, key_path)
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {state['jwt']}", "Accept": "application/json"})
        try:
            with opener(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:ERROR_BODY_CHARS]
            raise BankError(f"HTTP {error.code} en GET {path.split('?')[0]}: {detail}") from None
        except urllib.error.URLError as error:
            raise BankError(f"No se pudo conectar con la API: {error.reason}") from None
        except (ValueError, OSError) as error:
            raise BankError(f"Respuesta no válida de la API ({type(error).__name__})") from None

    return fetch
