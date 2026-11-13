// Refresh local release handoff files after the candidate tarball is frozen.
// No build, publish, network access, credential access or acceptance claims.
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, posix, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const defaultRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex')
const jsonBytes = (value) => Buffer.from(JSON.stringify(value, null, 2) + '\n')

function tar(args) {
  const result = spawnSync('tar', args, { encoding: 'utf8', windowsHide: true, timeout: 60000, maxBuffer: 16 * 1024 * 1024 })
  if (result.error || result.status !== 0) throw new Error(`cannot inspect tarball: ${result.error?.message ?? result.stderr}`)
  return result.stdout
}

function sourceFiles(root, relativeDir) {
  const paths = []
  for (const entry of readdirSync(join(root, relativeDir), { withFileTypes: true })) {
    const path = `${relativeDir}/${entry.name}`
    if (entry.isSymbolicLink()) throw new Error(`source links are not supported: ${path}`)
    if (entry.isDirectory()) paths.push(...sourceFiles(root, path))
    else if (entry.isFile()) paths.push(path)
  }
  return paths.sort()
}

function copyTarget(source) {
  if (source === 'docs/PLUGIN_TRIAL_GUIDE.md') return 'release/使用说明.md'
  if (source === 'docs/PLUGIN_RELEASE_NOTES.md') return 'release/发布说明.md'
  if (source.startsWith('docs/market/')) return source.replace('docs/market/', 'release/市场提交材料/')
  return source
}

