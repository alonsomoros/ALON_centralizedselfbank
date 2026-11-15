# Enable Banking — aplicación Sandbox (M2)

| Elemento | Valor |
| :--- | :--- |
| Entorno | Sandbox (bancos y datos ficticios) |
| Nombre de la aplicación | `selfbank-sandbox` |
| ID de la aplicación | `b239298e-83c6-4e7d-94f0-f8f6050da48c` (identificador público, no es secreto) |
| Clave privada | `C:\Users\Alonso\.selfbank\b239298e-83c6-4e7d-94f0-f8f6050da48c.pem`, fuera del repo, generada por el navegador, desechable |
| Redirect URL registrada | `http://localhost:8765/callback` (el formulario la aceptó) |
| Node en el PC | v22.17.1 |

La clave nunca se pega en conversaciones, commits ni logs. Si se pierde, se registra otra aplicación.

## Hallazgos del ensayo de autorización (M5a, BBVA simulado)

Flujo completo `POST /auth` → callback → `POST /sessions` → `GET balances` y `GET transactions` funcionando con `scripts en bench/enable-banking/py/`.

- `strategy=longest` fue aceptado por la API; no se sabe aún si en producción amplía el histórico.
- Sesión: `session_id`, `access.valid_until` (≈ 180 días) y una lista de cuentas con `uid`, `identification_hash`, `currency`, `name`, `product`, `cash_account_type` e IBAN en `account_id.iban`.
- Saldos: un solo saldo por cuenta, de tipo `CLBD` (contable al cierre). No hay `ITAV` (disponible).
- Movimientos: unas 20 por página, paginados con `continuation_key` (3 páginas para 43–60 movimientos).
- La contraparte está en `creditor.name` (cuando el movimiento es un gasto) o en `debtor.name` (cuando es un ingreso); el sentido lo da `credit_debit_indicator` (`CRDT`/`DBIT`).
- `transaction_id`, `merchant_category_code`, `bank_transaction_code`, `balance_after_transaction`, `note` y `transaction_date` llegan vacíos. Identifica un movimiento `entry_reference` (cadena) y no hay categoría del banco.
- `remittance_information` es una lista de cadenas, a veces vacía.
- Todos los movimientos del sandbox son `CRDT` y están `BOOK`: el sandbox no sirve para probar gastos ni movimientos pendientes.

## Implicaciones para el diseño

- Esta sección describe el sandbox, que difiere de Santander real (ver `docs/bancos.md`): el banco real no entrega contraparte.
- La categoría se calcula siempre en selfbank (reglas sobre contraparte y concepto).
- La idempotencia de la sincronización no puede depender de `transaction_id`; se decide con los datos reales de Santander.
- Los datos de prueba de selfbank deben incluir `DBIT` y movimientos `PDNG`, que el sandbox no ofrece.
