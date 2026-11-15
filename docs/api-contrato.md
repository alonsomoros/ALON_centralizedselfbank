# Contrato de la API (v1)

Fuente de verdad legible por máquinas: `selfbank/api/schemas.json` (JSON Schema). Este documento lo explica. Lo cumplen el **servidor simulado** (`selfbank/api/mock.py`) y, más adelante, el servicio real; `tests/test_api.py` comprueba cada respuesta contra los esquemas.

## Cómo probarlo sin pantalla

```bash
python -m selfbank.api.mock --port 8788 --token dev-token --model-delay 3 --cors-origin http://localhost:5173
```
```bash
# Linux/macOS/Git Bash (el cuerpo debe ser UTF-8; en consolas de Windows usa --data-binary @fichero.json)
curl -s -X POST http://127.0.0.1:8788/api/ask \
  -H "Authorization: Bearer dev-token" -H "Content-Type: application/json" \
  -d '{"question":"¿Cuánto gasté en restaurantes en octubre?"}'
```
```powershell
# PowerShell
$h = @{ Authorization = "Bearer dev-token" }
Invoke-RestMethod http://127.0.0.1:8788/api/ask -Method Post -Headers $h -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes('{"question":"¿Cuál es mi saldo?"}'))
```

Preguntas especiales para probar estados de la pantalla: `__busy__` (503), `__error__` (500). Cualquier pregunta que las reglas no resuelvan sola tarda `--model-delay` segundos (modelo simulado).

## Reglas generales

- JSON UTF-8. Todas las rutas bajo `/api`. Respuestas con `Cache-Control: no-store`.
- Autenticación: `Authorization: Bearer <token>` en todo salvo `GET /api/health`. Sin token válido: `401`. Estar en la tailnet **no** sustituye al token.
- El token vive en `sessionStorage` o en memoria, nunca en la URL ni en `localStorage`.
- La pantalla se sirve desde el mismo origen que la API (la Pi), así que no necesita CORS. El simulado lo admite solo para tu desarrollo en el PC (`--cors-origin`).
- La API es **síncrona**: `POST /api/ask` responde cuando termina (≈ 0 ms por reglas, 5–10 s si interviene el modelo). La pantalla muestra «pensando…» si tarda más de ~600 ms.
- Una consulta al modelo a la vez. Si la cola se satura: `503 model_busy` con `Retry-After`.
- El servidor no guarda ni registra el texto de las preguntas en logs.

## Rutas

| Método | Ruta | Para qué |
| :--- | :--- | :--- |
| GET | `/api/health` | ¿Está vivo? Sin token. `{"ok": true}` |
| GET | `/api/status` | Estado: modelo, última sincronización, caducidad del consentimiento |
| GET | `/api/suggestions` | Botones de preguntas frecuentes (todas se resuelven al instante) |
| POST | `/api/ask` | Hacer una pregunta |
| POST | `/api/feedback` | Marcar una respuesta como correcta o errónea («no era eso») |

### POST /api/ask

Petición: `{"question": "¿Cuánto gasté en restaurantes en octubre?"}` (1–500 caracteres; `conversation_id` opcional, reservado).

Respuesta `200`:

```json
{
  "id": "ans_3f9c1a7b2d04",
  "question": "¿Cuánto gasté en restaurantes en octubre?",
  "status": "answered",
  "reason": null,
  "source": "rules",
  "interpretation": {
    "title": "Gasto total",
    "chips": ["restaurantes", "1 oct – 31 oct 2026"],
    "text": "Gasto total · restaurantes · 1 oct – 31 oct 2026",
    "query": { "intent": "aggregate", "metric": "sum", "direction": "out", "category": "restaurantes", "from": "2026-10-01", "to": "2026-10-31", "...": "15 campos siempre" }
  },
  "answer": {
    "text": "Has gastado 1.768,90 € (restaurantes) del 1 oct al 31 oct 2026.",
    "kind": "scalar",
    "data": { "value": 1768.9, "unit": "EUR" }
  },
  "timing_ms": 3,
  "mock": true
}
```

