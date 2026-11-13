# Fase 0 — Validación antes de construir

Objetivo: comprobar con datos reales que el proyecto es viable en tu Pi 5 y con tus bancos, antes de escribir la aplicación. Nada de esta fase queda en producción: es medir y decidir.

**Criterio de paso**: intent correcto ≥ 90 %, latencia p95 < 30 s, sin throttling térmico, swap usado sin crecer durante el benchmark (línea base en `docs/baseline-pi.md`), > 1 GB de RAM disponible, Baileys estable 48 h, al menos un banco tuyo accesible con histórico útil.

**Cómo trabajamos**: cada bloque se explica antes de tocar nada. Tú das el visto bueno y, cuando el paso lo haga otra persona (tú en tu navegador o en la Pi), te dejo la instrucción exacta. No se ejecuta nada técnico sin que hayas entendido qué hace.

## Mapa de bloques

| Bloque | Qué responde | Dónde | Quién actúa |
| :--- | :--- | :--- | :--- |
| 0.A Enable Banking (sandbox) | ¿Entiendo y puedo hacer el flujo de autorización? ¿Admite `localhost`? | Navegador + PC | Tú (portal), yo (script) |
| 0.B Cobertura de bancos | ¿Cubre mis bancos españoles y cuánto histórico? | API | Yo, tras 0.A |
| 0.C LLM en la Pi | ¿Cabe y es lo bastante rápido y preciso? | Pi | Tú (instalar), yo (scripts) |
| 0.D Dataset de preguntas | ¿Cómo medimos la precisión? | Repo | Yo, tú lo revisas |
| 0.E Chat web mínimo | ¿Puedo preguntar desde el móvil, solo por la tailnet y con credencial propia? | Pi | Tú (probar), yo (código) |
| 0.F Informe | ¿Pasamos o recortamos ambición? | `docs/` | Yo, con tus mediciones |

Orden recomendado: **0.A → 0.B** (decide si el proyecto es posible), en paralelo **0.D** (no depende de nada), después **0.C** y **0.E** en la Pi.

---

## 0.A Enable Banking: conceptos que debes entender primero

Enable Banking es el intermediario PSD2: tu aplicación no habla con el banco, habla con Enable Banking, y esta con el banco.

### Piezas
- **Aplicación (application)**: lo que registras en el Control Panel. Tiene un ID (UUID), un nombre y una lista de redirect URLs permitidas. Hay dos entornos distintos y separados:
  - **Sandbox**: bancos de mentira con datos ficticios. Sirve para aprender el flujo sin riesgo y sin tus datos.
  - **Producción en modo restringido**: bancos reales, solo tus propias cuentas vinculadas. Es el modo gratuito de uso personal.
  Son dos aplicaciones distintas con IDs y claves distintas. Empezamos siempre por sandbox.
- **Par de claves**: al registrar la aplicación, el navegador genera una clave privada (archivo `.pem`, nombrado con el ID de la aplicación) y envía solo la pública. La privada **no se transmite a Enable Banking** y no se puede recuperar: si la pierdes, hay que registrar otra aplicación.
- **JWT RS256**: cada petición a la API lleva una cabecera `Authorization: Bearer <JWT>`. El JWT lo firmas tú con la clave privada (header `kid` = ID de la aplicación; `iss` = `enablebanking.com`; `aud` = `api.enablebanking.com`; caducidad de hasta 1 h). Así Enable Banking sabe que la petición viene de tu aplicación sin enviar nunca la clave.

### Flujo de autorización (una vez por banco, cada ≤ 180 días)
1. `GET /aspsps?country=ES` → lista de bancos disponibles (un banco se llama ASPSP) con su validez máxima de consentimiento.
2. `POST /auth` con banco, país, `valid_until`, un `state` y tu `redirect_url` → devuelve una **URL de autorización**.
3. **Tú abres esa URL en tu navegador**, entras en tu banco (con tu app o credenciales del banco) y aceptas compartir tus datos.
4. El banco te devuelve, a través de Enable Banking, a tu **redirect URL** con un parámetro `code` en la dirección.
5. `POST /sessions` con ese `code` → devuelve la sesión y las cuentas autorizadas (cada una con un `uid`).
6. Con el `uid`: `GET /accounts/{uid}/balances` y `GET /accounts/{uid}/transactions` (paginado con `continuation_key`).

### Qué es la redirect URL y por qué importa `localhost`
La redirect URL **no la visita Enable Banking ni el banco: la abre tu navegador** al terminar el paso 3. Por eso:
- Debe ser una dirección que **tu navegador** pueda alcanzar, aunque nadie más pueda.
- Si tu aplicación corre en la misma máquina que tu navegador, `http://localhost:3000/callback` funciona: el navegador va a su propio equipo y tu script, escuchando ahí, lee el `code`.
- Tu Pi no tiene pantalla ni navegador. Si el script corre en la Pi y el navegador está en tu PC, `localhost` apuntaría a tu PC, no a la Pi. Tres soluciones:
  1. **Hacer la autorización desde tu PC** (script en tu PC con `localhost`). Es lo más simple porque la autorización es puntual. La sesión resultante (identificador y cuentas) se copia a la Pi; la sincronización posterior ya no necesita navegador.
  2. **Túnel SSH** `ssh -L 3000:localhost:3000 pi`: tu `localhost:3000` se reenvía a la Pi.
  3. **Tailscale**: la Pi tiene una dirección privada alcanzable desde tu PC.
