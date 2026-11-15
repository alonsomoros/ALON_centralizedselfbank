# Datos y consultas: de los movimientos en bruto a las cifras

Estado a 15-nov-2026. Cómo se guardan los movimientos, cómo se calculan las respuestas y cómo se comprueba que son correctas.

## Camino de los datos

```
Enable Banking ──> ~/.selfbank/raw/prod/*.json (en bruto, 600)
                        │  python -m selfbank.tools.ingest_cli
                        ▼
              enmascarar tarjetas ─> tipo/comercio/categoría ─> deduplicar ─> ~/.selfbank/selfbank.db (600)
                                                                                   │ solo lectura
pregunta ─> reglas / modelo ─> consulta interpretada ─> ejecutor SQL ────────────────┘
                                                        └─> plantillas ─> respuesta + interpretación visible
```

## Base de datos (`selfbank/db.py`)

| Tabla | Contenido |
| :--- | :--- |
| `accounts` | Cuenta (clave estable = `identification_hash` de Enable Banking, no el `uid`, que cambia con cada sesión) |
| `transactions` | Movimientos: fecha, importe en **céntimos enteros positivos**, dirección (`out`/`in`), texto **ya enmascarado**, tipo, comercio o contraparte, categoría |
| `balances` | Último saldo por tipo (`CLBD` cierre, `OPBD` apertura) |
| `meta` | Última sincronización y caducidad del consentimiento |
| `history` | Cada pregunta hecha, su interpretación de 15 campos (sin resultados) y el veredicto del usuario |

- El fichero se crea con permisos 600. El ejecutor abre la base **solo en lectura** (`mode=ro`): ni un fallo ni una inyección podrían modificarla.
- Los importes nunca son `float`: céntimos enteros (`0.10 + 0.20 = 30`, no `0.30000000000000004`).

## Importación (`selfbank/ingest.py`)

- **Enmascarado antes de guardar y antes de calcular nada**: 9 o más dígitos seguidos y los dígitos tras `*` conservan solo los 4 últimos. La base no contiene números de tarjeta (lo comprueba `system_check` cada vez).
- **Idempotente**: la clave de deduplicación es compuesta (cuenta, fechas, importe, sentido, texto enmascarado) más un contador de apariciones idénticas. Dos compras iguales el mismo día se conservan; reimportar lo mismo no añade nada; una ventana más corta no borra nada.
- Solo `BOOK`. Un movimiento defectuoso se rechaza y se cuenta; nunca rompe la importación.
- No imprime ni registra contenido, solo contadores.

## Categorías (`selfbank/categorize.py`)

1. El texto del banco se interpreta con las formas conocidas de Santander (compra con tarjeta, compra en el extranjero, Bizum enviado/recibido, transferencia, devolución, ingreso, comisión).
2. La categoría sale de **palabras clave sobre el comercio**. Las de por defecto son genéricas; **tu fichero `~/.selfbank/categories.local.json` manda sobre ellas**.
3. Tras importar datos reales, la cobertura inicial fue del **35 %** de las compras: mis listas no conocen tus comercios. Para subirla:

```bash
cd ~/selfbank-dev
python3 -m selfbank.tools.categorize_wizard          # en TU terminal: te enseña los comercios sin clasificar y eliges con un número
python3 -m selfbank.tools.recategorize               # si editas categories.local.json a mano
```

## Cómo se calculan las cifras (`selfbank/executor.py`)

| Concepto | Regla |
| :--- | :--- |
| Gasto / ingreso | `out` e `in` se suman por separado y en positivo |
| «Ambos» (`any`) | Neto: ingresos − gastos |
| Devoluciones | Son ingresos del banco: no se restan del gasto (así cuadra con los totales brutos) |
| Listados | Importes con signo (negativo = gasto); por defecto los 50 más recientes (máximo 200) |
| Texto buscado | Sin tildes ni mayúsculas, plurales sencillos y coincidencia aproximada con comercios existentes («burguer king» ≈ «burger king»); `a\|b` = cualquiera de los dos |
| Ranking de comercios | Excluye a las personas (Bizum y transferencias) salvo que se pida Bizum, o ingresos |
| Recurrentes | Al menos 3 cargos, a intervalos semanales, mensuales, trimestrales o anuales, de importe estable (±15 %) y aún activos |
| Seguridad | Parámetros siempre `?`; los fragmentos de SQL salen de listas cerradas; abortan las consultas de más de 5 s |

