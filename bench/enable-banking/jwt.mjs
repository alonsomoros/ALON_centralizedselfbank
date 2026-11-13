import { createPrivateKey, createSign } from "node:crypto";
import { readFileSync } from "node:fs";

const b64url = (input) =>
  Buffer.from(input).toString("base64url");

/**
 * Firma un JWT RS256 para la API de Enable Banking.
 * La clave privada se lee del disco, se usa en memoria y nunca se imprime.
 */
export function signJwt({ applicationId, keyPath, ttlSeconds = 3600 }) {
  const key = createPrivateKey(readFileSync(keyPath));
  const iat = Math.floor(Date.now() / 1000);

  const header = { typ: "JWT", alg: "RS256", kid: applicationId };
  const payload = {
    iss: "enablebanking.com",
    aud: "api.enablebanking.com",
    iat,
    exp: iat + ttlSeconds,
  };

  const signingInput = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(payload))}`;
  const signature = createSign("RSA-SHA256").update(signingInput).sign(key);

  return { token: `${signingInput}.${signature.toString("base64url")}`, header, payload };
}
