// Shared, side-effect-free helpers for reading directly downloadable course files.
// Credentials never enter reports; only token-free webservice URLs are returned.
import {createHash} from 'node:crypto';
import {basename, extname} from 'node:path';

export const MAX_BYTES = 64 * 1024 * 1024;
// Direct file areas can legitimately contain documents, images and recordings.
export const FILE_EXTENSIONS = new Set([
  '.pdf', '.pptx', '.docx', '.xlsx', '.ppt', '.doc', '.xls', '.odt', '.odp', '.ods',
  '.txt', '.csv', '.rtf', '.md', '.html', '.htm',
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg',
  '.mp3', '.m4a', '.wav', '.ogg', '.mp4', '.m4v', '.webm',
  '.zip',
]);
export const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']);
const PACKAGE_EXTENSIONS = new Set(['.pptx', '.docx', '.xlsx', '.odt', '.odp', '.ods', '.zip']);
const LEGACY_OFFICE_EXTENSIONS = new Set(['.ppt', '.doc', '.xls']);

export function digest(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

export function safeName(value) {
  return typeof value === 'string' && value.length <= 240 && value === basename(value)
    && !/[<>:"/\\|?*\x00-\x1f]/.test(value)
    && !/[. ]$/.test(value) && value !== '.' && value !== '..'
    && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(value);
}

export function safeSubdir(value) {
  return typeof value === 'string' && /^[A-Za-z0-9._-]{1,80}$/.test(value)
    && value !== '.' && value !== '..' && !value.startsWith('.');
}

export function remoteName(source) {
  if (typeof source?.remote_filename === 'string' && source.remote_filename) return source.remote_filename;
  try { return decodeURIComponent(new URL(source?.url).pathname.split('/').at(-1)); }
  catch { return null; }
}

export function extensionOf(filename) {
  return extname(String(filename ?? '')).toLowerCase();
}

export function isDownloadableName(filename) {
  const extension = extensionOf(filename);
  return safeName(filename) && FILE_EXTENSIONS.has(extension);
}

export function isImageName(filename) {
  return safeName(filename) && IMAGE_EXTENSIONS.has(extensionOf(filename));
}

// Convert a pluginfile URL to the token-authenticated webservice endpoint. The
// returned URL contains no token; callers add it only for the live request.
export function webserviceFileURL(site, raw) {
  const base = new URL(site), url = new URL(raw);
  const prefix = base.pathname.replace(/\/$/, '');
  if (base.protocol !== 'https:' || url.origin !== base.origin || url.username || url.password || url.hash)
    throw new Error('Unsupported course file source');
  if (url.pathname.startsWith(prefix + '/pluginfile.php/'))
    url.pathname = prefix + '/webservice' + url.pathname.slice(prefix.length);
  if (!url.pathname.startsWith(prefix + '/webservice/pluginfile.php/'))
    throw new Error('Unsupported course file endpoint');
  url.search = '';
  url.searchParams.set('forcedownload', '1');
  return url;
}

function imageMagic(bytes) {
  const head = bytes.subarray(0, 16);
  return head.subarray(0, 8).toString('hex') === '89504e470d0a1a0a'
    || head.subarray(0, 3).toString('hex') === 'ffd8ff'
    || /^GIF8[79]a/.test(bytes.subarray(0, 6).toString('latin1'))
    || (bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WEBP');
}

function magicMatches(filename, bytes) {
  const extension = extensionOf(filename);
  const head = bytes.subarray(0, 1024);
  // Moodle file areas sometimes keep a wrong extension (for example a JPEG named
  // .png). Images are validated by content, not by the filename suffix.
  if (IMAGE_EXTENSIONS.has(extension)) {
    if (extension === '.svg') {
      const text = bytes.subarray(0, 2048).toString('utf8').toLowerCase();
      return text.includes('<svg') || (text.includes('<?xml') && text.includes('svg'));
    }
    return imageMagic(bytes);
  }
  if (extension === '.pdf') return head.includes(Buffer.from('%PDF-'));
  if (PACKAGE_EXTENSIONS.has(extension)) return bytes.subarray(0, 2).toString() === 'PK';
  if (LEGACY_OFFICE_EXTENSIONS.has(extension)) return bytes.subarray(0, 8).toString('hex') === 'd0cf11e0a1b11ae1';
  if (extension === '.mp3') return bytes.subarray(0, 3).toString() === 'ID3' || (bytes[0] === 0xff && (bytes[1] & 0xe0) === 0xe0);
  if (extension === '.wav') return bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WAVE';
  if (extension === '.ogg') return bytes.subarray(0, 4).toString() === 'OggS';
  if (extension === '.mp4' || extension === '.m4v' || extension === '.m4a') return bytes.subarray(4, 8).toString() === 'ftyp';
  if (extension === '.webm') return bytes.subarray(0, 4).toString('hex') === '1a45dfa3';
  if (extension === '.html' || extension === '.htm') return /<(!doctype|html|head|body|div|p|table|meta|title|section|article)\b/i.test(head.toString('utf8'));
  if (extension === '.rtf') return head.toString('latin1').startsWith('{\\rtf');
  // Plain text formats have no reliable signature; the size and empty checks still apply.
  return true;
}

export function validateFileBytes(filename, bytes) {
  if (!bytes.length) throw new Error('empty file');
  if (!magicMatches(filename, bytes)) throw new Error('file signature mismatch');
  return bytes;
}

// Read one directly downloadable file. Returns token-free URL plus verified bytes.
export async function fetchCourseFile(client, file, {maxBytes = MAX_BYTES, expectedBytes = null} = {}) {
  const clean = webserviceFileURL(client.baseUrl, file.url ?? file.fileurl);
  const authenticated = new URL(clean);
  authenticated.searchParams.set('token', client.token);
  let response;
  try {
    response = await client.transport(authenticated.toString(), {
      method: 'GET', redirect: 'error', signal: AbortSignal.timeout(60000),
    });
    const contentType = response.headers.get('content-type') ?? '';
    if (!response.ok || !response.body) throw new Error('invalid response');
    const extension = extensionOf(file.filename);
    if (/application\/json/i.test(contentType)) throw new Error('invalid response');
    if (/text\/html/i.test(contentType) && !['.html', '.htm'].includes(extension)) throw new Error('invalid response');
    const declared = Number(response.headers.get('content-length'));
    if (Number.isFinite(declared) && declared > maxBytes) throw new Error('too large');
    const chunks = [];
    let length = 0;
    for await (const chunk of response.body) {
      length += chunk.length;
      if (length > maxBytes) throw new Error('too large');
      chunks.push(Buffer.from(chunk));
    }
    const bytes = Buffer.concat(chunks);
    validateFileBytes(file.filename, bytes);
    const expected = Number.isFinite(file.filesize) && file.filesize > 0 ? file.filesize : expectedBytes;
    if (Number.isFinite(expected) && expected > 0 && expected !== length) throw new Error('size mismatch');
    return {bytes, source_url: clean.toString(), content_type: contentType || null};
  } catch {
    // Fetch errors may contain the authenticated URL. Never propagate them.
    throw new Error('Course file download failed (network, redirect, type, size or integrity)');
  }
}
