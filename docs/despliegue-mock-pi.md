# Servidor simulado en la Pi (prueba de conectividad, M10)

Estado a 15-nov-2026. Es una **prueba**: sirve cifras inventadas, no hay datos bancarios. Todavía no es un servicio de systemd.

## Qué hay desplegado

| Elemento | Dónde |
| :--- | :--- |
| Código | `~/selfbank-dev/` (copia del repo: `selfbank/`, `tests/`, `bench/`) |
| Arranque / reinicio | `bash ~/run-selfbank-mock.sh` |
| Proceso | `python3 -m selfbank.api.mock --port 8788 --token-file ~/.selfbank/mock-api-token` en `127.0.0.1:8788` |
| Token | `~/.selfbank/mock-api-token` (permisos 600, 64 caracteres aleatorios; no está en git ni en la línea de comandos) |
| Publicación | `tailscale serve --bg --https=8443 http://127.0.0.1:8788` → `https://raspberrypi.tail6f4cba.ts.net:8443` |
| Log | `/tmp/selfbank-mock.log` (solo líneas de petición; nunca el texto de las preguntas) |

El puerto 443 de `tailscale serve` (hub, `127.0.0.1:8787`) no se ha tocado.

## Qué se comprobó

- HTTPS con certificado válido desde el PC por Tailscale; 401 sin token o con token incorrecto; 200 con el token real.
- Reglas: 9 ms en el servidor (49 ms con la red). Camino del «modelo» simulado: 3,0 s.
- El 8443 escucha solo en la interfaz `tailscale0`; el 8788, solo en `127.0.0.1`.
- Desde la LAN, ni el 8788 ni el 8443 responden. Sin Funnel (`tailnet only`).

## Probarlo desde tu PC

```bash
TOKEN=$(ssh alonso@raspberrypi.local 'cat ~/.selfbank/mock-api-token')
curl -s https://raspberrypi.tail6f4cba.ts.net:8443/api/health
curl -s -H "Authorization: Bearer $TOKEN" https://raspberrypi.tail6f4cba.ts.net:8443/api/status
```
Nunca pegues el token en chats, issues ni commits. Un navegador no puede enviar la cabecera `Authorization` al abrir la URL: la pantalla del chat (fetch con la cabecera) es quien la envía.

## Actualizar el código

```bash
# desde la raíz del repo, en el PC
tar --exclude=__pycache__ -cf - selfbank tests bench | ssh alonso@raspberrypi.local 'tar -xf - -C ~/selfbank-dev'
ssh alonso@raspberrypi.local 'bash ~/run-selfbank-mock.sh'
```

## Deshacerlo

```bash
ssh alonso@raspberrypi.local '
  tailscale serve --https=8443 off          # deja de publicar el 8443
  kill "$(cat /tmp/selfbank-mock.pid)"      # para el simulado
  rm ~/.selfbank/mock-api-token             # borra el token (se regenera al volver a desplegar)
'
```
`tailscale serve --bg` **persiste tras un reinicio**: si la Pi se reinicia, el 8443 seguirá publicado pero el simulado no arrancará solo (devolvería error de pasarela). Es inofensivo, pero conviene desactivarlo si no se va a usar.

## Pendiente para el servicio real

- Unidad systemd (usuario sin privilegios, reinicio automático, `ProtectSystem`, sin acceso a `~/.selfbank` salvo lo necesario).
- Sustituir el simulado por el motor real (SQLite + ejecutor + Ollama).
- Servir también la pantalla del chat desde la misma Pi y el mismo origen.
