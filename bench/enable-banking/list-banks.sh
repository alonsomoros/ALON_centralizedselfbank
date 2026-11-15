#!/usr/bin/env bash
# Lista los bancos de un país con una aplicación de Enable Banking.
# Firma el JWT con openssl: la clave privada se lee del disco y nunca se imprime.
#
# Uso:  EB_APP_ID=<uuid> ./list-banks.sh [PAIS]
# Opcional: EB_KEY_PATH (por defecto ~/.selfbank/selfbank-prod.pem)
set -euo pipefail

APP_ID="${EB_APP_ID:?Falta EB_APP_ID (ID de la aplicación)}"
KEY="${EB_KEY_PATH:-$HOME/.selfbank/selfbank-prod.pem}"
COUNTRY="${1:-ES}"
OUT="${EB_OUT:-$HOME/.selfbank/aspsps-$COUNTRY.json}"

[ -r "$KEY" ] || { echo "No se puede leer la clave en $KEY" >&2; exit 1; }

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

iat=$(date +%s)
exp=$((iat + 3600))
header=$(printf '{"typ":"JWT","alg":"RS256","kid":"%s"}' "$APP_ID" | b64url)
payload=$(printf '{"iss":"enablebanking.com","aud":"api.enablebanking.com","iat":%s,"exp":%s}' "$iat" "$exp" | b64url)
signature=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$KEY" | b64url)
jwt="$header.$payload.$signature"

echo "JWT cabecera: $(printf '%s' "$header" | tr '_-' '/+' | base64 -d 2>/dev/null || true)"
echo "Hora local  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

response=$(curl -sS -w '\n%{http_code}' -H "Authorization: Bearer $jwt" \
  "https://api.enablebanking.com/aspsps?country=$COUNTRY")
status=${response##*$'\n'}
body=${response%$'\n'*}

if [ "$status" != "200" ]; then
  echo "ERROR HTTP $status: ${body:0:500}" >&2
  exit 1
fi

umask 077
printf '%s' "$body" > "$OUT"
echo "Respuesta guardada en $OUT"

python3 - "$OUT" <<'PY'
import json, sys
bancos = json.load(open(sys.argv[1]))["aspsps"]
print(f"\n{len(bancos)} bancos\n")
for b in sorted(bancos, key=lambda x: x["name"].lower()):
    dias = round(b["maximum_consent_validity"] / 86400) if isinstance(b.get("maximum_consent_validity"), (int, float)) else "?"
    psu = ",".join(b.get("psu_types", []))
    print(f"{b['name']:<40} {psu:<20} {dias} d")
PY
