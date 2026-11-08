// Generate the GitHub Pages directory site for the community plugin index.
//
// The source of truth for the plugin entry is docs/market/index.json; this
// script writes a deployable directory under release/pages:
//   index.json        plugin index (publishTimestamp set for this release)
//   index.html        short Chinese help page for students
//   icons/icon.svg    plugin icon referenced relatively by index.json
//
// It never pushes, enables Pages or publishes anything. Deployment steps are in
// docs/market/PUBLISH_PLAN.md.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const outDir = join(repoRoot, 'release', 'pages')
const indexSource = join(repoRoot, 'docs', 'market', 'index.json')
const helpSource = join(repoRoot, 'docs', 'market', 'pages', 'index.html')
const iconSource = join(repoRoot, 'plugin', 'icon.svg')

function fail(message) {
  console.error(`build-market-site: ${message}`)
  process.exit(1)
}

function argValue(name) {
  const index = process.argv.indexOf(name)
  return index >= 0 ? process.argv[index + 1] : undefined
}

function main() {
  if (!existsSync(indexSource)) fail('docs/market/index.json is missing')
  if (!existsSync(helpSource)) fail('docs/market/pages/index.html is missing')
  if (!existsSync(iconSource)) fail('plugin/icon.svg is missing')

  const index = JSON.parse(readFileSync(indexSource, 'utf8'))
  const version = index.plugins?.[0]?.versions?.[0]
  if (!version?.version) fail('index.json has no plugin version')

  const publishedAt = Number(argValue('--published-at') ?? process.env.MARKET_PUBLISHED_AT ?? Math.floor(Date.now() / 1000))
  if (!Number.isInteger(publishedAt) || publishedAt <= 0) fail('--published-at must be a Unix timestamp in seconds')
  version.publishTimestamp = publishedAt
  for (const plugin of index.plugins ?? []) {
    if (plugin.icon && !/^data:/i.test(plugin.icon) && !/^https?:/i.test(plugin.icon)) {
      // Relative icon paths resolve against the index URL on Pages.
      plugin.icon = String(plugin.icon).replace(/^\.?\//, '')
    }
  }

  rmSync(outDir, { recursive: true, force: true })
  mkdirSync(join(outDir, 'icons'), { recursive: true })
  writeFileSync(join(outDir, 'index.json'), JSON.stringify(index, null, 2) + '\n', 'utf8')
  cpSync(helpSource, join(outDir, 'index.html'))
  cpSync(iconSource, join(outDir, 'icons', 'icon.svg'))
  const size = statSync(join(outDir, 'icons', 'icon.svg')).size
  const iconMb = Math.round((statSync(join(outDir, 'icons', 'icon.svg')).size / 1024) * 10) / 10
  console.log(`build-market-site: wrote ${relative(repoRoot, outDir)}`)
  console.log(`build-market-site: index.json version=${version.version} publishedAt=${publishedAt}`)
  console.log(`build-market-site: icon=${iconMb} KiB, files=3`)
  if (size > 256 * 1024) fail('icon exceeds the 256 KiB plugin icon limit')
}

main()
