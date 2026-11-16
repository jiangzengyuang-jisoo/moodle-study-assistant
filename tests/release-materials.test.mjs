import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve, sep } from 'node:path'
import { syncReleaseMaterials } from '../tools/release/sync-release-materials.mjs'

const digest = (value) => createHash('sha256').update(value).digest('hex')
const version = '0.6.0-trial.7'
const packageName = 'dsh-moodle-study'
const base = join(tmpdir(), 'moodle-release-materials-tests')
mkdirSync(base, { recursive: true })

function put(root, name, value) {
  const path = join(root, name)
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, typeof value === 'string' ? value : JSON.stringify(value, null, 2) + '\n')
}

function fixture(t, { packedVersion = version, marketVersion = version, pages = false } = {}) {
  const root = mkdtempSync(join(base, 'case-'))
  t.after(() => {
    assert(resolve(root).startsWith(resolve(base) + sep))
    rmSync(root, { recursive: true, force: true })
  })
  put(root, 'plugin/package.json', { name: packageName, version })
  put(root, 'plugin/icon.svg', '<svg>current icon</svg>')
  put(root, 'docs/PLUGIN_TRIAL_GUIDE.md', '# Guide\n[Candidate](market/CURRENT_CANDIDATE.md#limits)\n[Changelog](../CHANGELOG.md)\n')
  put(root, 'docs/market/CURRENT_CANDIDATE.md', '# Candidate\n[Guide](../PLUGIN_TRIAL_GUIDE.md)\n[Market](README.md)\n')
  put(root, 'docs/market/index.json', { format: 1, plugins: [{ name: packageName, icon: './icons/icon.svg', versions: [{ version: marketVersion, publishTimestamp: 123, source: { type: 'npm' } }] }] })
  put(root, 'docs/market/README.md', '# Market\n[Guide](../PLUGIN_TRIAL_GUIDE.md)\n[Metadata](../../release/release-manifest.json)\n[Remote](https://example.test/x)\n')
  put(root, 'docs/market/PUBLISH_PLAN.md', '# Pending\nNo publication performed.\n')
  put(root, 'docs/market/pages/index.html', '<html>not published; current help</html>')
  put(root, 'pack/package/package.json', { name: packageName, version: packedVersion, bundleDependencies: ['mock-runtime'] })
  put(root, 'pack/package/program/build-info.json', { pluginVersion: packedVersion, builtFromCommit: 'a'.repeat(40), builtAt: '2026-11-13T16:00:00.000Z' })
  put(root, 'pack/package/node_modules/mock-runtime/package.json', { name: 'mock-runtime', version: '1.0.0' })
  mkdirSync(join(root, 'release/npm'), { recursive: true })
  const packed = spawnSync('tar', ['-czf', join(root, `release/npm/${packageName}-${version}.tgz`), '-C', join(root, 'pack'), 'package'], { encoding: 'utf8', windowsHide: true })
  assert.equal(packed.status, 0, packed.error?.message ?? packed.stderr)
  put(root, 'release/npm/dsh-moodle-study-0.6.0-trial.6.tgz', 'old tarball bytes are preserved')
  put(root, 'release/moodle-study-assistant-0.6.0-trial.3.zip', 'old zip bytes are preserved')
  put(root, 'release/private-notes.txt', 'not a managed release file')
  if (pages) put(root, 'release/pages/index.json', { plugins: [{ name: packageName, versions: [{ version: '0.6.0-trial.4', publishTimestamp: 99 }] }] })
  return root
}

test('sync derives candidate metadata and hashes from the actual tarball, preserving history and excluding other files', (t) => {
  const root = fixture(t)
  const tgz = join(root, `release/npm/${packageName}-${version}.tgz`)
  const bytes = readFileSync(tgz)
  const result = syncReleaseMaterials({ root })
  assert.equal(result.sha256, digest(bytes))
  assert.equal(result.sizeBytes, bytes.length)
  assert.equal(result.bundledDependencies, 1)
  const metadata = JSON.parse(readFileSync(join(root, 'release/release-manifest.json')))
  assert.equal(metadata.version, version)
  assert.equal(metadata.builtFromCommit, 'a'.repeat(40))
  assert.equal(metadata.verificationStatus, 'unverified')
  assert.equal(metadata.publicationStatus, 'unverified')
  assert.deepEqual(metadata.managedFiles, result.managedFiles)
  assert.equal(readFileSync(join(root, 'release/npm/dsh-moodle-study-0.6.0-trial.6.tgz'), 'utf8'), 'old tarball bytes are preserved')
  assert.equal(readFileSync(join(root, 'release/moodle-study-assistant-0.6.0-trial.3.zip'), 'utf8'), 'old zip bytes are preserved')
  const checksums = readFileSync(join(root, 'release/SHA256SUMS.txt'), 'utf8')
  assert(!checksums.includes('private-notes'))
  assert(!checksums.includes('SHA256SUMS.txt'))
  for (const line of checksums.trim().split('\n')) {
    const [, hash, name] = /^([a-f0-9]{64})  (.+)$/.exec(line)
    assert.equal(digest(readFileSync(join(root, 'release', name))), hash, name)
  }
  assert(!existsSync(join(root, 'release/pages')))
  assert.match(readFileSync(join(root, 'release/README.md'), 'utf8'), /站点尚未生成/)
  assert.deepEqual(syncReleaseMaterials({ root }).changed, [])
  assert.deepEqual(syncReleaseMaterials({ root, check: true }).changed, [])
})

