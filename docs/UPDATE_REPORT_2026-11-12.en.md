# 2026-11-12: Teaching continuity fixes and review simplification

**English** · [简体中文](UPDATE_REPORT_2026-11-12.md)

This update targets the public `codex` branch and covers code commits `74e589f` and `e689499`. Progress readers and saves use consistent rules, legacy review commands use the current workflow, and unchanged pages are no longer rewritten.

## What changed

- **Consistent teaching continuity.** Course overview, course continuation and memory queries share one teaching-position selector. Assignment scope, recorded teaching position and mastery evidence remain separate. Saving an older lesson later does not by itself replace newer continuity; conflicts remain flagged for review.
- **Verified, retry-safe saves.** `teaching-save` stores learning events with actual occurrence times and chat sources under the shared lock, then reads them back. Stale requests cannot overwrite progress. Answered, skipped and answer-shown questions no longer remain current pending prompts. Existing answers, scores and FSRS history are preserved.
- **Legacy review branches removed.** `review.py due` uses current question preview and `render` uses the unified page writer. The retired `record` command directs callers to `daily.py attempt`; older archives cannot bypass question and answer-exposure checks.
- **Fewer redundant file operations.** State, library caches, material versions and pages share safe writing. Identical pages retain their files, and freshness checks reuse rendered plan content. Locks, recovery, snapshot validation and profile timezone behavior remain in place.

## Validation evidence

The public code passed **795 regression tests**: 573 study-tool Python tests, 92 Python onboarding/entry tests and 130 Node tests. Tests used project-local temporary directories, synthetic learning records and simulated school responses. These counts are for the public project, not the personal local deployment.

Follow-up runtime review also checked `study.cmd continue / today / continue` with a temporary profile, retry recovery after a successful save followed by failed page generation, before/after results across profile timezones, and preservation of original files plus temporary-file cleanup on write failure. All 9 unchanged pages in the inspected sample avoided rewriting. No regression requiring rollback was found.

This publication pass adds reports and documentation links only. It does not change runtime code or recount the earlier tests. Live school login, downloads and installation on a fresh computer have not been verified for this public update. Live media checks in the personal deployment do not substitute for public-project validation. Fewer writes do not establish a measured overall speed improvement.

## Upgrading an existing installation

Follow the [upgrade steps that preserve learning records](新手使用指南.md#从旧版更新) to update the `codex` code. Keep `Study/`, local configuration and credentials; no profile reset is required. Downloading source files does not automatically replace an existing installation.

See the [teaching continuity guide](MEMORY.md#教学接续保存与漏记检查) for saving and missing-record checks, and [tool instructions](TOOLS.md) for review commands. A save receipt only confirms events actually sent to the save command; it cannot guarantee that every chat has been recorded.

## Scope and remaining work

This sync includes public source, tests and documentation. It excludes personal learning records, course files, credentials and archived scripts from the personal deployment. The DeepSeek branch is outside this update.

Explicit file downloads and ongoing course synchronization remain separate, valid entry points. A small amount of filename and URL validation is still duplicated between them. This update neither claims to consolidate it nor removes either download feature.