// Preserve the destination of relative Markdown links when renaming the copies.
// External URLs, fragments, code examples and document prose remain unchanged.
function markdownCopy(bytes, source, target) {
  return Buffer.from(bytes.toString('utf8').replace(/\]\(([^\s)]+)\)/g, (match, href) => {
    if (/^(?:[a-z][a-z0-9+.-]*:|\/|#)/i.test(href)) return match
    const splitAt = href.search(/[?#]/)
    const path = splitAt < 0 ? href : href.slice(0, splitAt)
    const suffix = splitAt < 0 ? '' : href.slice(splitAt)
    const original = posix.normalize(posix.join(posix.dirname(source), path))
    return `](${posix.relative(posix.dirname(target), copyTarget(original))}${suffix})`
  }))
}

function candidateInfo(root, manifest) {
  if (manifest.name !== 'dsh-moodle-study' || !/^[0-9A-Za-z][0-9A-Za-z.+-]*$/.test(manifest.version ?? '')) {
    throw new Error('plugin manifest has an unexpected package name/version')
  }
  const tarball = `npm/${manifest.name}-${manifest.version}.tgz`
  const path = join(root, 'release', tarball)
  if (!existsSync(path)) throw new Error(`frozen candidate tarball is missing: release/${tarball}`)
  const entries = tar(['-tzf', path]).split(/\r?\n/).filter(Boolean)
  const packed = JSON.parse(tar(['-xzf', path, '-O', 'package/package.json']))
  const build = JSON.parse(tar(['-xzf', path, '-O', 'package/program/build-info.json']))
  if (packed.name !== manifest.name || packed.version !== manifest.version || build.pluginVersion !== manifest.version) {
    throw new Error('tarball package/build version differs from plugin/package.json')
  }
  const bundled = packed.bundleDependencies ?? packed.bundledDependencies
  if (!Array.isArray(bundled) || !bundled.length || new Set(bundled).size !== bundled.length) {
    throw new Error('tarball bundled dependency manifest is missing or invalid')
  }
  for (const name of bundled) {
    if (!entries.includes(`package/node_modules/${name}/package.json`)) throw new Error(`tarball is missing bundled dependency: ${name}`)
  }
  return {
    tarball,
    version: packed.version,
    sha256: sha256(readFileSync(path)),
    sizeBytes: statSync(path).size,
    entries: entries.length,
    bundledDependencies: bundled.length,
    builtFromCommit: build.builtFromCommit ?? null,
    builtAt: build.builtAt ?? null,
  }
}

// Public only so tests can use a synthetic repository; the CLI always uses its
// own repository root and deliberately exposes no arbitrary output directory.
export function syncReleaseMaterials({ root = defaultRoot, check = false } = {}) {
  root = resolve(root)
  const manifest = JSON.parse(readFileSync(join(root, 'plugin/package.json'), 'utf8'))
  const candidate = candidateInfo(root, manifest)
  const market = JSON.parse(readFileSync(join(root, 'docs/market/index.json'), 'utf8'))
  const entry = market.plugins?.find((item) => item.name === manifest.name)
  if (entry?.versions?.[0]?.version !== manifest.version) throw new Error('market candidate version differs from plugin/package.json')

  // Prepare everything, including input validation, before writing any output.
  const files = new Map()
  const sources = ['docs/PLUGIN_TRIAL_GUIDE.md', 'docs/PLUGIN_RELEASE_NOTES.md', ...sourceFiles(root, 'docs/market')]
  for (const source of sources) {
    const target = copyTarget(source)
    const bytes = readFileSync(join(root, source))
    files.set(target.slice('release/'.length), source.endsWith('.md') ? markdownCopy(bytes, source, target) : bytes)
  }
  const pagesExist = existsSync(join(root, 'release/pages'))
  let pageTimestamp = null
  if (pagesExist) {
    // Keep an explicitly prepared timestamp, never invent a publication time.
    const previous = JSON.parse(readFileSync(join(root, 'release/pages/index.json'), 'utf8'))
    pageTimestamp = previous.plugins?.find((item) => item.name === manifest.name)?.versions?.[0]?.publishTimestamp
    if (!Number.isInteger(pageTimestamp) || pageTimestamp <= 0) throw new Error('existing pages index has no valid preparation timestamp')
    const pageIndex = structuredClone(market)
    pageIndex.plugins.find((item) => item.name === manifest.name).versions[0].publishTimestamp = pageTimestamp
    for (const plugin of pageIndex.plugins) {
      if (plugin.icon && !/^(?:data:|https?:)/i.test(plugin.icon)) plugin.icon = String(plugin.icon).replace(/^\.?\//, '')
    }
    files.set('pages/index.json', jsonBytes(pageIndex))
    files.set('pages/index.html', readFileSync(join(root, 'docs/market/pages/index.html')))
    files.set('pages/icons/icon.svg', readFileSync(join(root, 'plugin/icon.svg')))
  }

  // Only known distribution artifacts are hashed; never recursively scan an
  // arbitrary release folder that might contain personal files or diagnostics.
  const artifacts = new Map()
  for (const item of readdirSync(join(root, 'release/npm'), { withFileTypes: true })) {
    if (item.isFile() && /^dsh-moodle-study-[0-9A-Za-z.+-]+\.tgz$/.test(item.name)) artifacts.set(`npm/${item.name}`, readFileSync(join(root, 'release/npm', item.name)))
  }
  for (const item of readdirSync(join(root, 'release'), { withFileTypes: true })) {
    if (item.isFile() && /^moodle-study-assistant-[0-9A-Za-z.+-]+\.zip$/.test(item.name)) artifacts.set(item.name, readFileSync(join(root, 'release', item.name)))
  }
  const historical = [...artifacts.keys()].filter((name) => name !== candidate.tarball).sort()
  files.set('README.md', Buffer.from([
    '# 发布材料与本地候选', '',
    '> 本目录由 `npm run sync:release` 从当前文档与已冻结的安装包生成。',
    '> 同步只整理本地材料，不执行或证明 npm/Pages 已发布，也不代表通过运行或真机验收。',
    '> 公开状态、验收结果及发布操作以 [发布说明](发布说明.md) 和 [发布清单](市场提交材料/PUBLISH_PLAN.md) 为准。', '',
    `当前候选：**${manifest.name}@${candidate.version}**。`, '',
    `- 安装包：[${candidate.tarball}](${candidate.tarball})`,
    `- 大小：${candidate.sizeBytes} 字节；${candidate.entries} 个 tar 条目；${candidate.bundledDependencies} 个内置依赖包。`,
    `- SHA-256：\`${candidate.sha256}\``,
    `- 构建来源：\`${candidate.builtFromCommit ?? '未记录'}\`；构建时间：\`${candidate.builtAt ?? '未记录'}\`。`,
    '- 机器可读元数据：[release-manifest.json](release-manifest.json)；校验清单：[SHA256SUMS.txt](SHA256SUMS.txt)。',
    '- 学生说明：[使用说明](使用说明.md)；发布说明：[发布说明](发布说明.md)。',
    '- 社区目录材料：[市场提交材料](市场提交材料/README.md)。',
    pagesExist ? `- 站点准备文件：\`pages/\`；保留的时间戳 ${pageTimestamp} 仅为准备值，不证明已发布。正式发布时按发布清单重新生成。` : '- 站点尚未生成；正式发布时按发布清单生成 `pages/`，本次同步不创建站点。', '',
    '## 历史安装包', '',
    ...(historical.length ? historical.map((name) => `- [${name}](${name})（保留原文件，不作为当前候选）`) : ['暂无历史安装包。']), '',
    '## 同步与校验', '',
    '`npm run sync:release -- --check` 只检查受管理材料是否仍与来源文档及安装包一致，不修改文件。',
    'SHA256SUMS.txt 只包含受管理说明、元数据、已有站点及上述安装包；不扫描其他本地文件，也不包含校验清单自身。',
    '源文档中的历史版本与历史验收记录保留；复制后的相对 Markdown 链接按目标位置调整。', '',
  ].join('\n')))
  files.set('release-manifest.json', jsonBytes({
    schemaVersion: 1,
    ...candidate,
    verificationStatus: 'unverified',
    publicationStatus: 'unverified',
    note: 'Metadata and hashes only. Runtime acceptance and public release are not verified by this command; consult the release notes and publishing plan.',
    pages: { generated: pagesExist, preparationTimestamp: pageTimestamp },
    managedFiles: [...files.keys(), 'release-manifest.json', 'SHA256SUMS.txt'].sort(),
  }))
  const checksums = new Map([...artifacts, ...files])
  files.set('SHA256SUMS.txt', Buffer.from([...checksums.keys()].sort().map((name) => `${sha256(checksums.get(name))}  ${name}`).join('\n') + '\n'))

  const changed = [...files].filter(([name, bytes]) => {
    const target = join(root, 'release', name)
    return !existsSync(target) || !readFileSync(target).equals(bytes)
  }).map(([name]) => name)
  if (check && changed.length) throw new Error(`release materials are stale: ${changed.join(', ')}`)
  if (!check) {
    for (const name of changed) {
      const target = join(root, 'release', name)
      mkdirSync(dirname(target), { recursive: true })
      writeFileSync(target, files.get(name))
    }
  }
  return { ...candidate, changed, managedFiles: [...files.keys()].sort(), pagesGenerated: pagesExist }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2)
    if (args.length > 1 || (args.length && args[0] !== '--check')) throw new Error('usage: node tools/release/sync-release-materials.mjs [--check]')
    const result = syncReleaseMaterials({ check: args[0] === '--check' })
    console.log(`sync-release-materials: ${result.version}; ${result.changed.length} files updated; ${result.managedFiles.length} managed files; ${result.sha256}`)
  } catch (error) {
    console.error(`sync-release-materials: ${error?.message ?? error}`)
    process.exitCode = 1
  }
}
