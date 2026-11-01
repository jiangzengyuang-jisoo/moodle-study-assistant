import {readFileSync} from 'node:fs';
const descriptions = JSON.parse(readFileSync(new URL('./connection-errors.json', import.meta.url), 'utf8'));

export function connectionError(code) {
  const safe = Object.hasOwn(descriptions, code) ? code : 'STUDY_NETWORK';
  return Object.assign(new Error(descriptions[safe][0]), {code: safe});
}

export function classifyTransportError(error) {
  const seen = new Set(), pending = [error], errors = [];
  while (pending.length && errors.length < 16) {
    const item = pending.shift();
    if (!item || seen.has(item)) continue;
    seen.add(item); errors.push(item);
    pending.push(item.cause);
    if (Array.isArray(item.errors)) pending.push(...item.errors.slice(0, 16));
  }
  const codes = new Set(errors.map(e => e.code));
  if (codes.has('ENOTFOUND') || codes.has('EAI_AGAIN')) return connectionError('STUDY_DNS');
  if (errors.some(e => ['TimeoutError', 'AbortError'].includes(e.name)) ||
      ['ETIMEDOUT', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT'].some(c => codes.has(c))) return connectionError('STUDY_TIMEOUT');
  if (['CERT_HAS_EXPIRED', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'SELF_SIGNED_CERT_IN_CHAIN', 'ERR_TLS_CERT_ALTNAME_INVALID', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY'].some(c => codes.has(c))) return connectionError('STUDY_TLS');
  return connectionError('STUDY_NETWORK');
}

export function connectionDiagnostic(error) {
  return Object.hasOwn(descriptions, error?.code) ?
    {connection_error: error.code, message: descriptions[error.code][0], next_step: descriptions[error.code][1]} : null;
}
