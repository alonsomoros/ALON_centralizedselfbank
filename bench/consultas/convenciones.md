# Convenciones de la consulta

Contrato entre el modelo (que traduce) y el ejecutor (que calcula). El esquema formal está en `schema.json`.

## Reglas generales

- Todos los campos aparecen siempre; lo que no aplica va a `null`. `direction` nunca es `null`.
- El modelo no calcula ni ve datos: solo traduce. Los números los calcula SQL.
- Las fechas son ISO `AAAA-MM-DD`, ambas **inclusivas**. `from` y `to` van juntos (o ambos `null`). `compare_from` y `compare_to` igual.
- Sin periodo en la pregunta, `from` y `to` son `null` y el ejecutor usa todo el histórico.
- La fecha de hoy se inyecta en el prompt; el modelo solo emite fechas ISO.
- Importes en euros, siempre positivos. `direction` indica el sentido: `out` gasto, `in` ingreso, `any` ambos o no aplica.
- `text` es lo que el usuario nombra (un comercio, un concepto), tal cual lo dijo; el ejecutor lo normaliza y lo busca en el texto del movimiento.
- Bizum y transferencias son categorías propias (`bizum`, `transferencias`) y no cuentan como comercios en `top_merchants`.

## Periodos relativos

| Expresión | `from` → `to` |
| :--- | :--- |
| hoy / ayer | ese día |
| este mes | día 1 del mes → hoy |
| el mes pasado | mes natural anterior completo |
| esta semana | lunes → hoy |
| la semana pasada | lunes → domingo anteriores |
| este año | 1 de enero → hoy |
| el año pasado | año natural anterior completo |
| los últimos N días | hoy − (N − 1) → hoy (N días contando hoy) |
| «en octubre» (mes sin año) | ese mes del año más reciente que no empiece en el futuro |

## Intents

| Intent | Campos que exige | Notas |
| :--- | :--- | :--- |
| `aggregate` | `metric` (`sum` o `count`) | `group_by` opcional (`category`, `merchant`, `month`) |
| `list_transactions` | — | `sort` y `limit` opcionales; por defecto, los más recientes |
| `last_transaction` | — | el más reciente que cumpla los filtros |
| `balance` | — | todos los demás campos a `null`, `direction: any` |
| `compare_periods` | `metric`, `from`/`to` y `compare_from`/`compare_to` | el periodo anterior es completo |
| `top_merchants` | `group_by: merchant` | `limit` opcional |
| `recurring` | — | detecta cargos periódicos; `category` y `text` opcionales |
| `unknown` | `clarification` | pregunta ambigua o fuera de alcance; el resto a `null` |

`clarification` solo se rellena en `unknown`, con una pregunta breve en español para aclarar.
