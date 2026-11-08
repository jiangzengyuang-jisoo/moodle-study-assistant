// Build the installable DeepSeek Harness bundle for the Moodle Study Assistant.
//
// Output: dist/moodle-study-assistant/ — a self-contained plugin package:
//   * Host plugin (index.js), bundle patch, locale, icon
//   * program/  the study engine + MCP server + the six vendored Node packages
//   * no personal data: Study/, .private/, config.local.json, .venv, logs and
//     developer node_modules are never copied.
//
// The package has zero npm dependencies at install time: the MCP server's
// runtime packages are vendored, and Python dependencies are installed by the
// plugin's own first-use flow into a private support directory.
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const pluginSource = join(repoRoot, 'plugin')
const outRoot = join(repoRoot, 'dist')
const outDir = join(outRoot, 'moodle-study-assistant')

const VENDORED_NODE_PACKAGES = [
  '@modelcontextprotocol/sdk',
  'moodle-mcp',
  'zod',
  'zod-to-json-schema',
  'ajv',
  'ajv-formats',
]

const SKIP_DIRS = new Set(['__pycache__', 'node_modules', '.venv', '.git'])
const SKIP_TMP_PREFIX = /^tmp/i
const SKIP_FILE = /^(test_.*\.py|.*\.pyc|.*\.dpapi|config\.local\.json)$/

function fail(message) {
  console.error(`build-plugin: ${message}`)
  process.exit(1)
}

function copyTree(source, target, { skipFile = null, skipDir = null } = {}) {
  mkdirSync(target, { recursive: true })
  for (const entry of readdirSync(source, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name) || SKIP_TMP_PREFIX.test(entry.name)) continue
      if (skipDir && skipDir(entry.name)) continue
      copyTree(join(source, entry.name), join(target, entry.name), { skipFile, skipDir })
    } else if (entry.isFile()) {
      if (skipFile && skipFile(entry.name)) continue
      cpSync(join(source, entry.name), join(target, entry.name))
    }
  }
}

function checkVendoredPackage(name) {
  const dir = join(repoRoot, 'node_modules', name)
  if (!existsSync(join(dir, 'package.json'))) fail(`missing vendored package ${name}; run npm ci first`)
  let native = 0
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const path = join(current, entry.name)
      if (entry.isDirectory()) walk(path)
      else if (/\.(node|dll|exe|so|dylib)$/i.test(entry.name)) native += 1
    }
  }
  walk(dir)
  if (native) fail(`vendored package ${name} contains native binaries; review the packaging strategy`)
  return dir
}

function assertPatchedMoodleMcp(dir) {
  const assignments = readFileSync(join(dir, 'dist', 'tools', 'assignments.js'), 'utf8')
  const forums = readFileSync(join(dir, 'dist', 'tools', 'forums.js'), 'utf8')
  if (!assignments.includes('a.cmid')) fail('moodle-mcp assignments.js is not patched (run npm ci / postinstall)')
  if (!forums.includes('mod.instance')) fail('moodle-mcp forums.js is not patched (run npm ci / postinstall)')
}

function copyProgram() {
  const program = join(outDir, 'program')
  mkdirSync(program, { recursive: true })
  for (const dir of ['tools/moodle', 'tools/study', 'skills', 'templates']) {
    copyTree(join(repoRoot, dir), join(program, dir), { skipFile: (name) => SKIP_FILE.test(name) })
  }
  for (const file of ['LICENSE', 'THIRD_PARTY_NOTICES.md', 'requirements.txt']) {
    if (existsSync(join(repoRoot, file))) cpSync(join(repoRoot, file), join(program, file))
  }
  // Runtime requirements are split so first use can install the small core
  // engine first and the browser sign-in component only when needed.
  cpSync(join(pluginSource, 'resources', 'pip', 'core-requirements.txt'), join(program, 'requirements-core.txt'))
  cpSync(join(pluginSource, 'resources', 'pip', 'login-requirements.txt'), join(program, 'requirements-login.txt'))
  // Program assets: this file marks the DeepSeek target so the engine never
  // takes the legacy Codex branch or writes .codex/ into user data.
  writeFileSync(
    join(program, 'client-target.json'),
    JSON.stringify({ version: 1, client: 'deepseek' }, null, 2) + '\n',
    'utf8',
  )
  writeFileSync(
    join(program, 'build-info.json'),
    JSON.stringify(
      {
        pluginVersion: JSON.parse(readFileSync(join(pluginSource, 'package.json'), 'utf8')).version,
        builtAt: new Date().toISOString(),
        builtFromCommit: process.env.GIT_COMMIT ?? null,
      },
      null,
      2,
    ) + '\n',
    'utf8',
  )
  for (const name of VENDORED_NODE_PACKAGES) {
    const source = checkVendoredPackage(name)
    if (name === 'moodle-mcp') assertPatchedMoodleMcp(source)
    const target = join(program, 'node_modules', name)
    mkdirSync(dirname(target), { recursive: true })
    cpSync(source, target, { recursive: true })
  }
}

