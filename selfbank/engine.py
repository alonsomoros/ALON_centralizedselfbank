"""Motor real: pregunta -> interpretación -> SQL de solo lectura -> respuesta con plantillas.

Camino de una pregunta:
  ruta rápida por reglas (ms)  ──┐
  modelo local (Ollama, 5-9 s) ──┴─> pipeline (reglas, validación, fechas) -> ejecutor SQL -> plantillas
El modelo solo traduce; las cifras salen de SQL y el texto, de plantillas. Cada pregunta queda en `history`
(pregunta e interpretación, sin cifras) para poder valorar la precisión real y mejorar las reglas.
"""

import json
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import answers, db, describe, pipeline, router
from .api.server import ApiError
from .executor import Executor, ExecutorError
from .suggestions import as_list

VERSION = "0.2.0"
QUERY_SECONDS = 5.0       # una consulta SQL que tarde más se aborta
MODEL_QUEUE_SECONDS = 45  # espera máxima para usar el modelo (una consulta a la vez)


class Engine:
    def __init__(self, db_path: str, llm=None, clock=date.today, history_limit: int = 2000):
        self.db_path, self.llm, self.clock, self.history_limit = db_path, llm, clock, history_limit
        self._model_lock = threading.Lock()
        db.connect(db_path).close()  # crea el esquema si hace falta

    # ---- contrato de la API ----
    def suggestions(self) -> list:
        return as_list()

    def status(self) -> dict:
        today = self.clock()
        conn = db.connect(self.db_path, read_only=True)
        try:
            through = conn.execute("SELECT MAX(booking_date) FROM transactions").fetchone()[0]
            accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            last_sync = db.get_meta(conn, "last_sync")
            valid_until = db.get_meta(conn, "consent_valid_until")
        finally:
            conn.close()
        days_left = (date.fromisoformat(valid_until) - today).days if valid_until else None
        return {
            "ok": True, "version": VERSION, "mode": "live",
            "model": {"name": self.llm.model if self.llm else "ninguno", "loaded": bool(self.llm and self.llm.is_loaded())},
            "data": {"last_sync": last_sync, "data_through": through, "accounts": accounts},
            "consent": {"valid_until": valid_until, "days_left": days_left},
        }

    def feedback(self, answer_id: str, verdict: str, correction) -> bool:
        conn = db.connect(self.db_path)
        try:
            with conn:
                cur = conn.execute("UPDATE history SET verdict = ?, correction = ? WHERE answer_id = ?", (verdict, correction, answer_id))
            return cur.rowcount > 0
        finally:
            conn.close()

    def ask(self, question: str) -> dict:
        today = self.clock()
        fast = router.fast_intent(question)
        if fast is not None:
            source, raw = "rules", fast
        else:
            source, raw = "model", self._translate(question, today)
        query, reason = pipeline.to_query(question, raw, today)

        status = "answered"
        if query["intent"] == "unknown":
            status = "needs_clarification" if reason == "ambigua" else "unsupported"
            answer = answers.render(query)
        else:
            try:
                answer = answers.render(query, self._execute(query))
            except ExecutorError as error:
                status, reason = "unsupported", "fuera_de_alcance"
                answer = {"text": str(error), "kind": "none", "data": None}

        answer_id = "ans_" + secrets.token_hex(6)
        self._remember(answer_id, question, source, status, query)
        return {
            "id": answer_id, "question": question, "status": status, "reason": reason, "source": source,
            "interpretation": {**describe.describe(query), "query": query}, "answer": answer, "timing_ms": 0,
        }

    # ---- piezas ----
    def _translate(self, question: str, today):
        if self.llm is None:
            return None
        if not self._model_lock.acquire(timeout=MODEL_QUEUE_SECONDS):
            raise ApiError(503, "model_busy", "El modelo está ocupado; vuelve a intentarlo en unos segundos.", {"Retry-After": "10"})
        try:
            return self.llm.translate(question, today)
        finally:
            self._model_lock.release()

    def _execute(self, query: dict) -> dict:
        conn = db.connect(self.db_path, read_only=True)
        deadline = time.monotonic() + QUERY_SECONDS
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)  # aborta consultas demasiado largas
        try:
            return Executor(conn).run(query)
        except Exception as error:
            if "interrupted" in str(error).lower():
                raise ExecutorError("La consulta ha tardado demasiado; prueba con un periodo más corto")
            raise
        finally:
            conn.close()

    def _remember(self, answer_id: str, question: str, source: str, status: str, query: dict) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        conn = db.connect(self.db_path)
        try:
            with conn:
                conn.execute("INSERT INTO history (answer_id, asked_at, question, source, status, query_json) VALUES (?, ?, ?, ?, ?, ?)",
                             (answer_id, now, question, source, status, json.dumps(query, ensure_ascii=False)))
                conn.execute("DELETE FROM history WHERE answer_id NOT IN (SELECT answer_id FROM history ORDER BY asked_at DESC, rowid DESC LIMIT ?)",
                             (self.history_limit,))
        finally:
            conn.close()