| Campo | Significado |
| :--- | :--- |
| `status` | `answered`, `needs_clarification` (falta contexto) o `unsupported` (no sé hacerlo) |
| `reason` | Solo si no es `answered`: `ambigua`, `fuera_de_alcance` o `operacion` |
| `source` | `rules` (instantáneo) o `model` (pasó por el modelo) |
| `interpretation` | **Cómo se entendió la pregunta**: muéstralo siempre. `chips` son etiquetas para pintar |
| `answer.text` | Frase lista para mostrar. Siempre sale de plantillas, nunca del modelo |
| `answer.kind` / `data` | `scalar` → `{value, unit: "EUR"\|"count"}`; `table` → `{columns, rows}`; `none` → `null` |
| `mock` | Solo en el simulado: las cifras son inventadas |

Tabla (`kind: "table"`): `columns` = `[{key, label, type}]` con `type` ∈ `text`, `date`, `money`, `count`; cada fila es un array del mismo largo que `columns`. Los importes de `money` son números (negativos = gasto) y los formateas tú, o usas `answer.text`.

Cuando `status` no es `answered`, `answer.text` ya contiene el mensaje para el usuario («No tengo claro qué quieres saber…») y `kind` es `none`.

### POST /api/feedback

`{"answer_id": "ans_…", "verdict": "ok" | "wrong", "correction": "quería el mes pasado"}` (`correction` opcional, ≤ 300). Respuesta `{"stored": true}`; `404` si el id no existe. Solo se guarda en local, sin cifras.

### GET /api/status

```json
{ "ok": true, "version": "0.1.0", "mode": "mock",
  "model": { "name": "qwen2.5:3b (simulado)", "loaded": true },
  "data": { "last_sync": "2026-11-15T14:47:00+00:00", "data_through": "2026-11-14", "accounts": 1 },
  "consent": { "valid_until": "2027-04-15", "days_left": 151 } }
```
`consent.days_left` sirve para avisar cuando quede poco para reautorizar (≤ 14 días).

## Errores

Siempre `{"error": {"code": "...", "message": "..."}}`.

| HTTP | `code` | Cuándo | Qué hacer en la pantalla |
| :--- | :--- | :--- | :--- |
| 400 | `invalid_request` | JSON mal formado, campo ausente o fuera de rango | Es un fallo del cliente |
| 401 | `unauthorized` | Token ausente o inválido (`WWW-Authenticate: Bearer`) | Pedir el token |
| 404 | `not_found` | Ruta o `answer_id` inexistente | — |
| 405 | `method_not_allowed` | Método incorrecto | — |
| 413 | `payload_too_large` | Cuerpo > 8 KB | — |
| 415 | `unsupported_media_type` | Falta `Content-Type: application/json` | — |
| 503 | `model_busy` | Cola del modelo saturada (`Retry-After` en segundos) | «Estoy ocupado», reintentar |
| 500 | `internal` | Error del servidor (sin detalles internos) | Mensaje genérico |

## Guía de pantalla (recomendación)

1. Al abrir: `GET /status` y `GET /suggestions`; muestra los botones.
2. Al enviar: `POST /ask`; si pasan ~600 ms sin respuesta, muestra «pensando…».
3. Pinta `interpretation.chips` junto a la respuesta, `answer.text`, y la tabla si `kind` es `table`.
4. Si `status` ≠ `answered`, muéstralo como mensaje del asistente con 2–3 sugerencias.
5. Botón «no era eso» → `POST /feedback` con `verdict: "wrong"`.
6. Si `consent.days_left` ≤ 14, avisa de reautorizar.

## Fuera de la v1

Streaming de la interpretación antes de la cifra, memoria de conversación («¿y el mes anterior?»), varios usuarios y paginación de tablas largas.
