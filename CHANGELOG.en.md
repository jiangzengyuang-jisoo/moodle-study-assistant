# Changelog

**English** · [简体中文](CHANGELOG.md)

This log records how each update affects studying. Dates follow code commits in Hong Kong time (UTC+8). Earlier entries were reconstructed from commit history on 2026-10-30; they do not imply that separate installation packages were released on those dates. Entries describe behavior at the time of each update; later entries may supersede it. Linked detailed guides are primarily in Chinese.

Existing users should first read the [upgrade steps that preserve learning records](docs/新手使用指南.md#从旧版更新). Downloading new code does not automatically update an existing folder on your computer.

| Date | Main changes |
| --- | --- |
| [2026-11-12](#2026-11-12) | Consolidate review and file writes; skip unchanged pages |
| [2026-11-10](#2026-11-10) | Unify progress and verify saves; preserve practice and Feynman checks after important concepts |
| [2026-11-08](#2026-11-08) | Publish audit fixes for remaining study capacity and bound-answer grading |
| [2026-11-07](#2026-11-07) | Fix remaining-time limits and grading-state saves; preserve archived evidence and review options, with scoped queries and chapter recovery |
| [2026-11-06](#2026-11-06) | Complete English changelog, language links, and English README navigation |
| [2026-11-05](#2026-11-05) | Strict checks of current chapter files before teaching, same-chat reuse, local continuation, and task-scoped planning and notice queries |
| [2026-11-02](#2026-11-02) | Chat-based setup and course selection, proactive usage guidance, authorized material sync, memory recall and corrections, and client integrations |
| [2026-11-01](#2026-11-01) | Bilingual homepages explaining the course library and ongoing study; narrower startup scope, connection diagnostics, audit fixes, and known limitations |
| [2026-10-31](#2026-10-31) | Protection for tasks, deadlines, and discussions; hidden answer materials; review workload adjusted after actual overruns; teaching continuity and Chinese onboarding |
| [2026-10-30](#2026-10-30) | Current material versions for daily use; archived originals with historical references; optional cleanup after at least 30 days |
| [2026-10-23](#2026-10-23) | Reusable material parsing and fewer repeated Moodle checks within a chat |
| [2026-10-16](#2026-10-16) | Built-in self-study tutor with graded hints, Feynman explanations, transfer practice, and independent checks |
| [2026-10-11](#2026-10-11) | Daily review questions, learning feedback, weekly reviews, exam coverage checks, and mock exams |
| [2026-09-22](#2026-09-22) | Initial release: Windows installation, school account connection, course information, and learning records |

## 2026-11-12

### Review and file-write simplification (Codex)

- Remove legacy question selection and review-page implementations. `review.py due` uses the current preview workflow and `render` uses the unified page writer. The retired `record` command directs users to `daily.py attempt`, including when an older archive has no learning-loop flag.
- Share atomic file writing across study state, library caches, material versions and pages. Keep existing locks, recovery, snapshot validation and study timezones.
- Leave unchanged pages untouched and reuse rendered plan content during freshness checks; this reduces redundant work without claiming an overall speed improvement.
- Upgrade: keep your existing learning archive. Old answers and FSRS history remain intact; sources or verified variants that are missing are reported as needing preparation, not replaced with legacy prompts. See [tool instructions](docs/TOOLS.md).
- Validation: 573 study-tool tests, 92 Python onboarding/entry tests and 130 Node tests passed. Rerun with project-local temporary directories to avoid system temporary-directory permission restrictions. Tests use synthetic data and temporary directories; no live school login or download was performed. No DeepSeek branch changes.

## 2026-11-10

### Teaching progress and verified saves (Codex)

- Course overview, course continuation and memory queries share one teaching-position selector. Assignment scope, recorded teaching position and mastery evidence remain separate. Conflicts require review; neither the highest chapter number nor the time an old lesson was saved determines current progress.
- Add `library.cmd teaching-save --course ID` to append learning events with actual occurrence times and chat/turn sources under the shared lock. Saves are read back and verified; retries are idempotent and stale requests cannot overwrite state. Existing answers, scores and FSRS history remain unchanged.
- Answered, skipped and answer-shown questions no longer remain current pending prompts. New original answers retain assistance conditions and work with existing source verification; unknown conditions do not imply independent mastery.
- On progress queries, user corrections or conflicting records, inspect relevant unreviewed chats when tools are available. Titles only locate candidates; homework answer delivery does not establish a systematic teaching position. Report the recorded scope when chat access is unavailable.
- Validation: 130 public Node tests, 92 entry-point Python tests, 561 study-tool tests and 85 final targeted regressions passed; 324 local tool tests and 67 final targeted regressions passed. Tests use synthetic records. Read-only checks on a real profile confirmed agreement across all three readers and unchanged files during verification. Chat review still requires the assistant; a save receipt cannot prove that conversations which never invoked saving were recorded.
- Usage: upgrade while preserving learning records, then read the [teaching continuity guide](docs/MEMORY.md#教学接续保存与漏记检查). No profile reset, background task or automatic Moodle refresh is added. The DeepSeek branch is unchanged.

### Teaching instructions and practice continuity (Codex)

- Interpret continuation, acknowledgment, help and topic changes using intent, teaching stage and scope. Ambiguous continuation does not automatically skip questions or reveal answers; clarify at most once when necessary. Skipping or solving one question does not disable later practice.
- Alternate explanation and practice by default and check for prolonged explanation without attempts before advancing. Keep Feynman checks after important concepts, with flexible timing and related content grouped together. Respect requests for full explanations, direct answers and topic changes; record coverage separately from mastery.
- Validation: review simulated continuation, equivalent skip requests, pending-question ambiguity, help and chapter-wide explanation requests; check document changes and references. These are instruction checks, not evidence of improved learning. No runtime code changed or code tests rerun.
- Usage: follow the upgrade steps that preserve learning records, then have the assistant read the updated repository skill and tutoring rules on the Codex branch. No learning-profile reset is needed. The DeepSeek branch is unchanged.

## 2026-11-08

### Chinese and English PDF tutorials

- Replace relative PDF links and their `?raw=1` redirects with full, branch-specific direct-download URLs; add GitHub preview links and save instructions. Validate all four public download URLs by fetching the complete PDF, checking HTTP 200, 14 pages and matching SHA-256 hashes against the committed files.
- Publish the finalized Chinese tutorial and an English adaptation, each with 14 pages, with download links near the top of both READMEs.
- Both cover Codex and DeepSeek Harness, the edition recommendation for Pro users, and separate topic chats within the same workspace. The English edition translates complete setup prompts and explains Chinese UI screenshot labels.
- Validation: Checked PDF text completeness, page counts, bookmarks, links and rendered layout. This is a documentation-only addition: no runtime code changed or code test suites rerun, and no fresh-machine setup or real school sign-in was repeated.
- Download the tutorials from the README; no upgrade or reset of an existing study workspace is needed.

### Audit-fix publication

- This publication uses `5bf6a51` as its code baseline and targets `codex`. Plans, official review packages and read-only previews share remaining capacity, stop adding questions when time is exhausted and avoid deducting review time already included in cumulative feedback twice.
- Bound answers can advance through grading workflow states. Verified legacy and parent-question bindings remain compatible while answers, sources, help conditions, grading verdicts and unknown states remain protected. Bilingual documentation distinguishes repaired normal archiving from old records requiring review.
- Validation: Reused the full results for the unchanged code commit from November 7: all 759 tests passed (130 Node, 92 Python setup/entry, 537 Python study). This publication adds documentation only; the full suites were not rerun.
- Upgrade the matching client branch while retaining `Study/`, configuration and credentials; do not manually delete or rebuild bindings. Previously damaged records still need individual comparison with backups. Only code, tests and documentation are published, excluding personal profiles. Offline checks do not establish real school SSO/MFA, fresh-machine setup or end-to-end teaching quality.

## 2026-11-07

### Workflow consistency fixes after audit verification

- Plans and review selection share the same remaining-capacity rules. Once cumulative study time or explicitly non-overlapping sessions exhaust the day's budget, no new review questions are assigned. Unstarted questions are deferred to fit remaining capacity; completed answers and exposure records remain intact. Review time already included in cumulative feedback is not deducted twice, and ambiguous feedback is not summed.
- Bound answers can advance from `answered` to `graded` and receive additional grading notes. Before adapting existing answer or parent-question bindings, the tool verifies the current original against its old fingerprint. Original answers, sources, help conditions and existing grading verdicts remain protected; bindings are not deleted to bypass checks.
- Both homepages and the update report now distinguish repaired normal archiving from historical records already damaged by older versions, which still require individual comparison with backups.
- Validation: all 759 public-edition offline Windows tests passed (130 Node, 92 Python setup/entry, 537 Python study), with no failures or skips, including 21 added regression tests. All 71 related budget tests passed in the personal installation. Tests affected by system temporary-directory permissions were rerun using an isolated project directory. The full run caught a `pending → answered` compatibility omission, which was repaired and rechecked.
- Update the code while retaining study profiles; no manual binding changes are needed. Both fixes apply to public `codex`; the personal installation receives the applicable budget fix without importing the public edition's binding mechanism. The DeepSeek branch is unchanged. No real school connection was made, and this does not establish new-user installation or end-to-end AI teaching quality.

### Earlier Git branch release on this date

- This release is based on committed fix `818304d` and targets `codex`, including the historical-evidence and review-option fixes below. It publishes repository code and documentation only, excluding personal study profiles, configuration and credentials.
- Pre-release recheck: 20 material-version/evidence tests and 32 review-flow tests passed (52 total). This run was a targeted regression check, not a rerun of the full suite; real school login was not tested.
- Existing users should follow the upgrade guide above and preserve their study profiles. This release does not migrate or repair existing historical records.

### Preserve historical evidence and display complete review options

- Archiving and initial material installation update physical locations in the material catalog without recursively rewriting original answers, sources, evidence bindings or correction snapshots. This prevents invalid checksums and blocked subsequent full-state saves. Markdown relocation changes link destinations only; literal historical paths remain unchanged.
- Separately stored review options now accompany questions in daily packages, answer-free previews and Today Review. Answers, grading notes and feedback remain hidden. Older packages without verifiable options require review; later edited options are not silently attached to an old question.
- Keep profiles and old materials when upgrading and update the code. Use the original material ID to retrieve historical files; old paths are not current file locations. Previously damaged records require individual comparison with backups; this fix does not re-sign, regrade or automatically rewrite history. The personal installation receives the same fixes; the DeepSeek branch is unchanged.
- Validation: synthetic regressions cover two consecutive updates, initial installation, answer and parent-question bindings, correction snapshots, subsequent full-state saves, option display and hidden answers. Related material sync, planning and mock-exam integration checks passed. Tests use isolated local data and do not establish real school login or the integrity of other students' historical profiles.

### Learning improvements and shared reliability fixes

- Ported same-question answer-exposure checks across review sets and mock exams, chronological exposure handling, and earlier exposure backfills from the personal edition. Retained public-edition timezone, complete-state and original-answer protections.
- Recurring classes respect start/end dates. Overdue non-exam tasks remain priorities only with an explicit plan for today and evidence that work is unfinished. Exam-source indexes now detect image, link, attachment and edit-metadata changes and require re-review.
- Added five read-only MCP tools for calendars, grades, notifications, resource listings and site information. Calendar queries paginate by event cursor and report completeness; notifications paginate without marking messages read. API allowlists, credential redaction and existing download authorization remain in place.
- Normal material replacement now invalidates old papers, rejects old sources in new papers, and pauses unverified old-source review questions. Original papers, answers and backfilled answers actually completed before replacement are preserved; material IDs are never silently retargeted.
- Chapter checks now use OS locks: live checks exclude competitors, killed checks can recover on explicit retry, and helper failure stops later writes. Legacy lock files without owner information require confirmation that old checks have stopped; age or `--force` never authorizes deletion.
- Preserve profiles, configuration and credentials when upgrading; restart the project MCP connection to expose the new tools. The personal installation and DeepSeek branch are unchanged. See the [update report](docs/UPDATE_REPORT_2026-11-07.md) for scope and limitations.
- Validation: all 724 offline Windows tests passed (130 Node, 92 Python setup/entry, 502 Python study). Coverage includes real version promotion, killed external chapter processes, concurrent exclusion, exposure backfills, source changes and paginated read-only queries. No real school connection was tested. Path-based evidence-binding migration, same-folder cross-school ID collisions and dependency advisories remain outside this change.

## 2026-11-06

### Complete English changelog

- Added `CHANGELOG.en.md`, covering every dated entry, feature, fix, upgrade instruction, validation result, and known limitation in the Chinese log. Both logs link to each other, and the English README now opens the English log.
- Documentation only; program behavior and learning records are unchanged. Future changelog updates maintain both languages together.

## 2026-11-05

### Chapter-scoped checks and local continuation without automatic full refresh

- The [Codex and DeepSeek update report](docs/UPDATE_REPORT_2026-11-05.md) documents each edition's actual behavior, commits, and validation. DeepSeek retains the version published by Harness; Codex changes the other client branch only when explicitly requested.
- `continue` and `today` now resume locally and rebuild local plans. New chats no longer automatically collect all courses, assignments, calendars, and forums. Plans and latest notices use task-scoped, read-only MCP queries; `refresh` is reserved for comprehensive checks. Pending review, failure, and interruption states remain intact, as do full-snapshot and review protections.
- Added `study.cmd --chat "original-user-chat-id" verify-chapter --course COURSE_ID --chapter CHAPTER --material MATERIAL_ID`. It downloads only explicitly selected chapter materials and compares their actual SHA-256 hashes, even when names, sizes, and modification times are unchanged. Repeat `--material` for multiple files. Indexing handles only selected files and reuses valid caches.
- Success and failure receipts are reused for already checked materials in the same chat; additional files are checked separately. Keep the original chat ID across dates, reopening, and context compaction; a new chat performs a new check. Use `--force` only for an explicit recheck. Local file changes invalidate the previous result; failures do not trigger retry loops or justify calling a file current.
- Updates from the same source retain version archives and historical references. Newly discovered files for the chapter, renamed sources, or inconsistent sources require review rather than guessed replacement relationships. Originals are also retained when multiple sources change together. A chapter receipt does not prove full collection, announcement review, original-page review, or student mastery.
- Upgrades preserve learning records, authorization, and configuration. Follow [chapter checks](docs/CHAPTER_CHECK.md) after updating code and rules. Old default-refresh wording in existing profiles should be updated through the shared writer without rebuilding or overwriting learning records. Full initialization after course selection still calls `refresh` explicitly instead of relying on the old implicit collection in `continue`.
- Validation: all 671 offline tests passed: 120 Node, 92 Python onboarding/authorization tests, and 459 Python study-tool tests. Isolated fictional profiles and simulated school responses cover byte comparison, same-chat reuse, retained failures, scoped indexing, version references, and full-collection recovery compatibility. Real school-network latency and client identity propagation still require live verification.

## 2026-11-02

### Chat-based setup and proactive usage guidance

- The shared Codex/DeepSeek workflow now has the assistant install dependencies, open the school login page, list courses, and save selections in the current project chat. The `start.cmd` menu remains a manual fallback rather than a required default step.
- Added `courses --list` and `courses --select-file` without terminal prompts. They confirm actual courses and full/information-only modes, validate the school, account, visible courses, and state version, then save safely. Missing arguments, state conflicts, account changes, or cancellation cannot silently open menus or expand download authorization. Actual synchronization still uses the unified entry point with the original chat identity.
- After setup, the assistant explains common uses, gives example requests, and invites a first action. Onboarding records are separate from mastery and use the existing shared lock and safe writer. Guidance can resume after reopening, is not repeatedly shown once delivered, and can be skipped or requested again. The study entry point also returns pending-guidance information for existing installations.
- Upgrades preserve `Study/`, configuration, authorization, and material references. After updating, ask “Continue setup” or “Show me how to use this.” See [chat onboarding](docs/CHAT_ONBOARDING.md). Automatically creating a welcome chat when none is open requires host support; this project does not fabricate assistant messages.
- Validation uses isolated fictional profiles and simulated courses, covering chat-based selection, authorization, state conflicts, persisted guidance, and existing installation recovery. It does not establish real school SSO or Harness automatic welcome-event behavior.
- This implementation landed on the `codex` source branch. DeepSeek's existing `course_library_authorization` routing requires separate adaptation and must not be overwritten directly. A [DeepSeek implementation handoff](docs/DEEPSEEK_ONBOARDING_HANDOFF.md) is included; this entry did not itself modify or validate a DeepSeek deployment.

### Client release branches

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
