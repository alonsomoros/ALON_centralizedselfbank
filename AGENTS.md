# AGENTS.md — selfbank

Guía para agentes de IA y desarrolladores de este repositorio. Sustituye al `AGENTS.md` de carpetas superiores, que pertenece a otro proyecto.

## Propósito

Consultar mis cuentas bancarias españolas por WhatsApp en lenguaje natural. Todo gratuito y local en una Raspberry Pi 5 (8 GB). Uso personal, un único usuario.

## Principios

- **Coste cero**: sin tarjeta, sin APIs de pago, sin LLM en la nube.
- **Privacidad**: ningún dato bancario sale de la Pi. Único tráfico saliente: WhatsApp y Enable Banking.
- **El LLM solo traduce** pregunta → JSON de consulta restringido por esquema. Nunca calcula ni ve la BD entera. Los números los calcula SQL.
- **Datos bancarios nunca** en logs, commits, issues ni fixtures. Usar datos sintéticos.
- **Secretos** (clave privada RS256, sesión de Baileys, `.env`) fuera de git, permisos 600.

## Stack

| Elemento | Decisión |
| :--- | :--- |
| Runtime | Node 22 LTS (arm64), TypeScript |
| Datos | SQLite (better-sqlite3 + Drizzle) |
| Validación | zod |
| LLM | Ollama (alternativa: llama-server con GBNF); modelo elegido por benchmark |
| WhatsApp | `baileys` oficial, versión exacta fijada, sin forks |
| Banca | Enable Banking, modo restringido (uso personal) |
| Servicio | systemd en la Pi |

Los comandos (instalar, test, lint, bench) se añaden aquí en el mismo commit que crea `package.json`.

## Mapa del repositorio

```
/AGENTS.md      este archivo
/CLAUDE.md      puntero a este archivo
/src/           bot, sync, consultas, capa NL→JSON
/bench/         dataset de preguntas, benchmark y muestreo de métricas
/docs/          informes de Fase 0 y decisiones
```

Las carpetas se crean cuando se necesitan.

## Reglas de trabajo

- Plan de referencia: `C:\Users\Alonso\.claude\plans\pasted-content-id-4ae1-proyecto-selfban-abundant-stardust.md` (fases 0–7, métricas A–E).
- TDD para el ejecutor SQL, el validador y el router de reglas.
- Una consulta LLM a la vez; timeout duro de 45 s.
- No añadir dependencias sin justificarlas por coste, RAM o seguridad.
- Commits pequeños; no hacer commit sin que el usuario lo pida.
