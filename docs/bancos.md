# Cobertura de bancos (M4)

Catálogo real de producción (`GET /aspsps?country=ES`, aplicación `selfbank` activa): 120 entidades para España.

## Banco Santander

| Campo | Valor |
| :--- | :--- |
| Presente en el catálogo | sí |
| Tipos de cliente | `personal`, `business` |
| Método de autenticación (personal) | `REDIRECT` (login en la web de Santander y confirmación en su app) |
| Consentimiento máximo | 15 552 000 s = 180 días |
| Beta | no |
| BIC | `BSCHESMMXXX` |

## Medido con datos reales (M5b)

- Autorización por API con `psu_type: personal` y `strategy=longest`: funciona.
- Histórico devuelto: 730 días exactos (2 años), probablemente un tope del banco o de la API.
- Paginación con `continuation_key`: unos 100 movimientos por página.
- Saldos: `CLBD` (contable al cierre) y `OPBD` (contable a la apertura).
- Todos los movimientos llegan como `BOOK`; no aparecen pendientes con la consulta por defecto.
- `creditor` y `debtor` llegan siempre vacíos: **no hay nombre de contraparte**. El único texto es `remittance_information[]`.
- `transaction_id`, `merchant_category_code`, `bank_transaction_code` y `note` llegan vacíos; `entry_reference` a veces falta.
- El sandbox no es representativo: allí sí había contrapartes con nombre.

## Formato de `remittance_information` (M5c)

Siempre hay exactamente un texto por movimiento (hasta 140 caracteres). Formas dominantes:

| Tipo | Forma | Qué se puede extraer |
| :--- | :--- | :--- |
| Gasto con tarjeta | `PAGO … EN <comercio>, <lugar>, TARJ. :*<6 dígitos>` y `COMPRA … EN <comercio>, <lugar>, TARJ. :*<6 dígitos>` | comercio (entre `EN` y la primera coma) y lugar |
| Compra en el extranjero | `COMPRA <comercio>, <lugar>, TARJETA <16 dígitos> , COMISION <n>,<nn>` | comercio, lugar y comisión |
| Bizum | `BIZUM … DE … CONCEPTO: <texto libre>` | solo el concepto libre; la contraparte es una persona |
| Transferencia recibida | `TRANSFERENCIA DE <ordenante>, CONCEPTO <texto>` | ordenante y concepto |

- Los gastos con tarjeta son aproximadamente tres cuartas partes de los gastos; el resto son formas poco repetidas.
- **Dato sensible en el texto**: algunos conceptos contienen un número de tarjeta de 16 dígitos sin enmascarar y otros 6 dígitos tras `:*`. Al importar a la base de datos hay que enmascararlos (conservar solo los 4 últimos) y no volcarlos nunca a logs.
- No hay identificador de movimiento: la idempotencia de la sincronización se basará en una clave compuesta (cuenta, fechas, importe, sentido, hash del texto) y en un contador de apariciones idénticas el mismo día.

## Pendiente de medir

- Profundidad del histórico en consultas posteriores sin usuario presente (puede bajar a unos 90 días).
- Frecuencia máxima de refresco sin usuario presente.
- Si existen movimientos pendientes (`PDNG`) con otro valor de `transaction_status`.
