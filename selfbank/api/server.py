"""Servidor HTTP de la API (solo biblioteca estándar). No sabe de bancos: delega en un `engine`.

Rutas (todas bajo /api, JSON UTF-8, token Bearer salvo /api/health):
  GET  /api/health       {"ok": true}                       sin autenticación
  GET  /api/status       estado del sistema
  GET  /api/suggestions  preguntas sugeridas (botones)
  POST /api/ask          {"question": "..."} -> respuesta con interpretación
  POST /api/feedback     {"answer_id", "verdict", "correction"?}

El `engine` implementa ask(question), status(), suggestions() y feedback(answer_id, verdict, correction).
Los errores se devuelven como {"error": {"code", "message"}}.
"""

import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selfbank.schema import check

SCHEMAS = json.loads((Path(__file__).parent / "schemas.json").read_text(encoding="utf-8"))
MAX_BODY = 8 * 1024


def read_token_file(path: str) -> str:
    """Lee el token de un fichero (mejor que ponerlo en la línea de comandos, donde `ps` lo muestra)."""
    mode = os.stat(path).st_mode
    if os.name == "posix" and mode & 0o077:
        raise PermissionError(f"{path} es legible por otros usuarios: ejecuta chmod 600 {path}")
    with open(path, encoding="utf-8") as handle:
        token = handle.read().strip()
    if not token:
        raise ValueError(f"{path} está vacío")
    return token


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


class Handler(BaseHTTPRequestHandler):
    # los fija make_server
    engine = None
    token = ""
    cors_origins = frozenset()
    validate_responses = True
    quiet = False
    protocol_version = "HTTP/1.1"
    timeout = 30  # corta clientes que se quedan a medias

    ROUTES = {
        ("GET", "/api/health"): "_health",
        ("GET", "/api/status"): "_status",
        ("GET", "/api/suggestions"): "_suggestions",
        ("POST", "/api/ask"): "_ask",
        ("POST", "/api/feedback"): "_feedback",
    }
    PUBLIC = {"/api/health"}

    # ---- plumbing ----
    def log_message(self, fmt, *args):
        if not self.quiet:  # solo la línea de petición, nunca el cuerpo (puede contener preguntas)
            super().log_message(fmt, *args)

    def _send(self, status: int, payload=None, headers: dict = None):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if self._unread_body():
            # Si se responde sin consumir el cuerpo de la petición, esos bytes se leerían como la
            # siguiente petición de la conexión persistente. Se cierra la conexión para evitarlo.
            self.close_connection = True
            self.send_header("Connection", "close")
        if payload is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in {**self._cors_headers(), **(headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _unread_body(self) -> bool:
        if self.command != "POST" or getattr(self, "_body_consumed", False):
            return False
        try:
            return int(self.headers.get("Content-Length") or 0) > 0
        except ValueError:
            return True

    def _cors_headers(self) -> dict:
        origin = self.headers.get("Origin")
        if origin and ("*" in self.cors_origins or origin in self.cors_origins):
            return {
                "Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Max-Age": "600",
            }
        return {}

    def _error(self, error: ApiError):
        payload = {"error": {"code": error.code, "message": error.message}}
        self._send(error.status, payload, error.headers)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, value = header.partition(" ")
        return scheme == "Bearer" and hmac.compare_digest(value.encode(), self.token.encode())

    def _read_json(self, schema_name: str) -> dict:
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            raise ApiError(415, "unsupported_media_type", "El cuerpo debe ser application/json.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(400, "invalid_request", "Falta Content-Length.")
        if length > MAX_BODY:
            raise ApiError(413, "payload_too_large", f"El cuerpo supera {MAX_BODY} bytes.")
        raw = self.rfile.read(length)
        self._body_consumed = True
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "invalid_request", "El cuerpo no es JSON válido.")
        errors = check(payload, SCHEMAS[schema_name])
        if errors:
            raise ApiError(400, "invalid_request", errors[0])
        return payload

    def _respond(self, status: int, payload: dict, schema_name: str):
        if self.validate_responses:
            errors = check(payload, SCHEMAS[schema_name])
            if errors:
                raise ApiError(500, "internal", f"La respuesta incumple el contrato: {errors[0]}")
        self._send(status, payload)

    # ---- dispatch ----
    def _dispatch(self):
        path = self.path.split("?", 1)[0]
        self._body_consumed = False
        try:
            handler = self.ROUTES.get((self.command, path))
            if handler is None:
                if any(p == path for _, p in self.ROUTES):
                    raise ApiError(405, "method_not_allowed", f"{self.command} no está permitido en {path}.")
                raise ApiError(404, "not_found", f"No existe {path}.")
            if path not in self.PUBLIC and not self._authorized():
                raise ApiError(401, "unauthorized", "Falta el token o no es válido.", {"WWW-Authenticate": "Bearer"})
            getattr(self, handler)()
        except ApiError as error:
            self._error(error)
        except Exception:  # nunca se filtra la traza al cliente
            self._error(ApiError(500, "internal", "Error interno."))
            raise

    do_GET = do_POST = _dispatch

    def do_OPTIONS(self):  # preflight de CORS
        self._send(204)

    # ---- rutas ----
    def _health(self):
        self._respond(200, {"ok": True}, "Health")

    def _status(self):
        self._respond(200, self.engine.status(), "Status")

    def _suggestions(self):
        self._respond(200, {"suggestions": self.engine.suggestions()}, "Suggestions")

    def _ask(self):
        request = self._read_json("AskRequest")
        started = time.perf_counter()
        response = self.engine.ask(request["question"].strip())
        response["timing_ms"] = int((time.perf_counter() - started) * 1000)
        self._respond(200, response, "AskResponse")

    def _feedback(self):
        request = self._read_json("FeedbackRequest")
        stored = self.engine.feedback(request["answer_id"], request["verdict"], request.get("correction"))
        if not stored:
            raise ApiError(404, "not_found", "No conozco esa respuesta.")
        self._respond(200, {"stored": True}, "FeedbackResponse")


def make_server(engine, token: str, host: str = "127.0.0.1", port: int = 8788, cors_origins=(), quiet: bool = False,
                validate_responses: bool = True) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("El token no puede estar vacío")
    handler = type("BoundHandler", (Handler,), {
        "engine": engine, "token": token, "cors_origins": frozenset(cors_origins),
        "quiet": quiet, "validate_responses": validate_responses,
    })
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
