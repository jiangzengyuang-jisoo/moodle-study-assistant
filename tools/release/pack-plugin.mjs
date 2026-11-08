// Build the npm artifact for the Moodle Study Assistant bundle.
//
// Runs the normal package build, creates an actual `npm pack` tarball, and
// audits the packed bytes (not just the build directory): required bundle
// files, the six bundled runtime packages, no personal data, checksum.
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const packageDir = join(repoRoot, 'dist', 'moodle-study-assistant')
const releaseNpmDir = join(repoRoot, 'release', 'npm')

const REQUIRED_FILES = [
  'package/package.json',
  'package/index.js',
  'package/cordis.patch.yml',
  'package/icon.svg',
  'package/locale/en.json',
  'package/locale/zh.json',
  'package/LICENSE',
  'package/THIRD_PARTY_NOTICES.md',
  'package/program/tools/moodle/mcp-entry.mjs',
  'package/program/requirements-core.txt',
  'package/program/requirements-login.txt',
  'package/program/client-target.json',
]
const REQUIRED_ROOTS = [
  '@modelcontextprotocol/sdk',
  'moodle-mcp',
  'zod',
  'zod-to-json-schema',
  'ajv',
  'ajv-formats',
]
const FORBIDDEN_ENTRY = /(^|\/)(Study|\.private|\.venv)(\/|$)|config\.local\.json|\.pyc$|__pycache__/

function fail(message) {
  console.error(`pack-plugin: ${message}`)
  process.exit(1)
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? repoRoot,
    encoding: 'utf8',
    timeout: options.timeout ?? 600000,
    shell: options.shell ?? false,
    env: { ...process.env, ...(options.env ?? {}) },
  })
  if (result.error) {
    fail(`${command} could not start: ${result.error.message}`)
  }
  if (result.status !== 0) {
    console.error(String(result.stdout ?? '').slice(-2000))
    console.error(String(result.stderr ?? '').slice(-4000))
    fail(`${command} ${args.join(' ')} failed with exit code ${result.status}`)
  }
  return String(result.stdout ?? '')
}

function tarEntries(tgzPath) {
  return run('tar', ['-tzf', tgzPath], { cwd: dirname(tgzPath) })
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function cleanStaleTarballs() {
  for (const name of readdirSync(packageDir)) {
    if (name.endsWith('.tgz')) rmSync(join(packageDir, name), { force: true })
  }
}

function main() {
  console.log('pack-plugin: building the bundle...')
  run(process.execPath, [join(repoRoot, 'tools', 'release', 'build-plugin.mjs')])
  if (!existsSync(join(packageDir, 'package.json'))) fail('build did not produce a package directory')

  const manifest = JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8'))
  if (manifest.private) fail('publishable package must not be private')
  if (!manifest.version || !manifest.name) fail('package manifest is missing name/version')
  if (!manifest.engines?.dsh) fail('package manifest is missing engines.dsh')
  const vendoredDirs = readdirSync(join(packageDir, 'node_modules'), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => (entry.name.startsWith('@')
      ? readdirSync(join(packageDir, 'node_modules', entry.name), { withFileTypes: true })
          .filter((child) => child.isDirectory())
          .map((child) => `${entry.name}/${child.name}`)
      : [entry.name]))
  for (const name of REQUIRED_ROOTS) {
    if (!vendoredDirs.includes(name)) fail(`missing bundled dependency ${name}`)
    if (!existsSync(join(packageDir, 'node_modules', name, 'package.json'))) fail(`missing manifest for bundled dependency ${name}`)
  }
  const bundledNames = new Set(manifest.bundleDependencies ?? [])
  if (bundledNames.size !== vendoredDirs.length) fail('bundleDependencies does not cover every vendored package')
  for (const name of vendoredDirs) {
    if (!bundledNames.has(name)) fail(`vendored package ${name} is not declared as a bundled dependency`)
    if (!manifest.dependencies?.[name]) fail(`vendored package ${name} is not declared as a dependency`)
  }
  cleanStaleTarballs()

  console.log('pack-plugin: running npm pack...')
  const stdout = run('npm', ['pack', '--json'], { cwd: packageDir, shell: process.platform === 'win32' })
  const parsed = JSON.parse(stdout.slice(stdout.indexOf('[')).trim())
  const packed = Array.isArray(parsed) ? parsed[0] : parsed
  if (!packed?.filename) fail('npm pack did not report a filename')
  const tgzPath = join(packageDir, packed.filename)
  if (!existsSync(tgzPath)) fail(`npm pack output is missing: ${tgzPath}`)

  const entries = tarEntries(tgzPath)
  const missing = REQUIRED_FILES.filter((file) => !entries.includes(file))
  if (missing.length) fail(`packed tarball is missing: ${missing.join(', ')}`)
  const packedDeps = new Set(
    entries
      .map((entry) => /^package\/node_modules\/((?:@[^/]+\/)?[^/]+)\/package\.json$/.exec(entry)?.[1])
      .filter(Boolean),
  )
  for (const name of REQUIRED_ROOTS) {
    if (!packedDeps.has(name)) fail(`packed tarball is missing bundled dependency ${name}`)
  }
  if (packedDeps.size !== vendoredDirs.length) fail('packed tarball does not contain the full vendored dependency set')
  for (const name of packedDeps) {
    if (!bundledNames.has(name)) fail(`packed tarball includes undeclared dependency ${name}`)
  }
  const forbidden = entries.filter((entry) => FORBIDDEN_ENTRY.test(entry))
  if (forbidden.length) fail(`packed tarball contains forbidden entries: ${forbidden.slice(0, 5).join(', ')}`)
  const developmentEntries = entries.filter((entry) =>
    /^package\/(\.git(\/|$)|tools\/|tests\/|CHANGELOG|README\.zh-CN|package-lock\.json|node_modules\/\.cache)/.test(entry),
  )
  if (developmentEntries.length) fail(`packed tarball contains development-only entries: ${developmentEntries.slice(0, 5).join(', ')}`)

  // The packed manifest is the authority for install-time metadata.
  const packedManifestText = run('tar', ['-xzf', tgzPath, '-O', 'package/package.json'], { cwd: packageDir })
  const packedManifest = JSON.parse(packedManifestText)
  if (packedManifest.version !== manifest.version) fail('packed manifest version differs from the built package')
  if (packedManifest.private) fail('packed manifest still marks the package private')
  if (!Array.isArray(packedManifest.bundleDependencies) || packedManifest.bundleDependencies.length !== vendoredDirs.length)
    fail('packed manifest bundleDependencies is incomplete')
  if (!packedManifest.author || !packedManifest.repository || !packedManifest.license) fail('packed manifest is missing publish metadata')

  mkdirSync(releaseNpmDir, { recursive: true })
  const releaseTgz = join(releaseNpmDir, packed.filename)
  cpSync(tgzPath, releaseTgz)
  const digest = sha256(releaseTgz)
  const sizeMb = Math.round((statSync(releaseTgz).size / 1024 / 1024) * 100) / 100
  console.log(`pack-plugin: packed ${relative(repoRoot, releaseTgz)}`)
  console.log(`pack-plugin: version=${packedManifest.version} entries=${entries.length} size=${sizeMb}MB`)
  console.log(`pack-plugin: sha256=${digest}`)
}

main()
