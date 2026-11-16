# DeepSeek source update: Improvements from 16 November 2026

**English** · [简体中文](UPDATE_REPORT_2026-11-17-deepseek.md)

Prepared on 17 November 2026, Hong Kong time. This update merges committed DeepSeek development through `47f6799` with public `deepseek` changes through `5c46a81`, retaining both PDF tutorials and the login refresh tip. The Codex branch is unchanged.

## Improvements

- Before teaching, `study_check_chapter` checks chapter source timestamps, preferring modification time and falling back to creation time. Intact local originals with matching timestamps avoid another download; valid receipts are reused in the same chat. Explicit `strict` content diagnostics remain available.
- Existing full-course grants cover missing chapter files, new supplementary materials and recovery of lost originals. Ambiguous chapter matches return candidates without silently widening scope or triggering a full refresh.
- Fix remote updates when originals are missing, partial acquisition failures, index recovery, single-module versus whole-chapter receipts, timestamp baselines and interrupted replacement recovery. Account, page and local-annotation protections remain in place.
- After replacement originals and reading routes pass validation, retire only managed same-source originals in the current replacement chain. Preserve old IDs, hashes, provenance and answer history, with explicit missing or deleted states.
- Scanned PDFs and images without text layers validate rendering and report pages requiring visual review, without requiring OCR first. Corrupt, unrenderable or entirely blank materials still fail. Synchronization does not establish review or mastery.
- Current exam and deadline answers require applicable latest teacher notices. Save and read back actual teaching changes promptly. Unknown timestamps, missing chapter parts or failed synchronization pause affected teaching.

The merge also includes previously unpublished plugin source and build tools from the development branch. Their presence supports maintenance and future packaging; it does not mean an installable plugin has been published.

## Validation and limits

The merged public source is checked with the complete Node, Python installation-entry and Python study-tool suites; totals are recorded in this update's bilingual changelog. Runtime code matches `47f6799`; merge preparation retains remote tutorials and organizes documentation.

Tests use synthetic profiles and simulated school responses. This run does not validate the real DeepSeek model, Harness UI, school login or online course downloads. Matching timestamps cannot detect a server changing content without updating its timestamp.

## Upgrade and publication scope

Source users should follow the [record-preserving upgrade steps](新手使用指南.md#从旧版更新), update `deepseek`, retain `Study/`, configuration, credentials and grants, then restart the relevant services. Actual body or original-page reading is still required after verification.

This push includes source, tests and documentation only. It does not publish an npm package, GitHub Release, marketplace listing or new installation package. Frozen candidate `0.6.0-trial.8` does not contain the 16 November source changes, and existing plugin installations do not update automatically. See the [candidate notes](market/CURRENT_CANDIDATE.md) for historical verification and boundaries.

Personal data, credentials, runtime environments and nine pre-existing uncommitted development-document changes are excluded. Earlier “not pushed” statements describe their respective local maintenance runs; GitHub branch history establishes the actual push outcome.