test('renamed copies retain relative Markdown destinations and preserve historical prose', (t) => {
  const root = fixture(t)
  put(root, 'docs/market/CURRENT_CANDIDATE.md', '# trial.7\nHistorical trial.4 evidence stays historical.\n[Guide](../PLUGIN_TRIAL_GUIDE.md?lang=zh#first)\n[Market](README.md)\n')
  syncReleaseMaterials({ root })
  assert.match(readFileSync(join(root, 'release/使用说明.md'), 'utf8'), /\]\(市场提交材料\/CURRENT_CANDIDATE.md#limits\)/)
  assert(!existsSync(join(root, 'release/发布说明.md')))
  const notes = readFileSync(join(root, 'release/市场提交材料/CURRENT_CANDIDATE.md'), 'utf8')
  assert.match(notes, /Historical trial.4 evidence stays historical/)
  assert.match(notes, /\]\(\.\.\/使用说明.md\?lang=zh#first\)/)
  assert.match(notes, /\]\(README.md\)/)
  assert.match(readFileSync(join(root, 'release/README.md'), 'utf8'), /\]\(市场提交材料\/CURRENT_CANDIDATE.md\)/)
  assert.match(readFileSync(join(root, 'release/README.md'), 'utf8'), /\]\(\.\.\/CHANGELOG.md\)/)
  const market = readFileSync(join(root, 'release/市场提交材料/README.md'), 'utf8')
  assert.match(market, /\]\(\.\.\/使用说明.md\)/)
  assert.match(market, /\]\(\.\.\/release-manifest.json\)/)
  assert.match(market, /\]\(https:\/\/example.test\/x\)/)
})

test('existing site is refreshed with its preparation timestamp, not the current time', (t) => {
  const root = fixture(t, { pages: true })
  syncReleaseMaterials({ root })
  const site = JSON.parse(readFileSync(join(root, 'release/pages/index.json')))
  assert.equal(site.plugins[0].versions[0].version, version)
  assert.equal(site.plugins[0].versions[0].publishTimestamp, 99)
  assert.equal(site.plugins[0].icon, 'icons/icon.svg')
  assert.equal(readFileSync(join(root, 'release/pages/index.html'), 'utf8'), '<html>not published; current help</html>')
  assert.equal(readFileSync(join(root, 'release/pages/icons/icon.svg'), 'utf8'), '<svg>current icon</svg>')
  assert.deepEqual(readdirSync(join(root, 'release/pages')).sort(), ['icons', 'index.html', 'index.json'])
})

test('--check detects source-document drift without updating stale copies or checksums', (t) => {
  const root = fixture(t)
  syncReleaseMaterials({ root })
  const before = readFileSync(join(root, 'release/SHA256SUMS.txt'))
  const copy = readFileSync(join(root, 'release/使用说明.md'))
  put(root, 'docs/PLUGIN_TRIAL_GUIDE.md', '# Changed guide\n')
  assert.throws(() => syncReleaseMaterials({ root, check: true }), /release materials are stale:.*使用说明/)
  assert.deepEqual(readFileSync(join(root, 'release/SHA256SUMS.txt')), before)
  assert.deepEqual(readFileSync(join(root, 'release/使用说明.md')), copy)
  syncReleaseMaterials({ root })
  assert.equal(readFileSync(join(root, 'release/使用说明.md'), 'utf8'), '# Changed guide\n')
})

for (const [name, options, pattern] of [
  ['packed version mismatch', { packedVersion: '0.6.0-trial.6' }, /tarball package\/build version differs/],
  ['market version mismatch', { marketVersion: '0.6.0-trial.6' }, /market candidate version differs/],
]) {
  test(`${name} fails before writing any handoff files`, (t) => {
    const root = fixture(t, options)
    assert.throws(() => syncReleaseMaterials({ root }), pattern)
    assert(!existsSync(join(root, 'release/README.md')))
    assert(!existsSync(join(root, 'release/使用说明.md')))
    assert(!existsSync(join(root, 'release/release-manifest.json')))
  })
}

test('missing frozen candidate is rejected without creating a release summary', (t) => {
  const root = fixture(t)
  rmSync(join(root, `release/npm/${packageName}-${version}.tgz`))
  assert.throws(() => syncReleaseMaterials({ root }), /frozen candidate tarball is missing/)
  assert(!existsSync(join(root, 'release/README.md')))
})
