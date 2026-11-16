# 2026-11-16: Chapter sync, recovery and scanned lesson reading

**English** · [简体中文](UPDATE_REPORT_2026-11-16.md)

This update summarizes today's public `codex` changes through `3659998`. Before teaching, the assistant checks source timestamps for the selected chapter, acquires missing materials under existing grants, and provides original-page reading routes for scanned materials without a text layer.

## Main changes

- **Fewer repeated downloads.** Use the remote modification time, falling back to creation time. Reuse intact local originals with matching timestamps. Legacy records may download once to establish a baseline. Continue using chapter receipts in the same chat and check only newly needed scope.
- **Acquire missing materials under existing grants.** Missing chapter files, new supplementary uploads and lost registered originals are acquired within the current account, page and full-content authorization. No repeated download permission or full-course refresh is needed for that authorized scope.
- **Preserve historical evidence during replacement.** Once the new file and reading index pass validation, remove only managed same-source originals in the current replacement chain. Keep old material IDs, provenance, hashes and answer history. Preserve annotated originals, mismatched sources and files affected by failed updates; mark missing historical originals honestly.
- **Recover after failures.** Fix unchanged-timestamp recovery after indexing failures, remote updates when an original is missing, and interrupted replacement-chain deletion. Recheck local integrity and reading indexes before receipt reuse. Resolve the cause and explicitly retry a failed receipt.
- **Read scanned lessons through original pages.** PDFs and images without extracted text validate page rendering and identify pages requiring visual review. Successful sync retains source timestamps without requiring OCR first. Corrupt, unrenderable or entirely blank materials still fail. A reading route does not establish assistant review or student mastery.
- **Tighter source and information checks.** Fix omitted empty chapter resources, HTTP 200 JSON errors mistaken for text, cross-school source collisions and initial course-selection guidance. Current exam and deadline answers require applicable latest teacher notices; teaching changes must be saved and read back promptly.

## Validation for this update

The current public source was rechecked with the complete Node, Python installation-entry and Python study-tool suites. Results are recorded under “Publication summary and revalidation” in today's changelog. Tests use isolated temporary directories, synthetic courses and simulated school responses. Parser warnings from intentionally corrupt PDF fixtures are not test failures.

The outgoing file list and diff formatting were also checked. Publication preparation changes only bilingual reports, changelogs and homepage links, with no runtime code edits.

No live school login, online course download or clean-machine installation was tested. Earlier real-material isolation checks remain historical evidence and are not included in this revalidation. Matching source timestamps do not establish remote byte equality: a server changing content without updating timestamps is outside this strategy's detection. The strict `verify-chapter` diagnostic remains available.

## Upgrading an existing installation

Follow the [record-preserving upgrade steps](新手使用指南.md#从旧版更新) to update the `codex` source while retaining `Study/`, local configuration, credentials and course grants. No profile reset is needed; downloading source does not automatically update an installed folder.

Continue asking for study help in chat. The assistant locates chapter materials and calls `ensure-chapter`; material IDs are optional and ambiguous names require catalog review. It must then read the body or original pages before teaching. Explicitly retry a failed receipt with `--force` only after resolving the cause. Unknown timestamps, missing chapter parts or failed synchronization pause the affected teaching.

## Publication scope

The push targets the `codex` branch of `jiangzengyuang-jisoo/moodle-study-assistant`, containing general source, tests and documentation. Personal course files, learning state, credentials and runtime environments are excluded. The DeepSeek branch is outside scope. This is a source update, with no new version tag, GitHub Release or plugin package.

Earlier same-day “not pushed” statements describe the end of their individual local maintenance runs. This report summarizes the version prepared for this push; the GitHub branch history establishes its actual outcome.
