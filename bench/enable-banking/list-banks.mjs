import { mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { signJwt } from "./jwt.mjs";

const applicationId = process.env.EB_APP_ID ?? "b239298e-83c6-4e7d-94f0-f8f6050da48c";
const keyPath = process.env.EB_KEY_PATH ?? join(homedir(), ".selfbank", `${applicationId}.pem`);
const country = process.argv[2] ?? "ES";

const { token, header, payload } = signJwt({ applicationId, keyPath });

// Solo se muestran cabecera y carga útil: no son secretas. Ni la firma ni la clave se imprimen.
console.log("JWT cabecera:", JSON.stringify(header));
console.log("JWT carga   :", JSON.stringify({ ...payload, iat_fecha: new Date(payload.iat * 1000).toISOString() }));

const response = await fetch(`https://api.enablebanking.com/aspsps?country=${encodeURIComponent(country)}`, {
  headers: { Authorization: `Bearer ${token}` },
});

const text = await response.text();
if (!response.ok) {
  console.error(`\nERROR HTTP ${response.status}: ${text.slice(0, 500)}`);
  process.exit(1);
}

const { aspsps } = JSON.parse(text);
if (!Array.isArray(aspsps)) {
  console.error("\nRespuesta inesperada, claves recibidas:", Object.keys(JSON.parse(text)));
  process.exit(1);
}

mkdirSync(new URL("./out/", import.meta.url), { recursive: true });
writeFileSync(new URL(`./out/aspsps-${country}.json`, import.meta.url), JSON.stringify(aspsps, null, 2));

const days = (seconds) => (typeof seconds === "number" ? `${Math.round(seconds / 86400)} d` : "—");
console.log(`\n${aspsps.length} bancos para ${country}\n`);
console.table(
  aspsps
    .map((a) => ({
      banco: a.name,
      psu: Array.isArray(a.psu_types) ? a.psu_types.join(",") : "—",
      consentimiento_max: days(a.maximum_consent_validity),
    }))
    .sort((x, y) => x.banco.localeCompare(y.banco, "es")),
);
console.log("Campos disponibles por banco:", Object.keys(aspsps[0] ?? {}).join(", "));
