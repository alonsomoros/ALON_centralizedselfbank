# Enable Banking — aplicación de producción (B2)

| Elemento | Valor |
| :--- | :--- |
| Entorno | Producción, modo restringido (uso personal) |
| Nombre de la aplicación | `selfbank` |
| ID de la aplicación | `6bab54bb-4a48-491f-b026-0d82661114c7` (identificador público, no es secreto) |
| Estado | Inactiva hasta vincular una cuenta |
| Redirect URL | `https://localhost:8765/callback` (producción solo acepta `https`; `http` da "unsupported scheme") |
| Clave privada | en la Pi, `~/.selfbank/selfbank-prod.pem`, permisos 600, nunca sale de ella |
| Certificado | autofirmado, generado en la Pi a partir de esa clave. El formulario pide certificado X.509 en PEM, no la clave pública suelta |

## Autorización con la redirect URL

Con `https://localhost:8765/callback` no hay servidor escuchando: el navegador mostrará un error de conexión, pero la dirección con `?code=…` queda visible en la barra. Se copia esa URL completa y se pasa al script de autorización. El `code` es de un solo uso y no se pega en chats ni en logs.