- **Duda abierta**: la documentación no dice si Enable Banking acepta `http://localhost` en la lista de redirect URLs. Lo comprobaremos registrando la aplicación de sandbox con `http://localhost:3000/callback`: si el formulario lo rechaza, lo sabremos al instante y sin consecuencias. Si lo rechaza, escribimos a soporte o usamos Tailscale con HTTPS.

### Sandbox: qué esperar
Tras elegir un banco de prueba verás un banco simulado con usuarios ficticios. No es tu dinero ni tu banco. Sirve para: probar el JWT, probar el flujo completo, probar paginación y fijar el modelo de datos. **No sirve** para medir cobertura de bancos reales ni histórico real (eso es 0.B).

### Qué haremos y qué necesito de ti
1. **Tú**: crear cuenta en enablebanking.com (correo con enlace de un solo uso).
2. **Tú**: en *API applications*, registrar una aplicación **Sandbox** con nombre `selfbank-sandbox`, clave generada por el navegador y redirect URL `http://localhost:3000/callback`. Anotar si el formulario la acepta.
3. **Tú**: guardar el `.pem` **fuera del repo** (por ejemplo `C:\Users\Alonso\.selfbank\`). El `.gitignore` del repo ya excluirá `*.pem`, pero la regla es no guardarlo dentro. **No me lo pegues ni me lo muestres**: solo dime la ruta y el ID de aplicación (el ID no es secreto).
4. **Yo** (tras tu OK): un script mínimo que firme el JWT, liste los bancos de ES y haga el flujo hasta ver las cuentas del sandbox. Te explico cada línea antes de ejecutarlo.

Decisión tuya antes de empezar 0.A: ¿la autorización se hará desde tu PC (opción 1, recomendada) o directamente desde la Pi?

---

## 0.B Cobertura de bancos reales
`GET /aspsps?country=ES` no requiere cuentas tuyas: lista los bancos españoles, su `maximum_consent_validity` y qué datos ofrecen. Con eso se responde si tus bancos están. La profundidad real de histórico solo se ve con tu cuenta real en producción restringida (0.B2, **opcional en Fase 0**, requiere tu OK explícito porque toca datos reales; no se guarda nada de ellos en el repo).

**Necesito de ti**: la lista de tus bancos.

## 0.C LLM en la Pi (resumen; se detalla antes de ejecutar)
- Instalar Ollama y descargar `qwen2.5:3b` y `qwen2.5:1.5b` (más candidatos opcionales).
- `llama-bench` para medir velocidad de lectura del prompt (prefill) y de generación.
- Ejecutar el dataset de 0.D con y sin salida forzada por esquema (`format`), 3 repeticiones, mientras un muestreador registra RAM, CPU, temperatura y throttling cada segundo.
- Antes de nada, comprobar: refrigeración activa, fuente oficial, SSD, `vcgencmd get_throttled`, RAM libre actual.

## 0.D Dataset de preguntas (puedo empezarlo sin tocar nada técnico)
~40 preguntas en español con su JSON esperado: cantidades, listas, saldos, comparaciones, comercios, recurrentes, fechas relativas y ambiguas. Datos **sintéticos**. Tú revisas, y añades 10–15 preguntas tuyas reales sin cifras sensibles.

## 0.E Chat web mínimo
- Servicio nuevo, separado de la API del hub, escuchando solo en `127.0.0.1` y publicado con `tailscale serve` en un puerto libre (por ejemplo 8443). Sin Funnel.
- Sirve su propia página: el código que toca datos bancarios no viene de Vercel. El hub solo enlaza a ella.
- Credencial propia (token); estar en la tailnet no basta.
- En Fase 0 solo repite lo que escribes. Mide: acceso desde el móvil, rechazo sin credencial, reinicio de la Pi sin intervención.
- WhatsApp/Baileys queda aparcado; el núcleo mantiene una interfaz de canal por si se retoma.

## 0.F Informe
`docs/FASE0_INFORME.md` con las tablas de métricas rellenas, el modelo elegido y la decisión: pasamos, o bajamos ambición (más reglas, LLM solo de respaldo).

---

## Estado
- [x] `AGENTS.md` propio creado
- [x] 0.A cuenta y aplicación sandbox (M2, M3)
- [x] 0.B lista de bancos y cobertura (M4: Santander cubierto)
- [x] Aplicación de producción registrada y activada con una cuenta vinculada
- [ ] 0.A2 ensayo del flujo de autorización en sandbox (M5a)
- [ ] 0.A3 autorización real de Santander e importación en bruto (M5b)
- [ ] 0.D dataset de preguntas (M6)
- [ ] 0.C benchmark en la Pi (M7–M9)
- [ ] Contrato de la API del chat y servidor simulado (M10a)
- [ ] 0.E chat web mínimo (M10b)
- [ ] 0.F informe (M12)
