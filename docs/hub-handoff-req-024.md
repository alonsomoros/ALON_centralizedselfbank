# Entrega para `alon-hub`: respuestas a REQ-024 (chat de consultas)

Documento para llevar a la sesión del repositorio `alon-hub`. Este repositorio (`selfbank`) **no modifica** el hub: aquí solo se describe lo que el hub puede esperar de la API y lo que habría que decidir allí. Estado a 16-nov-2026.

`REQ-024` ya existe en el hub como `Propuesta`. Se propone **reutilizarla** (WORKFLOW §2: una tarea que continúa trabajo existente reutiliza su nota) en lugar de abrir otra: este texto responde a sus cuatro decisiones abiertas y sustituye las fases 2 y 3.

## Respuestas a las decisiones abiertas de REQ-024

| Decisión abierta | Respuesta |
| :--- | :--- |
| ¿Cómo expone datos el proyecto de banca? | **API HTTP** (Python 3.13, solo biblioteca estándar) en la misma Pi. Escucha en `127.0.0.1:8789`, token Bearer, contrato en `docs/api-contrato.md` de este repo (rutas `health`, `status`, `suggestions`, `ask`, `feedback`). |
| ¿Qué consultas se hacen? | Dataset de 66 preguntas reales en `bench/consultas/questions.jsonl` (gasto por categoría y periodo, última transferencia, saldo, comparar meses, comercios principales, suscripciones). |
| ¿Modelo local o externo? | **Local** (`qwen2.5:3b` en Ollama, solo en `127.0.0.1`). Nada sale de la Pi. El modelo solo traduce la frase a una consulta de catálogo cerrado; las cifras salen de SQL y el texto de plantillas. El ~79 % de las preguntas se resuelve con reglas en ~10 ms; el resto tarda de 5 a 9 s. |
| ¿Servicio en la API del hub o aparte? | **Aparte** (otro proceso y otro repo). El hub solo hace de pasarela. |

Consecuencia: las fases 2 (comandos y atajos) y 3 (lenguaje natural) de REQ-024 **ya las cubre `selfbank`**. Al hub le queda la fase de interfaz y, después, la voz.

## Lo que ofrece la API (resumen; el contrato manda)

- `GET /api/suggestions`: chips de preguntas frecuentes (se resuelven al instante).
- `POST /api/ask {question}` → respuesta con texto, **interpretación visible**, estado (`answered`, `needs_clarification`, `unsupported`) y un `answer_id`.
- `POST /api/feedback {answer_id, verdict}`: botón «no era eso».
- `GET /api/status`: modelo, última sincronización, caducidad del consentimiento.
- Errores siempre como `{"error":{code,message}}`. Una pregunta no soportada responde «no puedo», nunca otra cifra.

## Puntos de diseño que el hub debe decidir

1. **Pasarela en `server/` del hub.** El hub prohíbe secretos en el bundle y en `localStorage`; el token de `selfbank` vive solo en el entorno de la Pi (variable del contenedor del hub, nunca en `.env.example` con valor). El navegador habla solo con el hub (`/api/selfbank/*`), así que no hace falta CORS nuevo ni abrir puertos en el tailnet.
2. **Alcance de red (a verificar en la Pi).** La API del hub corre en un contenedor (`ci-cd_default`) y `selfbank` escucha en el loopback **del host**: desde el contenedor `127.0.0.1` no lo alcanza. Opciones: (a) que `selfbank` escuche en la IP de la pasarela del bridge de Docker (alcanzable por contenedores, no por la LAN) y el contenedor use `host-gateway`; (b) meter `selfbank` en el mismo compose (Ollama seguiría en el host y habría que abrirlo al bridge). Recomiendo (a); hay que comprobarlo en la Pi antes de implementar. Ninguna opción debe publicar nada en `0.0.0.0` (REQ-023).
3. **Quién puede preguntar.** Hoy el perímetro del hub es el tailnet y la API no pide credenciales (REQ-018). Aquí se exponen datos bancarios. Decisión del usuario: aceptar el perímetro del tailnet para la v1 (documentado, con límite de peticiones y sin guardar contenido en logs) o añadir un segundo factor en la ruta del chat.
4. **Timeouts.** La regla de 1,5 s del hub no sirve para `ask`: el peor caso es ~95 s (cola 45 s + modelo 45 s + SQL 5 s), y el arranque en frío del modelo puede acercarse al minuto. Propuesta: pasarela con 100 s para `ask` (1,5 s para `status` y `suggestions`) y estado «pensando…» en la UI con segundos transcurridos. `selfbank` devuelve `model_busy` si el modelo está ocupado.
5. **Privacidad en la PWA.** Las respuestas llevan importes: el service worker **no debe cachear** `/api/selfbank/*`, y no se guardan preguntas ni respuestas en `localStorage`. El historial lo conserva `selfbank` (en la Pi, permisos 600).

## Criterios de aceptación propuestos

- [ ] Vista de chat con chips de `suggestions`, entrada de texto y respuesta con la interpretación visible.
- [ ] Botón «no era eso» que llama a `feedback` y se refleja en la UI.
- [ ] Estado «pensando…» sin bloquear el resto del hub; los errores y `unsupported` se muestran con el mensaje de la API.
- [ ] Sin token en bundle, `localStorage` ni cabeceras del navegador hacia el hub.
- [ ] `/api/selfbank/*` no se cachea en el service worker.
- [ ] Tests `node:test` de la pasarela con un servidor falso (nombres con el REQ): token añadido, timeout, errores propagados, nada sensible en logs.
- [ ] Comprobación en Chrome móvil: pregunta, respuesta y «no era eso» (paso reproducible en la nota).
- [ ] Mientras no se publique el servicio real, desarrollar contra el simulado (`https://raspberrypi.tail6f4cba.ts.net:8443`, mismo contrato, cifras falsas).

## Lo que queda por hacer en `selfbank` (no es trabajo del hub)

- Escuchar en la dirección elegida en el punto 2 y dejar el servicio como unidad `systemd` (hoy se lanza con un script).
- Sincronización periódica (4 al día) y aviso de caducidad del consentimiento a 14 días: implementada el 16-nov-2026, pendiente de desplegar (`docs/despliegue-sync.md`); sin ella, los datos son los del 15-nov-2026.
- Cifrado en reposo de la base.

## Orden sugerido

1. En el hub: actualizar `REQ-024` a `En curso` con estas decisiones (las del usuario sobre los puntos 2 y 3 primero).
2. Hub: pasarela con tests contra un servidor falso; después la vista de chat contra el simulado.
3. `selfbank`: dirección de escucha, `systemd` y sincronización.
4. Cambiar la pasarela del simulado al servicio real.
5. Voz (fase 4 de REQ-024), con Whisper local o Web Speech tras decidir privacidad.
