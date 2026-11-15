# Convenciones de la consulta (contrato v2)

Contrato entre el modelo (que traduce) y el ejecutor (que calcula). Hay **dos formas** de la misma consulta:

| Forma | Quién la usa | Fichero | Cómo es |
| :--- | :--- | :--- | :--- |
| **Del modelo** | salida de Ollama | `selfbank/model.schema.json` | compacta: solo `intent` es obligatorio; los campos sin valor se **omiten** (nada de `null`) |
| **Completa** | el ejecutor SQL | `selfbank/resolved.schema.json` | siempre los 15 campos; lo que no aplica va a `null`, con fechas ISO |

`contract.py` convierte una en otra (`expand`). **El modelo nunca calcula fechas ni redacta texto**: solo elige etiquetas y valores de listas cerradas.

## Qué hace el código y no el modelo
- **Fechas**: el modelo escribe `period` (p. ej. `mes_pasado`); el código las resuelve (`resolve_period`).
- **Mensajes de `unknown`**: el modelo elige `reason` (`ambigua`, `fuera_de_alcance`, `operacion`); el texto es fijo (`MESSAGES`).
- **Valores por defecto**: `direction` = `any`; `top_merchants` implica `group_by: merchant`.
- **Categorías inequívocas por palabra clave** (`refine`): si el modelo no puso categoría y la pregunta dice *Bizum*, *cajero* / *sacar dinero*, o *comisión(es)*, se pone `bizum`, `efectivo` o `comisiones`. Nunca pisa la categoría del modelo, ni actúa en `unknown`, `balance` ni `compare_periods`.

## Tubería de la consulta del modelo
`forma (model.schema.json)` → `sanitize` → `model_errors` → `refine` → `expand`.
- **`sanitize`** ignora los campos que no corresponden al intent (p. ej. `reason` fuera de `unknown`, `compare_period` fuera de `compare_periods`). No inventa ni corrige valores: si falta un campo obligatorio, sigue siendo un error y se pide reformular.
- **Valores por defecto** (`expand`): `direction` = `any`, salvo `top_merchants` y `recurring`, que son siempre gastos (`out`) si el modelo no dice otra cosa; `compare_periods` sin `metric` = `sum`; `top_merchants` implica `group_by: merchant`.

## Reglas generales
- Importes en euros, siempre positivos. `direction`: `out` gasto, `in` ingreso, `any` ambos o no aplica.
- Sin periodo en la pregunta, no hay `period` y el ejecutor usa todo el histórico.
- `text` es lo que el usuario nombra (un comercio, un concepto), tal cual; el ejecutor lo normaliza y lo busca en el texto del movimiento.
- Bizum y transferencias son categorías propias y no cuentan como comercios en `top_merchants`.
- Fechas completas: ISO `AAAA-MM-DD`, ambas **inclusivas**; `from`/`to` van juntos, y `compare_from`/`compare_to` también.

## Etiquetas de periodo
| Etiqueta | `from` → `to` |
| :--- | :--- |
| `hoy`, `ayer` | ese día |
| `este_mes` | día 1 del mes → hoy |
| `mes_pasado` | mes natural anterior completo |
| `esta_semana` | lunes → hoy |
| `semana_pasada` | lunes → domingo anteriores |
| `este_trimestre` | día 1 del trimestre natural (ene/abr/jul/oct) → hoy |
| `este_ano` | 1 de enero → hoy |
| `ano_pasado` | año natural anterior completo |
| `ultimos_7_dias`, `ultimos_15_dias`, `ultimos_30_dias`, `ultimos_90_dias`, `ultimos_365_dias` | hoy − (N − 1) → hoy (N días contando hoy) |
| `enero` … `diciembre` | ese mes completo del año más reciente que no empiece en el futuro |

- «El último mes» (sin «pasado») = `ultimos_30_dias`. «El último año» / «12 meses» = `ultimos_365_dias`.
- Un periodo fuera de la lista (quincenas, «este fin de semana», «últimos 10 días») no se puede expresar: da `unknown` (`ambigua`).

## Comparaciones
En `compare_periods`, `period` es el periodo por el que se pregunta y `compare_period` el de referencia («¿gasté más en octubre que en septiembre?» → `period: octubre`, `compare_period: septiembre`). Ambos van completos tal como los resuelve la tabla; la respuesta del ejecutor añade además el tramo equivalente (mismos días) cuando el periodo en curso está incompleto.

## Solo lectura
Selfbank solo consulta. Cualquier orden de operar (transferir, pagar, bloquear tarjeta…) es `unknown` con `reason: operacion`. El análisis o consejo (patrones, dónde ahorrar, presupuestos) es `unknown` con `reason: fuera_de_alcance` en esta versión.

## Intents
| Intent | Campos que exige | Notas |
| :--- | :--- | :--- |
| `aggregate` | `metric` (`sum` o `count`) | `group_by` opcional (`category`, `merchant`, `month`); con `group_by` admite `sort` y `limit` («el mes con más gastos») |
| `list_transactions` | — | `sort` y `limit` opcionales; por defecto, los más recientes |
| `last_transaction` | — | el más reciente que cumpla los filtros |
| `balance` | — | ningún otro campo |
| `compare_periods` | `metric`, `period` y `compare_period` | |
| `top_merchants` | — | `group_by` implícito; `limit` opcional |
| `recurring` | — | detecta cargos periódicos; `category` y `text` opcionales |
| `unknown` | `reason` | ningún otro campo |

«Gasto en X» sin periodo = todo el histórico.
