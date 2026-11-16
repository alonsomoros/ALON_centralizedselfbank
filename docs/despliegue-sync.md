# Sincronización periódica con el banco

Estado a 16-nov-2026: implementada y probada con datos sintéticos (`tests/test_sync.py`); **sin desplegar**. Hasta desplegarla, los datos son los de la última importación manual.

## Qué hace

`python3 -m selfbank.tools.sync_cli` pide a Enable Banking los movimientos y saldos de cada cuenta de `~/.selfbank/session-prod.json` y los guarda en `~/.selfbank/selfbank.db`. Un temporizador de systemd lo lanza 4 veces al día (02:20, 08:20, 14:20 y 20:20, con hasta 10 min de retraso aleatorio; si la Pi estaba apagada, sincroniza al arrancar).

- **Primera vez** (la cuenta no tiene movimientos): todo el histórico disponible (`strategy=longest`).
- **Después**: desde el último movimiento guardado menos 5 días (un movimiento pendiente pasa a contabilizado más tarde). La importación es idempotente: solapar no duplica.
- **De banco a SQLite**: no deja ficheros en bruto en `~/.selfbank/raw/`.
- **Clave de cuenta**: `identification_hash` (el `uid` cambia en cada autorización; una cuenta sin `identification_hash` se omite y se avisa).
- **Una cuenta que falla** no bloquea a las demás; el resultado es «parcial».
- **Salida**: solo contadores (`status=ok inserted=3 already_present=40 …`); nunca movimientos ni importes.

## Consentimiento

El consentimiento caduca (máximo 180 días). Con **14 días o menos** la sincronización sigue pero imprime `AVISO: consent_expiring`; `GET /api/status` ya muestra `consent.days_left`. **Caducado**: no hace ninguna petición y sale con código 3; hay que volver a autorizar (`bench/enable-banking/py/auth_start.py` y `auth_finish.py`, desde el PC: cada autorización nueva invalida la anterior).

## Códigos de salida

| Código | Significado | Qué hacer |
| :--- | :--- | :--- |
| 0 | Bien | — |
| 1 | Fallo total o parcial (red, HTTP, tope de páginas) | `journalctl -u selfbank-sync -n 30 --no-pager`; el siguiente ciclo lo reintenta |
| 2 | Falta `session-prod.json` o `selfbank-prod.pem` | Autorizar / revisar rutas |
| 3 | Consentimiento caducado | Volver a autorizar |

## Instalación en la Pi

El código llega a la Pi con Git (el repositorio es público: se clona por HTTPS sin credenciales). Hasta el 16-nov-2026 `~/selfbank-dev` era una copia sin Git; se convierte en un clon **una sola vez**.

### Una vez: convertir `~/selfbank-dev` en un clon (en la Pi, por SSH)

Antes, desde el PC, el código nuevo debe estar subido (`git push`).

```bash
cd ~
git clone https://github.com/alonsomoros/ALON_centralizedselfbank selfbank-git
diff -rq --exclude=__pycache__ --exclude=out selfbank-dev selfbank-git   # solo debe haber archivos nuevos en selfbank-git ("Only in selfbank-git")
sudo systemctl stop selfbank-real                                         # el chat deja de responder unos segundos
mv selfbank-dev selfbank-dev.bak && mv selfbank-git selfbank-dev
sudo systemctl start selfbank-real
curl -s http://127.0.0.1:8789/api/health                                  # {"ok": true}
```

Si el `diff` muestra archivos que solo existen en `selfbank-dev` (`Only in selfbank-dev`), no continúes: revísalos. La copia antigua queda en `~/selfbank-dev.bak`; bórrala cuando todo funcione unos días.

### Cada actualización posterior (en la Pi)

```bash
cd ~/selfbank-dev && git pull --ff-only
sudo systemctl restart selfbank-real      # solo si cambió el servicio o la API; el temporizador usa el código nuevo en cada ejecución
```

### Primera ejecución y temporizador (en la Pi)

**Primero una ejecución a mano** (hace una petición real al banco; el límite de PSD2 sin el usuario delante es de 4 accesos al día, así que no la repitas):

```bash
cd ~/selfbank-dev
python3 -B -m selfbank.tools.sync_cli
# Esperado: status=ok inserted=<n> already_present=<n> ... y "consentimiento: quedan <n> días"
```

Después, el temporizador:

```bash
sudo cp ~/selfbank-dev/deploy/selfbank-sync.service ~/selfbank-dev/deploy/selfbank-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now selfbank-sync.timer
systemctl list-timers selfbank-sync.timer --no-pager     # muestra la próxima ejecución
```

Comprobar tras la primera ejecución programada: `journalctl -u selfbank-sync -n 20 --no-pager` y, en el chat, que «Datos hasta» avanza. Para lanzarlo a mano con el temporizador instalado: `sudo systemctl start selfbank-sync.service`.

Marcha atrás: `sudo systemctl disable --now selfbank-sync.timer`.

## Pendiente

- Aviso activo al llegar a 14 días (hoy solo se ve en el diario y en `GET /api/status`): por ejemplo un aviso desde n8n.
- Cifrado en reposo de la base (ver `docs/hub-handoff-req-024.md`).
