# Línea base de la Pi (M1)

Medida en reposo, antes de instalar nada de selfbank. Sirve de punto de partida para comparar los benchmarks.

| Elemento | Valor | Lectura |
| :--- | :--- | :--- |
| Arquitectura | `aarch64` | 64 bits, compatible con Node 22 y Ollama |
| SO | Debian 13 (trixie) | no es Raspberry Pi OS; `vcgencmd` funciona igualmente |
| RAM total | 7,9 GiB | |
| RAM usada | 2,4 GiB | |
| RAM disponible | 5,5 GiB | presupuesto real para selfbank (la caché de 4,3 GiB es recuperable) |
| Swap | 2,0 GiB, 214 MiB ya en uso | ver nota: el criterio es que no crezca |
| Disco raíz | `/dev/mmcblk0p2`, 115 GB, 34 % usado, 73 GB libres | **es la tarjeta SD**, no un SSD |
| Temperatura en reposo | 48,3 °C | margen amplio hasta ~75 °C |
| `get_throttled` | `0x0` | sin subtensión ni throttling desde el arranque |

## Puertos ya ocupados (TCP, escuchando)

22, 53, 80, 111, 443, 631 (solo local), 3000, 3001, 3002, 8080, 8181, 8787, 9000, 9090, 9100, 51821, más dos puertos efímeros de Tailscale.

- **`8765` está libre**: sirve como puerto del callback (`http://localhost:8765/callback`).
- **`8080` está ocupado**: otra razón para no usarlo.
- Tailscale ya está instalado y activo en la Pi.

## Consecuencias para el plan

1. **Swap**: el criterio "swap = 0" pasa a "el swap usado no crece durante el benchmark" (línea base: 214 MiB).
2. **Disco SD**: la carga del modelo (arranque en frío) será más lenta que con SSD, y las escrituras frecuentes desgastan la tarjeta. Medir `load_duration` tal cual y mantener logs y SQLite con pocas escrituras. Migrar a SSD queda como mejora posterior, no bloquea la Fase 0.
3. **Carga previa**: la Pi ya ejecuta otros servicios. Los benchmarks se hacen con esos servicios activos, que es el escenario real.
4. **Tailscale ya disponible**: facilita el chat web (M10) y, si hiciera falta, una redirect URL HTTPS.
