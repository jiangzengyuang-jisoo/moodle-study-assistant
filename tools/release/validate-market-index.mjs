// Validate a plugin index with the DSH validator that ships inside an
// installed Harness (asar). Run with Electron-as-Node and point DSH_ASAR_ROOT
// at the installed runtime:
//   $env:DSH_ASAR_ROOT='<install>/resources/app.asar/dsh'
//   $env:ELECTRON_RUN_AS_NODE='1'
//   & '<install>/DeepSeek Harness.exe' tools/release/validate-market-index.mjs \
//       --file docs/market/index.json \
//       --url https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const asarRoot = process.env.DSH_ASAR_ROOT
if (!asarRoot) {
  console.error('market-index validation: set DSH_ASAR_ROOT to the installed Harness app.asar/dsh directory')
  process.exit(1)
}
const argValue = (name) => {
  const index = process.argv.indexOf(name)
  return index >= 0 ? process.argv[index + 1] : undefined
}
const filePath = argValue('--file') ?? new URL('../../docs/market/index.json', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const indexUrl = argValue('--url') ?? 'https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json'
const index = JSON.parse(readFileSync(filePath, 'utf8'))
const asarModules = `${asarRoot.replace(/[\\/]+$/, '')}/node_modules/@deepseek-ai`

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
  const parsed = parseMarketIndex(index, indexUrl)
  const plugins = parsed.plugins ?? []
  console.log(JSON.stringify({
    ok: true,
    file: filePath,
    indexUrl,
    format: parsed.format,
    title: parsed.title,
    plugins: plugins.map((plugin) => ({
      name: plugin.name,
      icon: plugin.icon ?? null,
      author: plugin.author ?? null,
      repository: plugin.repository ?? null,
      versions: (plugin.versions ?? []).map((version) => ({
        version: version.version,
        engines: version.engines ?? null,
        source: version.source ?? null,
      })),
    })),
  }, null, 2))
} catch (error) {
  console.error('market-index validation failed:', error?.message ?? error)
  process.exit(1)
}
