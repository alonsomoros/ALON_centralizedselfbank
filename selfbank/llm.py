"""Cliente de Ollama: traduce una pregunta a la consulta compacta. Solo habla con la propia máquina.

- Solo acepta un servidor local (127.0.0.1, localhost o ::1): ningún dato sale de la Pi.
- Cualquier fallo (red, tiempo, JSON o esquema inválidos) devuelve None: el motor responde «no te he entendido».
- `keep_alive` largo mantiene el modelo cargado (evita el arranque en frío de ~1 min).
"""

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import contract, prompt
from .schema import check

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class OllamaClient:
    def __init__(self, model: str = "qwen2.5:3b", host: str = "http://127.0.0.1:11434", keep_alive=-1, timeout: float = 45.0):
        if urlparse(host).hostname not in LOCAL_HOSTS:
            raise ValueError("Ollama debe estar en la propia máquina (127.0.0.1): los datos no pueden salir de la Pi")
        self.model, self.host, self.keep_alive, self.timeout = model, host.rstrip("/"), keep_alive, timeout  # -1 = no descargar el modelo
        self.failures, self.last_error = 0, None  # los fallos no son silenciosos: se cuentan (sin guardar la pregunta)
        self._schema = prompt.schema_for_ollama()
        self._validator = contract.load_schema(contract.MODEL_SCHEMA_PATH)

    def _post(self, path: str, body: dict, timeout: float = None) -> dict:
        request = urllib.request.Request(self.host + path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            return json.load(response)

    def translate(self, question: str, today, timeout: float = None):
        """Consulta compacta del modelo, o None si algo falla o no cumple el esquema."""
        body = {
            "model": self.model, "messages": prompt.build_messages(question, today.isoformat()), "stream": False,
            "format": self._schema, "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 256},
        }
        try:
            content = self._post("/api/chat", body, timeout)["message"]["content"]
            got = json.loads(content)
        except (OSError, urllib.error.URLError, KeyError, TypeError, ValueError) as error:
            return self._fail(f"{type(error).__name__}: {str(error)[:120]}")
        if not isinstance(got, dict) or check(got, self._validator):
            return self._fail("la respuesta del modelo no cumple el esquema")
        return got

    def _fail(self, reason: str):
        self.failures += 1
        self.last_error = reason
        return None

    def is_loaded(self) -> bool:
        """¿Está el modelo cargado en memoria ahora mismo?"""
        try:
            with urllib.request.urlopen(self.host + "/api/ps", timeout=3) as response:
                return any(m.get("name") == self.model for m in json.load(response).get("models", []))
        except (OSError, ValueError):
            return False

    def warm_up(self, today, timeout: float = 240.0) -> None:
        """Carga el modelo y el prefijo del prompt en memoria con una pregunta inocua.

        El arranque en frío tarda ~1 minuto (carga del modelo + lectura del prompt), más que el plazo normal de una pregunta.
        """
        self.translate("¿Cuál es mi saldo?", today, timeout)
