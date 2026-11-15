# M7 — Primer prompt en la Pi (qwen2.5:1.5b, Ollama 0.40.16)

Datos sintéticos del dataset; ningún dato bancario. Prompt v1: sistema + 2 ejemplos + pregunta (~535 tokens), `temperature 0`, `num_ctx 2048`, `num_predict 256`, `format` = `schema.json`.

## Instalación
- Script oficial de `ollama.com/install.sh` revisado y con hash comprobado antes de ejecutar. Solo descarga `ollama-linux-arm64.tar.zst`; sin firma ni hash propios (confía en HTTPS).
- Crea el usuario `ollama`, el servicio systemd (activo al arrancar) y guarda modelos en `/usr/share/ollama`. Escucha solo en `127.0.0.1:11434`.
- Coste: ~2 GB de disco (binario) + 986 MB (modelo). Licencia del modelo: Apache 2.0. `qwen2.5:3b` usa «Qwen license»: pendiente de leer antes de bajarlo.

## Recursos
| Medida | Valor |
|---|---|
| RAM usada con el modelo cargado | +~1 GiB (2,3 → 3,3 GiB); disponible 4,5 GiB |
| Temperatura | 49 → 55 °C |
| `get_throttled` | 0x0 |

## Latencia (CPU, 4 hilos)
| Fase | Valor |
|---|---|
| Arranque en frío (carga del modelo) | 4–5 s; llamada total 23 s |
| Lectura del prompt, frío | 535 tokens en 7,5 s (71 tok/s) |
| Lectura del prompt, caliente | 0,1–0,3 s (se reutiliza el prefijo común) |
| Generación | 9–11 tok/s; 85–116 tokens → 8–11,6 s |
| Llamada caliente | 8,4–11,8 s |
| `format` frente a sin `format` | mismo ritmo (10,8 tok/s) y misma salida |

El modelo se descarga de memoria tras 5 min sin uso (`keep_alive` por defecto): la siguiente pregunta paga el arranque en frío.

## Calidad (4 preguntas, solo orientativo)
| Pregunta | Resultado |
|---|---|
| q001 restaurantes en octubre | casi: `group_by: category` sobrante (no altera el número) |
| q006 este mes vs mes pasado | mal: `to` = fin de mes, `compare_to` null → JSON incoherente |
| q013 «¿Qué tal?» | intent correcto; `clarification` inventada («¿Cómo estás hoy?») |
| q003 último Bizum | mal: sin `category: bizum`, `direction: out` |

## Conclusiones
1. **Leer el prompt es barato** (prefijo cacheado). **Generar es lo caro**, y se generan campos `null` inútiles.
2. **Fechas**: el modelo no debe calcularlas (q006). Debe emitir una etiqueta de periodo y el código resuelve las fechas.
3. **`clarification`**: debe ser texto fijo del código, no del modelo.
4. **Categoría por palabras clave** (Bizum, cajero…): mejor en código que en el modelo.
5. `format` no cuesta velocidad y garantiza la forma; se mantiene.
6. `OLLAMA_KEEP_ALIVE=-1` para evitar el arranque en frío (cabe en RAM).

Propuesta: contrato v2 (periodos como etiquetas, solo campos no nulos, `clarification` en código) antes de medir modelos en M9.

---

# M7b — Contrato v2 (mismo modelo, mismas 4 preguntas)

Cambios: periodos como etiquetas (el código calcula las fechas), solo campos con valor, `unknown` con `reason` y mensaje fijo, categoría por palabras clave en código. Detalle en `bench/consultas/convenciones.md`.

| Medida | v1 | v2 |
|---|---|---|
| Tokens generados | 85–116 | 14–33 |
| Generación (caliente) | 8–11,6 s | 1,2–2,9 s |
| Llamada total caliente | 8,4–11,8 s | 1,4–2,4 s |
| Llamada total en frío | 23 s | 14 s (3,1 s de carga + 8 s leyendo el prompt) |
| Lectura del prompt caliente (otra pregunta) | 0,2–0,3 s | 0,2–0,3 s |

| Pregunta | Resultado v2 |
|---|---|
| q001 | idéntica a lo esperado |
| q003 | idéntica (categoría `bizum` por palabra clave) |
| q006 | fechas correctas; faltaban `metric` y `direction` → `metric` pasa a `sum` por defecto en `compare_periods` y el prompt aclara `direction` (pendiente de re-medir en la Pi) |
| q013 | intent correcto; `reason` `ambigua` en vez de `fuera_de_alcance` (se puntuará aparte del intent) |

Ollama aceptó el esquema con campos opcionales y enums. Sigue pendiente: `OLLAMA_KEEP_ALIVE=-1` (evita el arranque en frío) y el reparto desarrollo/prueba del dataset antes de ajustar más el prompt.
