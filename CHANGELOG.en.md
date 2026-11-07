# Changelog

**English** · [简体中文](CHANGELOG.md)

## DeepSeek branch merge notes

- Retained independent client configuration while incorporating Chrome login, SSO authorization-resumption fixes, and course-library initialization.
- Normal refresh recognizes both authorization formats. A new information-only selection takes precedence over older grants, and course initialization avoids duplicate downloads.
- Preserved compatibility with legacy material installation, source references, and version archives, and corrected the diagnostic report location.

This log records how each update affects studying. Dates follow code commits in Hong Kong time (UTC+8). Earlier entries were reconstructed from commit history on 2026-10-30; they do not imply that separate installation packages were released on those dates. Entries describe behavior at the time of each update; later entries may supersede it. Linked detailed guides are primarily in Chinese.

Existing users should first read the [upgrade steps that preserve learning records](docs/新手使用指南.md#从旧版更新). Downloading new code does not automatically update an existing folder on your computer.

| Date | Main changes |
| --- | --- |
| [2026-11-08](#2026-11-08) | Publish audit fixes for remaining study capacity and bound-answer grading; make chapter checks locate the chapter precisely, reuse same-chat strict receipts and show stage progress |
| [2026-11-07](#2026-11-07) | Port same-question exposure, planning and exam-source checks; re-verify old papers/questions after courseware archiving; add five on-demand read queries; fix archive rewriting history evidence and review packages missing multiple-choice options; fix cross-school numeric ID collisions; post-audit fixes for review remaining capacity and bound-answer grading saves |
| [2026-11-06](#2026-11-06) | Complete English changelog for the Harness edition, language links, and English README navigation |
| [2026-11-05](#2026-11-05) | Chapter-scoped checks: starting a chapter strictly verifies its files instead of collecting information across the school |
| [2026-11-02](#2026-11-02) | One-time course authorization with automatic download, archiving, and indexing; bilingual memory recall and corrections; optional DeepSeek Harness integration |
| [2026-11-01](#2026-11-01) | Bilingual homepages explaining the course library and ongoing study; narrower startup scope, connection diagnostics, audit fixes, and known limitations |
| [2026-10-31](#2026-10-31) | Protection for tasks, deadlines, and discussions; hidden answer materials; review workload adjusted after actual overruns; teaching continuity and Chinese onboarding |
| [2026-10-30](#2026-10-30) | Current material versions for daily use; archived originals with historical references; optional cleanup after at least 30 days |
| [2026-10-23](#2026-10-23) | Reusable material parsing and fewer repeated Moodle checks within a chat |
| [2026-10-16](#2026-10-16) | Built-in self-study tutor with graded hints, Feynman explanations, transfer practice, and independent checks |
| [2026-10-11](#2026-10-11) | Daily review questions, learning feedback, weekly reviews, exam coverage checks, and mock exams |
| [2026-09-22](#2026-09-22) | Initial release: Windows installation, school account connection, course information, and learning records |

## 2026-11-08

### Precise chapter scope, same-chat reuse and waiting progress

- Fixed the slow chapter check: `chapter-check --section "Topic 5"` previously treated the Moodle section position (`section.section = 5`), the date `5` inside `Tutorial 3 (Oct 5 - Oct 9)` and `Tutorial 5` all as Topic 5. It selected the whole "Lecture Slides & Notes" area and two tutorial sections — 20 files, about 50.7 seconds in the latest run. The tool now resolves the chapter's modules from the local material index and its source mapping first (in this receipt `Topic5.pdf` belongs to module 4309760) and strictly verifies only those modules. Moodle section positions, dates, tutorial numbers and bare digits are no longer treated as chapter numbers; ambiguous matches return candidates and reasons (`verdict: ambiguous`), are written to `<chapter>.ambiguous.json`, never overwrite a usable receipt and never widen silently. No course ID, Topic 5 or module 4309760 is hard-coded as a general rule.
- Reliable same-chat reuse: a strict receipt is bound to the original chat digest (the raw chat ID is never stored), the school account, the course and page scope, the resolved material scope, the `strict` verification strength and the local file content hashes. The same chat and scope with unchanged local files reuse the receipt without network access. New local chapter material only verifies the added modules instead of re-downloading old files. A new chat, an account/authorization/course-scope change, changed local files, switching to quick or an explicit `--force` rechecks. Failed, missing-baseline, partially completed or ambiguous receipts never count as full success and are not retried automatically on ordinary follow-ups; only `--force` rechecks. The default remains `strict`; content-hash verification was not downgraded to quick.
- Better waiting: Node reports `[chapter-check]` stages on stderr (scope resolution, course catalog, per-file verification) while stdout keeps only the final JSON. The Python entry point forwards stderr while the child runs instead of buffering with `capture_output`, and the command warns against `Select-Object -Last 40` truncation. The current Harness background-task ability was verified (a `pwsh` background task exposes running stderr output and a completion notice); a slow first check can be followed as that same background task without starting a duplicate. The performance fix is not merely a lower `timeoutMs`: it comes from verifying one file and from cached same-chat reuse without network access.
- Boundaries unchanged: the tool is read-only; it does not write learning state/ledger/catalog, download into the library or trigger a full Moodle refresh. Strict checks still require the course-level `full` authorization; permission or decryption failures follow the existing authorization path without retry loops. Global permissions are unchanged, browser cookies are never read and credentials are never stored in plaintext. A chapter receipt still cannot substitute for announcements, assignments, discussions, the calendar or other chapters, nor be used for `review`, exam sources or a full mock paper.
- Upgrade: no `Study/` profile change is needed. Old (schema 1) chapter receipts are not reused; the next strict check rebuilds a strict receipt with the new scope, and the same chat reuses it from the second call onward. For ambiguous matches use a clearer chapter name or `--modules`; add `--force` when the learner explicitly asks to check again.
- Validation: offline synthetic courses used isolated temporary directories and did not connect to the real school. All three suites passed: **153** Node, **100** Python setup/entry and **568** Python study tests — **821** total, 0 failures, 0 skips (the Node suite ran with a workspace temp directory; the machine's existing system-temp multi-process lock limitation causes unrelated environment failures). The chapter-check coverage adds **31** Node and **26** Python entry tests for: Topic 5 not selecting other topics, dates or tutorial sections; Moodle section positions not becoming chapter numbers; ambiguous matches returning candidates without network access; the second same-chat check not using the network; incremental verification of added material; receipt invalidation on local-file change, new chat, account change and `--force`; failures or missing baselines never reported as success and never auto-retried; and stderr stages being visible while stdout stays parseable.
- Real check (authorized target material, one strict check only): `ECON1220` (`moodle-146887`) `Topic 5` verified only `Topic5.pdf` (module 4309760), `unchanged`, `verified.complete: true`, about **7.8 seconds** (the previous similar command took about 50.7 seconds for 20 files). The second check in the same chat returned in **0.3 seconds** with `reused: true, network_started: false` and no network access. That run covers the chapter files only; it does not establish that announcements, assignments or all teaching information are current, nor real SSO/MFA or other courses.

### Audit-fix publication

- This publication uses `325f830` as its code baseline and targets `deepseek`. Plans, official review packages and read-only previews share remaining capacity, stop adding questions when time is exhausted and avoid deducting review time already included in cumulative feedback twice.
- Bound answers can advance through grading workflow states. Verified legacy and parent-question bindings remain compatible while answers, sources, help conditions, grading verdicts and unknown states remain protected. Bilingual documentation distinguishes repaired normal archiving from old records requiring review.
- Validation: Reran all three offline suites before publication on November 8: 138 Node, 100 Python setup/entry and 561 Python study tests; all 799 passed with no failures or skips.
- Upgrade the matching client branch while retaining `Study/`, configuration and credentials; do not manually delete or rebuild bindings. Previously damaged records still need individual comparison with backups. Only code, tests and documentation are published, excluding personal profiles. Offline checks do not establish real school SSO/MFA, fresh-machine setup or end-to-end teaching quality.

## 2026-11-07

### Workflow-consistency fixes after audit review

- Fixed review remaining capacity not being shared. With 10 minutes available and an 8-minute study budget, after saving 8 cumulative minutes for the day through the normal `daily.py feedback` entry point, the main plan showed "at most 0 minutes left" while the review flow still added a 5-minute pending question. The main plan, the official packages prepared by `daily.py run/attempt/feedback/answer-shown/mock-attempt`, and the read-only `review.py due` preview now share `planning.remaining_study_minutes`: by the profile's study timezone, the day's known total budget (capacity-derived study budget plus directly recorded plan totals) minus reliable time feedback; cumulative feedback that already includes per-question review time is not deducted again; unknown feedback range or unknown total capacity returns no value instead of guessing. When capacity is exhausted no new questions are added: unstarted items are marked `deferred` with `budget_exempt`, answered/exposed items and FSRS history are kept, and deferral is not a failure. Repeated runs and course changes reuse the day's allowance without increasing it.
- Fixed the normal grading status failing to save after an original answer was bound. After saving a real answer as `answered` and registering an independent observation that created an original-answer binding, keeping the prompt, answer, source and conditions unchanged and only appending grading while changing the status to `graded` was rejected by the save entry point ("the bound original answer must remain in the same course…"), so the state was not written. Workflow states and grading verdicts are now defined consistently: `pending`, `answered`, `submitted`, `graded`, `partially_answered` and `completed` are workflow states that a real attempt can advance through; `correct`, `incorrect` and `partially_answered_correct` are grading verdicts, and unknown strings are protected conservatively as verdicts. Legacy bindings and legacy parent-question bindings are normalized only after their old `fields`/`sha256`/`ancestors` verify against the currently saved state (workflow status is removed from the fingerprint and an existing verdict is added back for protection); a changed draft is never used to re-sign, deleting a binding cannot bypass the check, and damaged legacy bindings are not repaired automatically. One save can advance an older question's grading and register a delayed retest that references the older observation; a failed save writes nothing and leaves the caller's draft unchanged, and repeated validation is idempotent. Historical protection for nested sub-questions is unchanged.
- Upgrade: update the code and keep the `Study/` profile; no manual binding edit, deletion or rebuild is required. Legacy-format bindings are normalized after their next verification through the real save entry point; records already damaged or mismatched before the fix are still never repaired automatically and need manual comparison against a backup.
- Validation: targeted regressions first, then the three complete suites. Targeted (after the fix): `test_daily` 20, `test_planning` 25, `test_learning_loop` 35, `test_study_tools` 9, `test_evidence_bindings` 23, `test_evidence_archive_binding` 10, `test_teaching_evidence_guards` 28, `test_cursor_evidence` 16, `test_state_draft` 10 and `test_teaching_preservation_fixes` 17, all passing; this round adds 26 regressions (`test_daily` +11, `test_evidence_bindings` +12, `test_planning` +2, `test_learning_loop` +1). Three complete suites: 100 setup/entry Python, 561 study Python and 138 Node tests — 799 passed, 0 failures, 0 skips (the earlier same-day migration stage recorded 773, for historical comparison only). Tests use fictional profiles and isolated temporary directories; no real school was contacted, no credentials were read, and no real learning profile was modified.
- Limits: this round validates offline flows and save entry points only; no real school connection, no security audit and no dependency upgrade. Bindings already damaged or mismatched before the fix are not repaired automatically and still need manual comparison against a backup. Normal material archiving preserves original-answer bindings and no longer rewrites historical evidence (see the earlier same-day migration entry and the [update report](docs/UPDATE_REPORT_2026-11-07-deepseek.md)).

### Earlier Git branch release that day

- This release is based on committed fix `2eb4515` and targets `deepseek`, including the historical-evidence and review-option fixes below while retaining Harness-specific implementations. It publishes repository code and documentation only, excluding personal study profiles, configuration and credentials.
- Pre-release recheck: 17 material-version, 10 archive-evidence binding, 34 review-flow and 9 daily-plan tests passed (70 total). That run was a targeted regression check, not a rerun of the full suite; real school login was not tested.
- Existing users should follow the upgrade guide above and preserve their study profiles. The earlier stage does not migrate or repair existing historical records.

### Ported learning improvements, on-demand queries, and Harness-edition fixes

- Before publication, restored the missing November 6 entry on both homepages so their three most recent updates match the full changelog.

This update only changes the `deepseek` branch. The personal local HKU deployment, real learning records and the `codex` edition are untouched. Learning improvements were ported feature by feature from the personal edition (not as a bulk merge), and no Codex file replaced a DeepSeek-specific implementation. Full scope and limitations: [update report](docs/UPDATE_REPORT_2026-11-07-deepseek.md).

- Added same-question answer-exposure checks across review packages and mock papers. Questions are matched by course + prompt + option text + source pages (question numbers and annotations ignored; no semantic guessing). Earlier exposure can be backfilled, a later exposure never invalidates an earlier real independent answer, and existing records are not rewritten.
- Recurring timetables honor validity start/end dates inclusively; explicitly dated makeups are preserved. Overdue non-exam tasks stay priorities only when that day's plan names them and positive unfinished evidence exists; submitted, group-submitted and historical exam items are not reopened, and old plans do not auto-carry.
- Exam-source indexes now retain non-text evidence: images, links, attachments, inline files and edit timestamps. Such changes mark the original requirement as needing re-review while preserving previous sources, URLs, site and course mapping; plain-text legacy hashes stay unchanged.
- After courseware is archived or superseded: old mock papers become stale, new papers reject old sources, and review preparation skips old-source variants. Previously assigned but unanswered old-source questions are marked as source-needing-verification with their budget released, keeping the original prompt, source and history. Real answers completed before the update remain backfillable at their original time; post-update independent ratings are rejected without losing the original record, and assisted practice is still recorded. Material IDs are never silently retargeted.
- Added five on-demand read-only MCP queries (calendar, grades, notifications, resources, site info; 10→15 read tools). Calendar pages by event cursor within a fixed time window and reports `complete:false` with a reason when pagination stalls or hits the page limit, and returns `api_not_enabled` when the school does not expose the API; notifications paginate without being marked read; resources list visible metadata only and never download; grades are limited to the current account; site info is whitelisted and redacted. All go through the read-only allowlist, reserved-parameter protection and redirect refusal. No assignment-submission, posting, notification-read or download tool was added, and the default `continue`/`refresh` collection policy is unchanged. Restart the project MCP connection after upgrading to see the new tools.
- Fixed courseware archiving rewriting historical evidence. A real archive or first installation of a material now updates only the actual location in the `materials` index and real Markdown link destinations, and no longer recursively replaces path strings across learning state. Historical original answers, parent-question sources, `evidence_bindings` and memory-correction `target_snapshot` stay untouched, so a complete save after archiving is no longer blocked by stale fingerprints; fingerprints are not recomputed to mask the rewrite. Archived versions remain readable through their original material IDs; references that only have an old path and cannot determine one version stay flagged for review and are not pointed at a same-named new version.
- Completed `options` support for multiple-choice review questions. A practice variant with `options` (a label→text dictionary whose insertion order is the display order; at least two non-empty keys and values) is snapshotted into the daily package with a deep copy of the verified options. `daily.py run`, public previews and `Today Review.md` show every option in original order as `A. text`, while `answer_check`, answers and feedback still do not appear before answering, and non-pending items never expose options. An existing package keeps its original snapshot even if the bank variant is later edited. An old package without valid options is never repaired from the possibly edited bank: the original question is kept as history, marked `needs_review` with `budget_exempt: true` plus a persistent `options_review_reason`/`options_review_required_at`, its reserved budget is released, another verified unused variant may be prepared, and `needs_preparation` explains when no alternative exists. Incomplete old questions that are marked or still identifiable as choice questions can never receive an independent FSRS rating, and a later bank edit or deletion does not clear the review reason; if the bank is already deleted before the first preparation so that a choice identity can no longer be established, the original record is kept for manual review instead of guessed.
- Fixed numeric course/module ID collisions when one Study profile silently switches school or account. Selection, legacy CLI authorization, library sync, collection, downloads, refresh precheck, chapter checks and login first verify the school/account recorded by the profile and stop without writing when they differ, without re-binding old grants, library ledger or chapter baselines. Reused or explicitly supplied snapshots are also checked, and a `continue` collection stopped by this guard explains the real reason, writes no failed receipt and leaves learning state unchanged. Selection/authorization records `profile_account_scope` and chapter receipts record `account_scope`. Legacy grants without account information are never guessed or back-filled; to change school, copy or create a separate project folder with its own profile and re-run setup and login.
- Preserved DeepSeek-specific behavior: chapter checks that verify only and write a receipt (`chapter-check --section --mode strict`), Harness configuration and chat identity, `course_library_authorization`, `full`/`info_only`, `library-sync`, `subject`/`source_pages`, the LibrarySync ledger and `archive_subdir`, plus state-lock, snapshot and original-answer protection. Codex's existence-based chapter lock and `verify-chapter` entry were deliberately not ported.
- Validation (earlier same-day migration stage): fictional temporary profiles and mock school responses; no real school was contacted. Three complete suites: **100** setup/entry Python, **535** study Python and **138** Node tests all passed (**773** total; 0 failures, 0 skips). On this machine a restricted system temporary directory caused environment-only failures in the setup/entry Python and Node suites; rerunning with a workspace temporary directory passed completely and those failures are unrelated to this change. New focused regressions cover two consecutive archives, first installation, original-answer/parent-question bindings and correction snapshots, complete saves after archiving, reading archived versions by original ID, and options snapshot/order/old-package handling/independent-rating rejection. A read-only audit of the real local learning state and related backups (hashes identical before and after) found no real records affected by either bug, so no recovery was performed; see the [update report](docs/UPDATE_REPORT_2026-11-07-deepseek.md#验证). Real SSO/MFA, fresh-machine installation and school API permissions remain unverified.

## 2026-11-06

### Complete English changelog for DeepSeek Harness

- Added `CHANGELOG.en.md`, covering this branch's complete history, including Harness onboarding, course-level authorization, login fixes, chapter checks, validation results, and known limitations. Both logs link to each other, and the English README now opens the English log.
- Documentation only; Harness runtime behavior and learning records are unchanged. Future changelog updates maintain both languages together.

## 2026-11-05

### Chapter checks without collecting information across the school

- Added the read-only `chapter-check` entry point. It enumerates one course's directory and compares the chapter's existing files. `--mode strict` retrieves chapter files again and compares content hashes, detecting changes even when an instructor keeps the same filename. `--mode quick` checks only server-reported size and modification time and is described as typically taking a few seconds, but can miss same-size rewrites.
- When teaching or revising a chapter already available locally, use `library.cmd context --read-only` for continuation, then `study.cmd ... chapter-check` for that chapter. Receipts are reused within the same chat without collecting submissions, announcements, every forum reply, or calendars across the school. Assignment requirements, deadlines, announcements, study planning, exam preparation, first installation/course changes, or missing local materials still use `continue` for full collection.
- Receipts are written to `Study/_System/ChapterChecks/<course>/<chapter>.json` with per-file status, evidence type (metadata/size/content), and scope. The tool does not write learning state, install files in the library, build indexes, or produce content-review receipts. Matching chapter files do not establish that announcements, assignments, or all teaching information are current, and cannot support a full mock exam on their own.
- Handle results through `verdict`: `unchanged` allows teaching from local files; `changed`/`partly_changed` requires course synchronization and activation of the new version before teaching; `unknown`/`partly_unknown` requires a content baseline or full synchronization; `incomplete` or network failure permits continued local use with an explicit “updates were not verified this time” notice.
- Upgrading requires no profile changes; commands and rules take effect with the code. Example: `.\study.cmd --chat "original-user-chat-id" chapter-check --course moodle-146265 --section "Chapter 8" --mode strict`. See [single-chapter checks](docs/WORKFLOW.md#单章资料核对不触发完整采集) and [course-level authorization](docs/COURSE_LIBRARY.md#只核对某一章不建索引).
- Validation recorded for this update: 14 new Node regressions covering chapter-number matching, metadata/size/content evidence, partial changes and missing baselines, receipt paths, authorization limits, and course-page read failures; 3 Python entry-point regressions; and all 461 `tools/study` tests passed. The local Node suite under `tests/` had 5 environment-related failures because Python child processes could not create lock files in the system temporary directory. Those failures also occurred with this change stashed, so the original validation record identifies them as unrelated to the change.

## 2026-11-02

### Harness chat-based setup and proactive usage guidance

- Integrated release-branch client isolation and automatic material synchronization. Setup configures only Harness; account authorization during chat-based course selection works with automatic synchronization. Checking the current material report avoids downloading again.
- The assistant runs installation, opens school login, lists courses, and saves selections in the current chat by default. CMD menus remain a manual fallback. Structured selection validates the school, account, visible courses, and state version before safely saving course selection and `course_library_authorization` together, preserving existing archiving and indexing.
- After installation, the assistant explains common requests and invites a first action. Guidance can resume, be skipped, or be requested again; it is not repeatedly shown once delivered and does not change subject mastery. The study entry point returns pending-guidance information.
- Retained the fix that resumes authorization when SSO returns to the homepage. After a successful login, course selection continues in chat. `full / info_only` and this edition's `library-sync` remain in use. New authorization for multi-page courses includes all selected pages, and switching accounts cannot reuse the new chat-based authorization grants.
- Upgrades preserve Study, configuration, credentials, and historical materials. Ask “Continue setup” or “Show me how to use this” in the project chat. Offline validation uses isolated synthetic profiles; real school login and Harness automatically creating a welcome chat require separate checks. This workflow does not push changes automatically.

### Course-level authorization and material initialization

- Version 0.4.0 added one-time course-level authorization with automatic initialization and incremental synchronization. The `courses` entry point (`choose-courses.cmd`, or item 6 in `start.cmd`, “Adjust courses and material authorization”) lists courses, then shows the authorization scope. Users choose `1` for full initialization (course information plus download/archive/index), `2` for information only (no material downloads), or `0` to cancel. Confirmation saves `state.settings.course_library_authorization`, recording `mode` (`full`/`info_only`), `moodle_courseid`, `authorized_at`, and `scope_version` for each page. Newly added course pages need new authorization; there are no background refreshes or scheduled tasks.
- Full initialization reuses a complete snapshot less than 6 hours old, or first performs read-only collection with `refresh`, then enumerates, downloads, archives, and indexes materials. Collection and content review remain separate. Later incremental runs use `library-sync` with optional `--course`, `--snapshot`, `--no-snapshot`, `--max-bytes`, `--batch-size`, and `--dry-run`. APIs remain read-only: no quiz attempts, assignment submissions, posts, email, or school-state changes.
- Noninteractive `courses --select … --mode full|info_only --yes [--refresh] [--no-sync] [--chat …]` is for AI use only after explicit authorization in the current chat. `--yes` represents the user's confirmed course scope, not authorization granted by the program itself.
- Downloads include directly accessible slides, assignment attachments, forum attachments, inline images, and downloadable audio/video. Announcements, assignment requirements, and discussion text retain accurate sources. Streaming media, external sites, extra login requirements, and permission-restricted content retain only their entry points and reasons, such as `quiz_questions_require_attempt_not_started`, `external_streaming_not_downloaded`, or `restricted_by_course_permission`.
- Archives retain `Study/<course_id>/<category>/<original-filename>`. Files first enter `_Inbox`, identical bytes are deduplicated by hash, and previous versions move to `_Archive`. Same-name files with different content in the same course/category use `module-<module-id>` subdirectories without overwriting. Identical bytes from different sources record `duplicate_of`; new bytes from the same source establish `supersedes`, preserving source pages and SHA-256.
- Multi-page subjects are merged: MATH1013 pages `146265` (primary) and `142648` (secondary) resolve to `moodle-146265`; ECON1210 pages `146739` (primary) and `146748` (secondary) resolve to `moodle-146739`. Materials use the primary subject's `course_id`, while `source_pages` preserves each page's provenance. `library.cmd search/outline --course` and `library.py sync --course` follow `merged_into` from secondary pages; `read`/`render` use material IDs.
- Per-item status and resumption use `installed`, `identical`, `duplicate`, `already_current`, `failed`, `skipped`, and `blocked` in `Study/_System/LibrarySync/ledger.json`. The same directory holds `latest.json` and the human-readable gap report `资料缺口.md`. After interruption, synchronization handles unfinished, added, or changed items and retries failures without downloading completed items again.
- Download success (`installed`) and parsing success (catalog `indexed`) do not establish content review or mastery. OCR/extracted content remains unverified; audio/video and archives are stored without parsing, with reasons recorded in the index. After installation, `library-sync` rebuilds the catalog, and the assistant locates materials by course, file, and page/unit before reviewing original pages.
- Data boundaries remain: no browser Cookie reading/export; tokens stay out of chats, state, logs, and Git; downloads remain in local, Git-ignored `Study/` and are not uploaded. Personal profiles from other computers are not imported.
- Restricted shells, including a DSH sandbox, may block Node child processes, DPAPI decryption, or downloads. Real synchronization should run in a normal terminal through `start.cmd`, `choose-courses.cmd`, or an approved full-access command.
- Upgrades preserve `Study/`, learning records, and authorization. Run `courses` again to confirm scope; no profile rebuild is required. Existing materials are not migrated in bulk, and new synchronization installs incrementally. See [course-level authorization and automatic synchronization](docs/COURSE_LIBRARY.md).
- The original validation record reports real full initialization across 7 selected course pages: 369 Moodle modules, 304 directly downloadable file candidates, 44 text captures, and 153 explained skips. Of the 304 file candidates, 300 distinct files were installed, 4 were deduplicated, and 0 failed; the initial download was about 214 MB. The final two subsequent runs each downloaded 0 files, marked all 348 items `already_current`, and reported 0 failures. The local index contained 394 material rows, 4623 units, and 4358 text-bearing units, all materials marked `indexed`. Real-material searches in MATH1013, ACCT1101, ECON1220, ECON1210, and CCGL9019 located specific files/pages, whose extracted text was reread to confirm keywords; successful search was not counted as content review or mastery. New `tests/course-library.test.mjs` and `tools/study/test_course_library.py` regressions cover authorization writes/merges, repeated-authorization idempotency, batch installation, same-name separation, deduplication, same-source version archives, merged-subject search, gap reports, retry, and blocking unauthorized courses. Results were `npm test` 56/56, Python `tests/` 78/78, and Python `tools/study/` 437/437. Automated tests use isolated temporary directories and fictional data without school access or personal records. Real school-server absence of writes was not verified; the client uses only read-only interfaces and exposes no write functions. Restricted shells require a normal terminal or approved full-access execution.

### Login browser startup fixes

- If school login returns to the Moodle homepage, the login tool resumes the original authorization request once in the same browser window, preserving session-validation parameters. Another return to the homepage produces an explicit error rather than an endless retry. Cookies are neither read nor exported. Local Chrome simulations cover direct authorization, return to the homepage after login, and repeated homepage returns.
- `connect.cmd` uses the project's Git-ignored `tmp/auth-runtime` directory for the current login process, avoiding failure before the browser opens when the system temporary directory is unavailable. It does not change global Windows environment variables and gives a clear error if directory creation fails.
- `connect.cmd` defaults to Chrome. Use `connect.cmd --browser msedge` to switch temporarily.
- After updating, double-click `connect.cmd` again and complete school login yourself. Browser reinstallation and learning-record migration are unnecessary. The original temporary-directory error was reproduced, and blank Edge/Chrome browser startup was verified after the fix; real school login still requires the user.

### Client release branches

This branch targets `deepseek`; both homepages and onboarding describe DeepSeek Harness.

- Created local `codex` and `deepseek` release branches. Setup, diagnostics, and onboarding follow each branch's `client-target.json`; environment variables no longer select which client to configure. Codex setup does not write Harness configuration, and DeepSeek initialization does not generate Codex configuration. Existing settings and learning data are retained.
- Both editions retain common download, indexing, memory, and state protocols. Shared fixes use separate commits and are validated for each client. See [branch maintenance](docs/BRANCHES.md).
- Preparing branches does not mean they have been pushed or that the default branch has changed. Shared-engine validation reuses earlier results; this change additionally checks client isolation, configuration preservation, and diagnostics.

### Full course initialization

- Course selection offers full initialization or information-only synchronization. Full authorization binds the school, account, and selected course pages. Saving it immediately collects information, downloads accessible materials, archives original filenames, and builds a search index. Later normal refreshes fill gaps incrementally without asking for every file. Upgrading an old profile does not automatically grant authorization.
- Coverage includes course resources/folders, assignment instruction attachments, and forum text, attachments, and inline images from all authors. Same-name files from different sources remain separate; updates preserve previous versions and learning references. Streaming downloads and interruption checkpoints support large files and avoid repeating all work.
- Downloading, indexing, content review, and mastery are reported separately. Permission limits, external links, unopened pages, and parsing failures remain explicit gaps. Material failures do not alter saved collection snapshots or learning progress, enable background jobs, or delete archives.
- After upgrading, run `choose-courses.cmd` to confirm courses and modes. See [course content synchronization](docs/COURSE_CONTENT.md) for scope and recovery. Validation uses isolated synthetic courses and simulated networking, not a verified full download from a real school.

### DeepSeek Harness (DSH) integration

- The same learning engine can run in the DeepSeek Harness desktop client while preserving the Codex local-project entry point. Added [DSH guidance](docs/DSH.md) and example configuration in `templates/dsh/`.
- Added the one-time, reversible command `python tools/onboarding/dsh_integration.py install`. It appends MCP stdio and `customSkillDirs` configuration to a Harness profile, preserves existing settings, creates a backup, and updates only its own marked section on repeated runs. It does not overwrite a same-name MCP entry or user-defined skill configuration. In a Harness environment, `setup` performs this merge automatically.
- Chat identity uses Harness's stable session. For `study.cmd`/`session.py`, resolution is explicit `--chat`, then `STUDY_CHAT_ID` (an agreed session name), then the Harness main-chat `DSH_SESSION_ID`. Bare UUIDs from internal sessions are rejected; subtasks must pass the original ID and `--subtask`. IDs are neither randomly generated nor shared as a single constant across all chats. `refresh` uses the same resolution.
- Local diagnostics recognize both Codex and DSH configuration instead of treating a missing `.codex` directory as incomplete setup. The read-only `chat-id` command shows the identity source.
- Data protections remain: ordinary questions and memory lookup/correction stay offline; study startup and explicit refresh use the existing session/snapshot protocol; state writes use shared locking, version checks, deduplication, and historical-evidence protection.
- Validation added 17 Python regressions: 6 identity cases, 8 configuration merge/backup/idempotency/conflict cases, and 3 CLI cases, plus 6 Node identity regressions. Full offline results, actual Harness-chat validation, and unverified areas are recorded in the relevant commit notes. Tests use isolated temporary directories and fictional data, without personal learning profiles.

### DSH configuration merging and diagnostics fixes

- Profile patches now use structured parsing through PyYAML, installed by setup. Comments and commented-out examples are no longer treated as configuration. A valid top-level empty list `[]` is normalized before appending rather than producing invalid YAML.
- Before replacing a file, `install` parses and validates the complete result, including the top-level list, entry structure, and path targets. Parse or validation failures preserve the original without changes beyond a backup. Repeated runs report `unchanged` when content already matches. Added `--update-existing` to update only the old deployment's `run.mjs` path after confirming ownership, preserving other settings and comments; existing `!!js` expressions and multiline folded paths are supported.
- Diagnostics inspect actual `moodle_study` MCP and skill-directory entries and distinguish valid configuration, same-name conflicts, parsing errors, and missing dependencies. `configuration_ok` and `runtime_loaded: unverified` are reported separately: correct configuration does not establish runtime loading.
- Regressions cover empty lists, document markers, misleading comments, preservation after parse/validation failure, idempotency, `update-existing`, and configuration/runtime distinctions. The learning engine and state protocols are unchanged.
- Fixed two `--update-existing` defects. Already-satisfied skill configuration no longer returns `unchanged` before applying an MCP path change; success is reported only after writing and checking the result. Updates replace only the target `moodle_study` entry's `args`, preserving all other entries, `!!js` expressions, and settings byte for byte. Added regressions for actual writes with existing skills, unrelated matching paths, flow/folded arguments, and non-writing dry runs.

- Added `library.cmd memory --course ID` for course progress, teaching position, and original-record locations; `--query` searches across chapters. Old records, user statements, and independent performance remain distinct, and a search hit does not establish mastery.
- History search ranks phrases, explicit Chinese/English aliases, and keywords, prioritizing topic matches and returning evidence. Course isolation, hidden answers, result limits, and the distinction between “no match” and “not studied” are retained. No additional model or online search is used.
- Added `memory-correct`, appending a correction only when the user actually makes one. Shared locking, state-version checks, and target-content validation reject stale overwrites; repeated requests do not duplicate entries. Original answers, grades, and mastery remain unchanged. Later continuation checks corrections first.
- New and old records remain compatible without bulk reassessment or rewritten history. After upgrading, ask “Where did I get to in this course, and what is that based on?” See [learning memory](docs/MEMORY.md).
- Validation: 13 new memory tests and the existing 471 offline regressions passed. Coverage includes aliases, course isolation, hidden original answers, appended corrections, stale-write rejection, protection against deleting corrections in normal saves, and independent-process reads. Public tests use fictional materials. A new-process read does not verify every assistant behavior in a new chat.

### Memory audit fixes

- Memory review reuses teaching-continuation selection, returning newer nested teaching positions and accurate source paths while retaining the top-level original-record entry and omission hints. This avoids repeating an obsolete next step.
- Matching observations include direct and chained `corrects` entries even when corrections lack query terms or fall outside ranked results. Mastery is not rewritten automatically.
- New corrections retain an original-record snapshot and content fingerprint, preserving traceability after ongoing study and repeated saves. Snapshot answers are hidden by default and are not treated as current records. Missing snapshots in old profiles are not fabricated.
- Multi-concept queries retain keywords not covered by aliases. Independent short and long terms in the same sentence both participate in retrieval; complete terms are not reduced to generic fragments.
- Updating code is sufficient; profiles need not be rebuilt. Existing answers, grades, and corrections remain intact. See [learning memory](docs/MEMORY.md).
- Validation: 27 targeted regressions, including cases that failed before the fixes, and all 511 offline tests passed: 43 Node, 42 onboarding/authorization, and 426 study-tool tests. Coverage includes continuation selection, corrections beyond result limits, snapshot traceability across saves, legacy compatibility, multi-concept queries, and hidden answers. Fictional isolated profiles do not establish reliable recall for every possible chat.

## 2026-11-01

### English and Chinese product documentation

- `README.md` provides the English homepage and `README.zh-CN.md` the Chinese version, with language links at the top. Both cover the same capabilities, setup steps, privacy, and known limitations.
- The pages first explain the course library, instructor requirements, and learning records across chats, then provide example requests and installation guidance. They describe which repeated preparation is reduced while retaining realistic limits on course coverage and model judgment.
- Installation commands and troubleshooting are collapsed, with existing detailed guides reused. The setup wizard and most in-depth documentation remain Chinese. This is a documentation-only change without profile migration.

### Study startup scope and connection diagnostics

- Project introductions, tool comparisons, and general questions skip learning state and Moodle. The study entry point runs when a chat first enters course study, revision, or planning. Connection repair performs only necessary diagnostics and does not consume the later first-collection opportunity. The assistant selects scope from the request; directly running `continue` still performs initial collection under the rules at this date.
- Connection diagnostics distinguish DNS, timeout, certificate, HTTP, permission, authorization-expiry, unexpected-response, and Windows decryption failures. Entry points, onboarding diagnostics, and collection failures return safe categories and recovery advice without exposing underlying URLs, credentials, or response bodies. Network errors no longer immediately suggest resetting credentials; decryption errors first require checking the Windows account and execution environment.
- Session reuse, independent snapshot binding, separate collection and review, explicit retry after failure, subtask restrictions, and all record protections remain. No network permissions or background jobs are added.
- Validation: 18 new regressions and all 471 offline tests passed: 43 Node, 42 onboarding/authorization, and 386 study-tool tests. Coverage includes classification, sensitive-output isolation, no automatic retry, and normal first-study collection after read-only diagnostics.
- Upgrades preserve `Study/`, local configuration, and authorization files. Update code and rules without rebuilding profiles; initialization preserves custom preferences. Isolated fictional profiles and simulated connections do not prove a real school-network issue is fixed.

### ChatGPT onboarding guidance

- The README now introduces the Windows ChatGPT desktop app and Codex local-project entry point before offering copyable installation instructions. ChatGPT can handle environment checks, dependencies, and configuration; the user participates in school login and course selection.
- Added concrete steps for selecting Codex mode and local execution. Browser-assisted login remains experimental; real school login and complete online installation on a new computer need further validation. A fallback connection method is provided.
- Manual installation commands and tool details move to the detailed guide. The homepage retains preparation, completion criteria, help with errors, everyday requests, and upgrades.
- Clarified supported ChatGPT usage, account-permission requirements, and local-data boundaries while retaining known issues. Documentation only; no program behavior or personal records changed.

### Release summary

- **Learning-record protection:** full-state saves prevent tasks, deadlines, and discussion records from being emptied or reduced to IDs. Normal completion, workload changes, rescheduling, and additional sources remain allowed.
- **Answer-material detection:** explicit answer flags are retained, and `sol` / `sols` filenames are recognized. Installing identical content can add answer protection without changing material IDs, paths, originals, or historical references.
- **Review-budget adjustment:** after an actual overrun, questions that no longer fit the remaining budget are deferred. Actual time is preserved; deferral is not an incorrect answer and does not add to the day's budget again.
- **Validation:** 11 new regressions and all 453 offline tests in the release repository passed. Normal updates, failure retries, and cross-date timing were also checked with fictional materials and isolated profiles, without connecting to school or changing personal records.
- **Upgrade:** preserve `Study/`, configuration, and authorization and follow the [upgrade steps](docs/新手使用指南.md#从旧版更新). No profile rebuild or bulk migration of old materials is required. Identical-content answer protection is added later during an authorized installation that confirms the answer classification.
- **Known limitation:** after a mock exam records an answer reveal, backfilling an earlier reveal time may still be ignored as a duplicate. This edge case was not fixed in this update. Such backfills need manual checks of reveal time and independent-answer eligibility; tool flags alone cannot establish mastery.

## 2026-10-31

### Audit fixes for learning records, answer materials, and review budgets (based on 0.3.1)

- Full-state drafts now protect tasks, deadlines, and discussion records together. They reject emptied lists, ID-only records, and rewritten original sources while allowing normal completion updates, deadline changes, new evidence, and appended records.
- Material installation retains confirmed answer flags and detects `sol` / `sols` filenames. Reinstalling identical content can add newly confirmed protection; later missing or negative flags do not automatically weaken it. Material IDs, paths, content, and learning references remain unchanged.
- Actual time is retained when an answer takes longer than expected, and remaining questions that no longer fit are deferred. Late answers to older questions affect only the actual response date. Deferral is not an incorrect answer; the same question can continue the next day.
- Upgrades preserve `Study/`, configuration, and existing records without rebuilding profiles. Saving answers or generating review questions applies the budget fix. Old materials are not rewritten in bulk; protection is added at the next authorized installation that confirms the same content is an answer resource.
- Regressions use fictional materials and temporary profiles to check normal updates, answer visibility, repeated installation, backups, and preserved references. Test records do not replace personal learning evidence.

### Teaching continuity and cross-chapter evidence fixes (based on 0.3.1)

- A second audit fixed 6 classes of issues: answer-key/grading-area evidence and bypasses of parent-question help conditions; binding observations to the actual answer topic, time, and pass result; historical retrieval of real reviews by session; locating original answers after question archival and course reordering; exact preservation of `0`/`False`, empty answers, and source types; and lossless chapter transitions for legacy dictionary-form teaching history.
- Added read-only `evidence-source --course COURSE_ID --observation OBSERVATION_ID` to inspect the original answer bound to an observation. Saves maintain compact content bindings without copying answers. Unbound legacy observations remain marked for review instead of treating a new question at an old path as original evidence. Later grading is allowed; corrections to original answers or conditions must preserve the original record.
- Fixed pending questions and next steps in historical answers or old cursors being treated as current continuation. Previous teaching positions are preserved before topic/chapter changes; original answers remain protected by existing check history. Review needs for old chapters are stored separately.
- Added `tools/study/library.cmd evidence --course COURSE_ID --query "topic"` for read-only lookup of notes, answers, and feedback across a course's chapters. It returns bounded candidates and original-record paths. No match does not mean “never studied”; it does not assess mastery, connect online, write records, or run maintenance.
- Optional appended observations and coverage checks distinguish not found, explained, completed with help, completed independently, and delayed retesting. A user saying a topic was covered does not establish independent mastery. Full-coverage claims need scope evidence from original materials, actual assignments, and history, and still do not establish mastery.
- Uploaded materials are handled according to the current request. When assignments use older knowledge, history is checked first; reteaching follows actual difficulty or an explicit request. Corrections to past experience update plans rather than treating “not covered in this chat” as proof of an omission.
- Upgrades preserve `Study/` and configuration without rebuilding, bulk migration, or regrading old records. New fields are used only for real new events or verified corrections. AI judgments still require original materials and actual answers; structural validation cannot guarantee effective teaching.
- See the [teaching-continuity regression record](docs/TEACHING_CONTINUITY_TESTS.md) for validation scope.

### 0.3.1: onboarding audit fixes

- The startup wizard and installer share Python-selection rules, and the wizard passes its validated interpreter. Having both an old `py` launcher and a newer Python no longer causes the wrong version to be selected.
- Setup checks pip first. Interrupted environment creation or missing pip triggers an offline recovery attempt with Python's built-in `ensurepip`. If recovery fails, the wizard clearly asks for environment repair instead of repeatedly recommending the same failed installation step.
- Local diagnostics within one wizard run no longer clear a known online failure. Reused results are labeled; changes to the school URL or credential file mark the connection unverified again. Only a successful explicit recheck replaces a failure. Connection diagnostics are not persisted to disk, and no background networking is added.
- The actual study timezone is validated, fixing readiness checks that accepted the same typo in two places. Existing dates are not changed automatically.
- School-authorization decryption failures now have a fixed error identifier while remaining compatible with the older Chinese error. Guidance correctly asks for reconnection under the current Windows account without echoing credentials.
- Continue using `start.cmd` / `diagnose.cmd` after upgrading, preserving personal directories without rebuilding the profile. See [phase-two validation](docs/PHASE2_TESTS.md) for tests and replay limits.

### 0.3.0: phase-two onboarding and diagnostics

- Added the Chinese text wizard `start.cmd`, reusing existing setup, browser login, official-token, and course-selection tools. It resumes from existing files without requiring a new profile after interruption.
- Added `diagnose.cmd`, which defaults to local checks. An explicit online check verifies school connectivity without refreshing courses. Chinese diagnostics distinguish missing environments, damaged profiles, expired authorization, access restrictions, network problems, and pending review.
- Diagnostic summaries do not echo personal data, paths, credentials, or raw exceptions and are not uploaded automatically. A damaged existing profile blocks wizard installation, connection, and course-selection writes.
- Added a one-page usage guide covering new chats, compaction, saving, refreshing, original-page checks, independent answers, and preserving records during upgrades. Onboarding no longer requires a manual refresh followed by another refresh in a new chat.
- After upgrading, double-click `start.cmd`. Existing chats can continue directly without rerunning the wizard each time. See [startup guidance](docs/START.md). This still requires a local AI client and is not a standalone chat app; real school SSO and new-student experience need further testing.

**Chat continuation, save protection, and interruption recovery**

### Added

- `study.cmd` provides a unified entry point for continuation, daily review, update checks, and status. It returns local continuation first and reuses the current check within the same original chat.
- Each collection stores its own school snapshot. Collection completion and AI review completion are distinct; review receipts bind the original snapshot, course/account scope, and state version.
- Local phase timings help locate delays without recording questions, answers, or credentials.

### Fixes and improvements

- Learning writes, course selection, and material archiving share locking and reject stale drafts, preventing supported tools from overwriting records concurrently.
- Page generation can recover after a successful save. Retrying the same event does not duplicate feedback, answers, or FSRS ratings.
- Internal tasks do not start another check. Failures are not retried for each follow-up, and older runs cannot replace a newer run already started.
- Read-only continuation is separated from archive maintenance. Progress, mistakes, original-page references, and authorized 30-day cleanup rules remain intact.

### Same-day audit fixes

- Drafts that retain only IDs, clear progress details, or remove mistake sources are rejected. Original answers, review history, and committed events are protected while normal learning updates remain allowed.
- Interrupted material transactions recover before normal saves so later feedback cannot invalidate recovery. User-edited originals are not overwritten.
- Each run fixes its collection scope and validates snapshot courses, preventing source mismatches if course selection changes during collection. Scope changes correctly show pending review.
- Failed school checks retain old exam evidence and mark it for review, restoring exam-generation safeguards.
- After the parent process stops, a snapshot completed later by its child can still be recognized as belonging to the same run, avoiding another network collection during continuation.

### Second audit fixes

- Unified save rules for all four current-question fields. Existing answers must first be preserved in the same course's history, preventing answer loss during question changes; unanswered questions and normal answer additions remain supported.
- Standalone exam registration, source synchronization, and the unified entry point share snapshot validation. Older chats cannot use a different command to restore obsolete exam requirements.
- Added positive and negative regressions for interleaved chats, modified snapshots, normal re-registration, current-question aliases, and answer archival.

### How to use after upgrading

Preserve personal directories and update code. The assistant passes the original user-chat ID as described in the [unified entry point](docs/SESSION.md). Clients without a reliable ID must explicitly choose and retain a session name. No profile rebuild or background refresh is required.

This version tests concurrency, retries, and interruptions with fictional data. End-to-end speed has not been validated against a real school network or every client; successful collection still requires careful content review.

## 2026-10-30

**Material updates and archives**

After an instructor updates materials, a confirmed new version returns to the normal course directory for everyday search and revision. Existing mistakes still point to the original file used at the time.

**Added**

- Material version management: after confirming the relationship between versions, the old version moves to an archive while preserving original files, page references, and history.
- Optional archive cleanup: once explicitly enabled, archives are retained for at least 30 days and checked during later use or synchronization. Files with note/mistake references, local edits, or an unverifiable replacement are retained. There is no background scheduler.
- Historical viewing: old versions can be opened explicitly when checking earlier questions. Old page numbers and answer records are not silently applied to new versions.
- A unified changelog, four earlier updates backfilled, and a Recent updates section on the homepage.

**Fixes and improvements**

- Fixed same-name updates being placed in `Versions` while old files remained searchable in the normal directory.
- New files are validated before switching versions. Corrupt downloads, unconfirmed relationships, or locally edited originals block replacement.
- Migration updates file paths in learning state and Markdown references while preserving progress, mistakes, actual answers, and hidden-answer defaults. Interrupted work can recover; newly detected local edits preserve the current situation for review.

**How to use after upgrading**

After updating code, ask: “Enable material version management, verify and archive existing old versions, retain them for 30 days, and check during use. Clean up only files without references or local edits whose replacement is verifiable. Preserve my progress and mistakes.” Cleanup requires explicit consent and is not enabled by a code update. At this release, ordinary Moodle refresh still did not download files automatically. Same-name supplementary notes or newly created Moodle resources need review; a filename alone cannot establish an old version.

[Feature commit a7d05c6](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/a7d05c632811852fea12479afe9a5a410bcef4da) · [Version management](docs/MATERIAL_VERSIONS.md)

## 2026-10-23

**Start revision faster, with less repeated waiting in the same chat**

Previously parsed, unchanged materials can be reused. When continuing a course, the assistant reads the current study position before locating original pages as needed.

**Added**

- A local material library for search, complete-page reading, chapter outlines, and original PDF-page viewing. Only affected files are reparsed when material content or parser versions change.
- Course-continuation summaries retaining current tasks, pending questions, and teaching position, with full history available on demand.
- Optional local OCR to help locate scanned content. Recognition results are labeled separately and still require checking the source image.

**Fixes and improvements**

- Replaced a full Moodle refresh on every message with one check at the start of each new chat and reuse within that chat. “Refresh Moodle” or “Check the latest information” can still request an immediate check.
- Ordinary follow-ups without new learning records no longer repeatedly generate plans. Actual answers, mistakes, and progress feedback continue to be saved.
- Read-only Moodle collection can process a small number of independent requests concurrently, reducing sequential waits while retaining forum pagination, reply-count, and collection-failure checks.
- File or cache validation mismatches block stale content and prompt rebuilding the affected cache.

**How to use after upgrading**

Update code and run `setup.cmd` to install new dependencies and build caches for existing materials, then have the assistant read the updated rules. Initial parsing still takes time; later, simply ask to continue revision. Explicitly request a refresh for newly posted instructor information; the chat does not monitor it in the background.

[Feature commit 640a590](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/640a5900b9abbfa7dfe236f591fe6a0f6f65c122) · [Material caching and faster startup](docs/LIBRARY.md)

## 2026-10-16

**Built-in self-study tutoring guided by actual performance**

A common tutoring workflow explains and demonstrates when foundations are weak, allows independent attempts during practice, and provides help based on actual difficulty.

**Added**

- A self-study tutor skill bundled with the repository, requiring no separate installation.
- Graded hints, from direction to key clues to a full solution, to locate conceptual, reasoning, or calculation difficulties.
- Feynman explanations, changed-condition questions, counterexamples, and unaided quizzes that check application through new independent answers.

**Improvements**

- Hearing an explanation, completing work with hints, and independent mastery are explicitly distinguished. Familiar material can move faster; weaker areas receive further practice.
- Short recall and retests are suggested for days 1, 3, and 7 within the existing review budget, without additional background reminders.
- Explicit requests for direct answers, complete explanations, or administrative tasks are honored without forcing practice.

**How to use after upgrading**

Have the assistant read the new project rules, then ask “Teach me from the basics” or “Check my understanding with a new question.” These are AI tutoring conventions, not a claim that the program can automatically establish mastery.

[Feature commit f731766](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/f731766ee66a752d0196c0a679fc39cb5afc59f2) · [Tutoring approach](docs/TUTORING.md)

## 2026-10-11

**Connecting mistakes, revision, feedback, and exam preparation**

Learning records now support concrete review question sets, later practice, and checks for missing materials and independent-answer evidence before exams.

**Added**

- Daily review questions, actual answer records, and hint/answer-exposure tracking. Independent answers can schedule later retests.
- Learning feedback and weekly reviews recording completed work, actual time, and difficulties for future planning.
- Exam coverage checks and mock-exam workflows. AI drafts and checks original questions against announced instructor requirements and local materials; tools validate structure and sources. Papers and answers are separate, and graded mistakes feed later revision.
- An explicitly authorized download entry point for specified resource modules in selected courses, saved first to the local inbox for review.

**Fixes and improvements**

- Plan checks now cover date, state version, and content to reduce stale-plan reuse. The same day's review budget is shared across runs instead of added repeatedly.
- Changed instructor guidance or material sources prompt rechecking existing mock exams. Incomplete requirements distinguish full-scope simulations from focused practice; assistant choices are not presented as official requirements.
- Expanded redaction of login parameters in collection results to keep sensitive fields such as `sesskey` out of ordinary evidence.

**How to use after upgrading**

Ask “Start a 15-minute review” or “Prepare a mock exam using the instructor's announced scope and question formats.” If no usable questions exist yet, the assistant must first prepare and verify them against materials. Unrecorded past independent tests are not fabricated as mastery evidence.

[Feature commit 4a44315](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/4a44315b9e5ed6f1a3ba799367e70e1ad011a142) · [Exam preparation](docs/EXAM_PREP.md)

## 2026-09-22

**Initial release: a local Windows Moodle study assistant**

Installation and usage start from an empty profile, with each student using their own school account and AI client.

**Added**

- Double-click entry points for installation, school connection, course selection, and refresh, plus a Chinese beginner guide.
- Read-only collection of courses, assignments, submission records, calendars, and forum discussions for AI review.
- Local learning state, course materials, question/mistake records, review scheduling based on independent answers, and mathematical calculation checks.
- Personal data is stored separately from public code. Users complete their own school login, local tokens are encrypted, and new installations contain none of the author's course materials, accounts, or learning profiles.

**Getting started**

Follow the [beginner guide](docs/新手使用指南.md) to install and connect. The school must enable the relevant Moodle services. Browser-assisted login remains experimental. The project requires your own AI client and does not include a model or subscription.

[Initial commit ec32b60](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/ec32b6077facd4734f00a330330c42487de6cab3)
