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

## Pendiente de medir

- Profundidad del histórico en consultas posteriores sin usuario presente (puede bajar a unos 90 días).
- Frecuencia máxima de refresco sin usuario presente.
- Si existen movimientos pendientes (`PDNG`) con otro valor de `transaction_status`.
