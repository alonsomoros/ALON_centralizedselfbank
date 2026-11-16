# AGENTS.md — selfbank

Guía para agentes de IA y desarrolladores de este repositorio. Sustituye al `AGENTS.md` de carpetas superiores, que pertenece a otro proyecto.

## Propósito

Consultar mis cuentas bancarias españolas en lenguaje natural mediante un chat web servido desde la Pi (WhatsApp aparcado). Todo gratuito y local en una Raspberry Pi 5 (8 GB). Uso personal, un único usuario.

## Principios

- **Coste cero**: sin tarjeta, sin APIs de pago, sin LLM en la nube.
- **Privacidad**: ningún dato bancario sale de la Pi. Único tráfico saliente: Enable Banking.
- **Red**: los servicios de selfbank escuchan solo en `127.0.0.1` y se publican con `tailscale serve` (solo tailnet). Nunca Funnel. La interfaz del chat la sirve la Pi, no Vercel, y exige credencial propia: estar en la tailnet no basta. No se añade nada bancario a la API del hub.
- **El LLM solo traduce** pregunta → JSON de consulta restringido por esquema. Nunca calcula ni ve la BD entera. Los números los calcula SQL.
- **Datos bancarios nunca** en logs, commits, issues ni fixtures. Usar datos sintéticos.
- **Secretos** (clave privada RS256, sesión de Baileys, `.env`) fuera de git, permisos 600.

## Stack

| Elemento | Decisión |
| :--- | :--- |
| Runtime | Python 3.13, solo biblioteca estándar (decisión del 15-nov-2026: la lógica de consulta ya existía y está probada en Python; cero dependencias de terceros) |
| Datos | SQLite (`sqlite3` de la biblioteca estándar) |
| Validación | `selfbank/schema.py` (JSON Schema acotado) |
| Pantalla del chat | React/TypeScript del usuario; solo habla con la API (`docs/api-contrato.md`) |
| LLM | Ollama (alternativa: llama-server con GBNF); modelo elegido por benchmark |
| Canal | Chat web servido por la Pi con `tailscale serve`; WhatsApp/Baileys aparcado, el núcleo mantiene una interfaz de canal |
| Banca | Enable Banking, modo restringido (uso personal) |
| Servicio | systemd en la Pi |

## Comandos

```bash
# tests (desde la raíz del repo)
python -m unittest discover -s tests -t .
python -m unittest discover -s bench/consultas
python -m unittest discover -s bench/ollama

# en la Pi (código en ~/selfbank-dev)
python3 -m selfbank.tools.ingest_cli --raw-dir ~/.selfbank/raw/prod --session ~/.selfbank/session-prod.json --db ~/.selfbank/selfbank.db
python3 -m selfbank.tools.categorize_wizard              # clasificar comercios (en tu terminal)
python3 -m selfbank.tools.system_check --db ~/.selfbank/selfbank.db --raw-dir ~/.selfbank/raw/prod [--llm]
bash ~/run-selfbank-real.sh                              # servicio real en 127.0.0.1:8789 (token en ~/.selfbank/api-token); en la Pi corre como selfbank-real.service
python3 -m selfbank.tools.sync_cli                       # sincroniza con el banco (lo lanza selfbank-sync.timer 4 veces al día; docs/despliegue-sync.md)

# desarrollo de la pantalla: servidor simulado sin datos reales
python -m selfbank.api.mock --port 8788 --token dev-token
```

## Mapa del repositorio

```
/AGENTS.md          este archivo
/CLAUDE.md          puntero a este archivo
/selfbank/          paquete del servicio (solo biblioteca estándar)
    contract.py     contrato de la consulta, periodos, valores por defecto
    rules.py        reglas deterministas (periodo, sentido, importes, categorías)
    router.py       ruta rápida: clasifica sin modelo cuando entiende todas las palabras
    pipeline.py     interpretación compartida: sanitize -> reglas -> validación -> fechas
    prompt.py llm.py  prompt y cliente de Ollama (solo local; los fallos se cuentan)
    db.py ingest.py   SQLite, importación idempotente y enmascarado de tarjetas
    categorize.py     tipo, comercio y categoría de cada movimiento (reglas propias en ~/.selfbank/categories.local.json)
    executor.py     SQL parametrizado de solo lectura: de la consulta a las cifras
    engine.py       motor real: pregunta -> respuesta (rutas rápida y del modelo, historial, estado)
    describe.py answers.py  interpretación visible y plantillas de respuesta (el modelo nunca redacta)
    schema.py text.py suggestions.py
    api/            servidor HTTP, esquemas del contrato, servicio real (serve) y simulado (mock)
    bank.py sync.py   cliente mínimo de Enable Banking y sincronización periódica (tools/sync_cli.py)
    tools/          ingest_cli, sync_cli, recategorize, categorize_wizard, system_check, oracle
    model.schema.json resolved.schema.json   lo que emite el modelo y la consulta completa
/tests/             pruebas del paquete, la API, el motor y las herramientas (datos sintéticos)
/bench/             dataset de preguntas, validador, runner y pruebas que dependen del dataset
/docs/              informes de Fase 0, decisiones, contrato de la API, datos y consultas y despliegue de la sincronización
/deploy/            unidades de systemd de la sincronización (service y timer)
```

Las carpetas se crean cuando se necesitan.

## Reglas de trabajo

- Plan de referencia: `C:\Users\Alonso\.claude\plans\pasted-content-id-4ae1-proyecto-selfban-abundant-stardust.md` (fases 0–7, métricas A–E).
- TDD para el ejecutor SQL, el validador y el router de reglas.
- Las respuestas se redactan con plantillas (`answers.py`); el modelo solo traduce la pregunta.
- Importes en céntimos enteros, nunca `float`. Las consultas SQL llevan siempre parámetros y usan una conexión de solo lectura.
- Nada de contenido de movimientos en logs, salidas de herramientas ni chats: las herramientas imprimen contadores y OK/FALLO.
- Cada respuesta de la API incluye la interpretación de la pregunta, para que un malentendido sea visible.
- Una consulta LLM a la vez; timeout duro de 45 s.
- No añadir dependencias sin justificarlas por coste, RAM o seguridad.
- Commits pequeños; no hacer commit sin que el usuario lo pida.