## Cómo se comprueba (pruebas de sistema y de completitud)

```bash
python -m unittest discover -s tests -t .        # 220+ pruebas con datos sintéticos
python -m selfbank.tools.system_check --db ~/.selfbank/selfbank.db --raw-dir ~/.selfbank/raw/prod [--llm]
```
`system_check` imprime solo OK/FALLO, contadores y porcentajes (nunca movimientos ni importes):

1. **Integridad**: mismo número de movimientos que el bruto, y sumas de gastos e ingresos iguales **al céntimo**; reimportar no añade nada.
2. **Extracción**: % de compras con comercio, Bizum y transferencias con contraparte, distribución por categoría.
3. **Completitud**: las 66 preguntas del dataset de punta a punta, sin excepciones, con latencias por fuente.
4. **Contraste**: 300 consultas aleatorias, SQL frente a un cálculo independiente en Python puro sobre el JSON bruto.
5. **Seguridad**: sin números de tarjeta en la base, permisos 600, conexión de consulta de solo lectura, historial sin resultados.

Con opciones explícitas (para mirar **tú**, en tu terminal; no las pegues en chats): `--show-monthly N` imprime gasto e ingreso por mes para compararlos con la app del banco, y `--uncategorized N` los comercios sin clasificar.

### Resultado sobre los datos reales (15-nov-2026)

| Comprobación | Resultado |
| :--- | :--- |
| Movimientos | 766 importados de 766; sumas de gastos e ingresos iguales al céntimo |
| Idempotencia | Reimportar no añade nada; importar desde cero da el mismo total |
| Contraste SQL / independiente | 299 de 299 coinciden |
| Preguntas del dataset con el modelo real | 64/66 con la interpretación esperada (cota optimista); reglas: mediana 10 ms; modelo: mediana 5,1 s, p95 9,1 s |
| Seguridad | Sin números de tarjeta, permisos 600, conexión de solo lectura |

### Errores que encontró este proceso (y quedaron cubiertos por tests)

| Error | Cómo se vio | Arreglo |
| :--- | :--- | :--- |
| 72 compras en el extranjero clasificadas como «comisiones» (el texto lleva la comisión de cambio dentro) | Un 13 % de gastos en una categoría rara | La categoría de una compra ignora el tramo `, TARJETA …, COMISION …` |
| Las reglas propias del usuario no ganaban a las de por defecto entre categorías | Un test | Ahora se evalúan primero |
| Ollama rechazaba `keep_alive: "-1"` (texto) con HTTP 400 y el cliente lo tragaba, así que las preguntas del modelo daban «no te he entendido» | Resultado idéntico al de sin modelo y 10 ms de latencia en una pregunta «por el modelo» | Se envía el número `-1`; los fallos del modelo se cuentan y la prueba de sistema los marca como FALLO |
| El plazo de 45 s no cubre el arranque en frío (~1 min) | Análisis previo | `warm_up` con plazo de 240 s al arrancar el servicio |
| La prueba de sistema contaminaba el historial real | Revisión de diseño | Trabaja sobre una copia temporal |

## Límites conocidos

- La cobertura de categorías depende de tu fichero local; hasta clasificar tus comercios, «gasto en restaurantes» subestima.
- Pendientes (`PDNG`) y el límite del histórico a ~90 días en refrescos sin usuario presente siguen sin medirse.
- Las capacidades nuevas (media mensual, duplicados, fines de semana, subidas de suscripciones) están en `docs/backlog-consultas.md`.
- El texto de las preguntas se guarda en `history` (local, permisos 600) para poder medir la precisión real; no contiene importes.
