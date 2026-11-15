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
| Bizum recibido | `BIZUM DE <nombre y apellidos> CONCEPTO <texto libre>` | **el nombre de quien envía** (sí viene en el texto) y el concepto |
| Bizum enviado | `BIZUM A <nombre> DE <titular> CONCEPTO: <texto libre>` | el nombre del destinatario (entre `A` y `DE`) y el concepto |
| Transferencia recibida | `TRANSFERENCIA DE <ordenante>, CONCEPTO <texto>` | ordenante y concepto |

- Los gastos con tarjeta son aproximadamente tres cuartas partes de los gastos; el resto son formas poco repetidas.
- **Dato sensible en el texto**: algunos conceptos contienen un número de tarjeta de 16 dígitos sin enmascarar y otros 6 dígitos tras `:*`. Al importar a la base de datos hay que enmascararlos (conservar solo los 4 últimos) y no volcarlos nunca a logs.
- No hay identificador de movimiento: la idempotencia de la sincronización se basará en una clave compuesta (cuenta, fechas, importe, sentido, hash del texto) y en un contador de apariciones idénticas el mismo día.

## Hallazgos al importar a SQLite (15-nov-2026, 766 movimientos reales)

| Hallazgo | Consecuencia |
| :--- | :--- |
| Bizum: el nombre de la persona **sí viene en el texto** (corrige lo anotado en M5c: se creía que solo había concepto) | Se extrae como contraparte (254/254): son posibles «Bizums que le envié a X» |
| Transferencias: ordenante o destinatario en el texto (25/25) | Mismo tratamiento |
| `entry_reference` es único (764/764) pero parece `fecha.secuencia` y 2 movimientos no lo traen | Solo se guarda, no se usa como clave |
| 13 grupos de movimientos idénticos el mismo día (32 movimientos) | El contador de apariciones es imprescindible: sin él se perderían 19 |
| 124 textos con tarjeta de 16 dígitos y 360 con `*` + 6 dígitos | Se enmascaran al importar (quedan los 4 últimos) |
| Las **compras en el extranjero** (113) llevan al final `, TARJETA <16 dígitos> , COMISION n,nn`: es la comisión de cambio de divisa dentro del texto de la compra | No es una comisión bancaria aparte; ese tramo no se usa para categorizar (un primer intento clasificó 72 compras como «comisiones» por esto) |
| Prefijos del texto: `PAGO` 292, `BIZUM` 254, `COMPRA` 167, `TRANSFERENCIA` 25, `ANUL` 11, `INGRESO` 9, `DEVOLUCION` 3, `RETROCESIÓN` 2 | Tipos: compra, bizum, transferencia, devolución (17), ingreso |
| Importes siempre positivos; el sentido va en `credit_debit_indicator` (551 `DBIT`, 215 `CRDT`) | Céntimos enteros positivos + dirección |
| Todo es `BOOK` y en EUR | Los pendientes (`PDNG`) se ignoran por ahora |

## Pendiente de medir

- Profundidad del histórico en consultas posteriores sin usuario presente (puede bajar a unos 90 días).
- Frecuencia máxima de refresco sin usuario presente.
- Si existen movimientos pendientes (`PDNG`) con otro valor de `transaction_status`.
