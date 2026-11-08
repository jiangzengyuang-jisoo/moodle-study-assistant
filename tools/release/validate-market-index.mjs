// Validate the community market index template with the DSH validator that
// ships inside an installed Harness (asar). Run with Electron-as-Node and
// point DSH_ASAR_ROOT at the installed runtime:
//   $env:DSH_ASAR_ROOT='<install>/resources/app.asar/dsh'
//   $env:ELECTRON_RUN_AS_NODE='1'
//   & '<install>/DeepSeek Harness.exe' tools/release/validate-market-index.mjs
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const asarRoot = process.env.DSH_ASAR_ROOT
if (!asarRoot) {
  console.error('market-index validation: set DSH_ASAR_ROOT to the installed Harness app.asar/dsh directory')
  process.exit(1)
}
const asarModules = `${asarRoot.replace(/[\\/]+$/, '')}/node_modules/@deepseek-ai`
const index = JSON.parse(readFileSync(new URL('../../docs/market/index.json', import.meta.url), 'utf8'))

let parseMarketIndex = null
const candidates = [
  `${asarModules}/dsh-plugin-market-index/lib/index.js`,
  `${asarModules}/dsh-plugin-market-index/lib/main.js`,
]
for (const candidate of candidates) {
  try {
    const module = await import(pathToFileURL(candidate).href)
    parseMarketIndex = module.parseMarketIndex ?? module.default?.parseMarketIndex ?? null
    if (parseMarketIndex) break
  } catch {
    // Try the next candidate.
  }
}
if (!parseMarketIndex) {
  console.error('market-index validation: parseMarketIndex not found in the installed Harness')
  process.exit(1)
}
try {
  const parsed = parseMarketIndex(index, 'https://example.invalid/dsh/index.json')
  const plugins = parsed.plugins ?? []
  console.log(JSON.stringify({
    ok: true,
    format: parsed.format,
    title: parsed.title,
    plugins: plugins.map((plugin) => ({
      name: plugin.name,
      versions: (plugin.versions ?? []).map((version) => ({ version: version.version, engines: version.engines ?? null })),
    })),
  }, null, 2))
} catch (error) {
  console.error('market-index validation failed:', error?.message ?? error)
  process.exit(1)
}
