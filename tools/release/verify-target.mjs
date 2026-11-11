// Shared release-verification target resolution.
//
// The startup verifier and the study-tool verifier must always check the same
// artifact. Both accept an explicit unpacked package directory as the first
// positional argument (or MOODLE_STUDY_VERIFY_PACKAGE), and otherwise default
// to the local build at dist/moodle-study-assistant. verify-plugin passes its
// own resolved target to verify-study-tools, so an installed npm artifact can
// be verified end to end without the study-tool checks silently falling back to
// a different local build.
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const repoRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))))

export function resolveVerifyTarget(argv = process.argv.slice(2)) {
  const requested = argv.find((value) => !value.startsWith('-')) ?? process.env.MOODLE_STUDY_VERIFY_PACKAGE ?? null
  const packageDir = requested ? resolve(requested) : join(repoRoot, 'dist', 'moodle-study-assistant')
  const manifestPath = join(packageDir, 'package.json')
  if (!existsSync(manifestPath)) {
    throw new Error(`verify target is not an unpacked plugin package (package.json missing): ${packageDir}`)
  }
  let manifest
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  } catch (error) {
    throw new Error(`verify target manifest is not valid JSON: ${manifestPath} (${error?.message ?? error})`)
  }
  if (manifest?.name !== 'dsh-moodle-study') {
    throw new Error(`verify target manifest is not dsh-moodle-study: ${packageDir}`)
  }
  const programDir = join(packageDir, 'program')
  const entry = join(programDir, 'tools', 'moodle', 'mcp-entry.mjs')
  if (!existsSync(entry)) {
    throw new Error(`verify target is missing program/tools/moodle/mcp-entry.mjs: ${packageDir}`)
  }
  return { packageDir, programDir, entry, version: manifest.version ?? '0.0.0' }
}
