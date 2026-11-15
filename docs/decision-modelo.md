# Decisión de modelo y de arquitectura de consulta (cierre de M7–M9)

Medido en la Pi 5 (8 GB, ventilador activo, Ollama 0.40.16, CPU), con `temperature 0`, prompt v3 y la tubería de reglas. Datos sintéticos y preguntas del usuario; ningún dato bancario.

## Resultados

| Modelo | Conjunto | Intent (sistema) | Consulta exacta | Mediana / p95 | Veredicto |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `qwen2.5:1.5b` | dev (visto) | 80 % | 76 % | 2,3 / 3,2 s | Descartado: intent < 90 % |
| `qwen2.5:3b` | dev (visto) | 92 % | 88 % | 4,7 / 7,0 s | — |
| `qwen2.5:3b` | blind (visto en parte) | 90,5 % | 86 % | 5,8 / 8,0 s | — |
| `qwen2.5:3b` | **fresh (no visto)** | 80 % | **50 %** | 5,7 / 9,2 s | **Modelo de respaldo** |
| `qwen3:4b` (sin razonar) | blind | 90,5 % | 81 % | 13,5 / 18,2 s | Descartado: 2,3× más lento, no mejora |
| `qwen3:4b` (sin razonar) | **fresh (no visto)** | 75 % | **50 %** | 14,3 / 18,2 s | Descartado |

Arranque en frío: 58–95 s (solo tras arrancar; se evita con `keep_alive` largo). RAM libre mínima 3,0–3,9 GiB. Temperatura máxima 69–73 °C, sin throttling.

## Lectura

1. **Cambiar de modelo mueve poco.** En preguntas nuevas los dos llegan al 50 % de consulta exacta. Con reglas, lo que mejora el resultado es cuánto vocabulario cubre el código.
2. **Los números sobre preguntas ya vistas son optimistas** (92 % → 80 % de intent; 88 % → 50 % exacta al pasar a preguntas nuevas). Cualquier cifra se mide con preguntas que no se usaron para ajustar.
3. Un `qwen2.5:3b` tarda 6–9 s por pregunta con el modelo cargado. Es demasiado para el caso habitual.

## Decisión

- **`qwen2.5:3b` queda como modelo de respaldo**, solo para lo que las reglas no resuelven.
- **Arquitectura híbrida**:
  1. **Atajos** en la pantalla del chat (preguntas frecuentes): instantáneo, 100 % fiable.
  2. **Ruta rápida por reglas** (`router.py`): clasifica sin modelo cuando entiende *todas* las palabras de la pregunta. Si hay una palabra desconocida (un comercio, un concepto) no adivina y pasa al modelo.
  3. **Modelo 3B** para la cola larga, con indicador «pensando…».
  4. Siempre se **muestra la interpretación** de la pregunta, y un botón «no era eso» registra la corrección en local.
- Fechas, sentido (gasto/ingreso), importes, límites y categorías inequívocas los fija el código; el modelo decide intent dudoso, comercio o concepto y métrica.
- Lo que el sistema no sabe hacer (medias, duplicados, fines de semana, suscripciones que suben, análisis o consejos) responde «no puedo» (`unknown`), nunca otra cifra.
- Una salida inválida del modelo equivale a «no te he entendido».

## Ruta rápida (medida sin modelo, sobre las 66 preguntas)

52 de 66 (79 %) se resuelven sin modelo, con acierto 100 % sobre ellas. **Es una cota optimista**: las reglas se afinaron con estas mismas preguntas. La cifra real se medirá con el uso (registro local de preguntas y correcciones).

## Pendiente

- Medir la tubería híbrida completa en la Pi (`run.py --hybrid`).
- `OLLAMA_KEEP_ALIVE` largo en el servicio, para evitar el arranque en frío.
- Capacidades nuevas en `docs/backlog-consultas.md`.
- Borrar `qwen2.5:1.5b` y `qwen3:4b` de la Pi (≈ 3,5 GB).