function copyPluginShell() {
  for (const entry of readdirSync(pluginSource, { withFileTypes: true })) {
    if (entry.name === 'program') continue
    const source = join(pluginSource, entry.name)
    const target = join(outDir, entry.name)
    if (entry.isDirectory()) copyTree(source, target)
    else cpSync(source, target)
  }
}

const FORBIDDEN_PATTERNS = [
  { name: 'developer absolute path', pattern: /D:[\\/]deepseek harness/i },
  { name: 'developer Windows user name', pattern: /Peter Jiang/i },
  { name: 'OpenAI-style secret', pattern: /sk-[A-Za-z0-9]{20,}/ },
  { name: 'Moodle token literal', pattern: /\bwstoken=[A-Za-z0-9]{20,}/ },
  { name: 'encoding corruption marker', pattern: /\uFFFD/ },
]

function auditPackage() {
  const problems = []
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const path = join(current, entry.name)
      if (entry.isDirectory()) {
        walk(path)
        continue
      }
      const rel = relative(outDir, path).replaceAll('\\', '/')
      if (/(^|\/)(Study|\.private|\.venv)(\/|$)/.test(rel) || rel.endsWith('config.local.json')) {
        problems.push(`forbidden path in package: ${rel}`)
        continue
      }
      if (/(^|\/)__pycache__(\/|$)/.test(rel) || rel.endsWith('.pyc')) {
        problems.push(`compiled Python cache in package: ${rel}`)
        continue
      }
      if (statSync(path).size > 2 * 1024 * 1024) continue
      if (!/\.(js|mjs|json|md|py|yml|yaml|txt|svg|csv)$/i.test(entry.name)) continue
      const text = readFileSync(path, 'utf8')
      for (const rule of FORBIDDEN_PATTERNS) {
        if (rule.pattern.test(text)) problems.push(`${rule.name} found in ${rel}`)
      }
    }
  }
  walk(outDir)
  return problems
}

function summary() {
  let files = 0
  let bytes = 0
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const path = join(current, entry.name)
      if (entry.isDirectory()) walk(path)
      else {
        files += 1
        bytes += statSync(path).size
      }
    }
  }
  walk(outDir)
  return { files, megabytes: Math.round((bytes / 1024 / 1024) * 10) / 10 }
}

function main() {
  if (!existsSync(pluginSource)) fail('plugin/ source directory is missing')
  rmSync(outDir, { recursive: true, force: true })
  mkdirSync(outDir, { recursive: true })
  copyPluginShell()
  copyProgram()

  const manifest = JSON.parse(readFileSync(join(outDir, 'package.json'), 'utf8'))
  if (manifest.name !== 'dsh-moodle-study') fail('unexpected package name')
  if (!manifest.dsh?.bundle?.patch) fail('package.json must declare dsh.bundle.patch')
  for (const file of ['cordis.patch.yml', 'index.js', 'icon.svg', 'locale/en.json', 'locale/zh.json']) {
    if (!existsSync(join(outDir, file))) fail(`missing required bundle file ${file}`)
  }
  const entry = join(outDir, 'program', 'tools', 'moodle', 'mcp-entry.mjs')
  if (!existsSync(entry)) fail('missing program/tools/moodle/mcp-entry.mjs')

  const problems = auditPackage()
  if (problems.length) {
    for (const problem of problems) console.error(`build-plugin: ${problem}`)
    fail(`package audit failed with ${problems.length} problem(s)`)
  }
  const info = summary()
  console.log(`build-plugin: built ${relative(repoRoot, outDir)} (${info.files} files, ${info.megabytes} MB)`)
  console.log('build-plugin: personal-data audit passed')
}

main()
