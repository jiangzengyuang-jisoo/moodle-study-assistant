# 2026-11-07：迁入学习改进、补齐按需查询，修复 DeepSeek Harness 版缺陷

本次只修改公开仓库的 `deepseek`（DeepSeek Harness）版，起点为 `dfcdce4`（与 `origin/deepseek` 一致）。个人本地 HKU 学习部署、真实学习记录和 `codex` 版不随本次修改；学习处理按功能从个人本地版（由 Codex 提交 `c259be5` 记录）选择性移植，没有整包合并，也没有用 Codex 文件覆盖 DeepSeek 专用实现。

## 迁入的学习处理

- **同题答案曝光**：按“课程 + 题干 + 选项文本 + 来源资料页码”识别同一道题，即使更换复习条目、日包、卷号或题号也能发现此前展示过答案；忽略题号、评分说明和来源标注差异，但不做语义相似度猜测，题干改写、缺少来源或页码变化会视为新题。区分作答前、作答后展示：更早的曝光可以补录并覆盖较早记录，较晚的曝光不会把更早的真实独立作答判成重复，也不改写既有记录。
- **计划边界**：重复课表遵守 `applicable_from` / `term_end`（含首尾日期），起止未知时不限制；学期外明确日期的补课保留。过期非考试任务只有当日计划明确点名且有正向未完成证据时才继续进入优先项；已提交、小组代交和历史考试不重开，旧日计划不自动顺延。保留本端档案时区与字典/列表两种计划格式兼容。
- **考试来源变化**：来源索引记录页面/帖子中的图片（`src`/`srcset`/`alt`/`title` 等）、链接、附件、内嵌文件和最后修改时间等非文本证据。论坛文字未变但这些信息变化时，原考试要求标记为待重新核读，原来源记录、链接、站点和课程映射保留；纯文本旧记录的哈希不变，未变化的多媒体不会重复要求核读。它不会主动下载媒体来检查同链接下的字节变化。

## 课件更新后的练习资格

旧材料保留原 ID、哈希和原件，但归档或被新版替代后不再默认具有当前使用资格：

- 旧模拟卷的新鲜度检查、新模拟卷登记、新一轮复习选题都会检查来源是否归档或被替代。新卷拒绝旧源，按新版原页重新核验后才能登记。
- 已安排但未作答的旧源题在下次准备时标为“题源待核验”并释放预算，保留原题、原来源和历史作答；不会自动替换材料 ID 来冒充已按新版核验。
- 更新前真实完成的作答可按原时间补录；更新后的旧源复习题不能获得新的独立复测评级，但辅助练习记录和原记录仍保留。

## 按需只读学校查询

MCP 新增 5 个按需只读工具，读取工具由 10 个增至 15 个：`moodle_get_calendar_events`、`moodle_get_grades`、`moodle_get_notifications`、`moodle_list_resources`、`moodle_get_site_info`。

- 日历默认未来 30 天，可按课程与 Unix 时间范围查询；优先用学校提供的课程接口，否则按事件游标分页后筛选课程，整个请求保持同一时间窗。分页不前进或达到页数上限时返回 `complete:false` 与原因，应缩小时间范围继续查，不能把部分结果当成全部。完整性只指所选接口和时间范围，不等于所有类型的学校事件，也不等于 AI 已核读。
- 通知用 `limit/offset` 读取，`has_more`/`next_offset` 表示后续页；读取不会把通知标记为已读，后续单页也不冒充完整历史。
- 成绩只查询当前账号；资源目录只列可见的 resource/folder/url 模块元数据，不下载文件、不扩大课程内容授权；站点信息只返回必要身份与能力字段并脱敏凭据。
- 新方法仍经过 `ReadOnlyClient` 的 API 白名单、保留参数保护和重定向拒绝；没有新增作业提交、发帖、通知已读或下载入口。接口未启用时明确返回 `api_not_enabled`，不当作没有数据。
- 完整采集（`continue` / `refresh`）的默认触发时机和范围不变；这些查询只是按当前任务需要读取。升级后需要重启本项目的 MCP 连接才能看到新工具。

## 归档不改写历史证据（DeepSeek 版修复，取代原先的绑定重算）

旧问题：资料归档/首次安装会把原件移动到 `课程/_Archive/` 或正式目录，同时把整个学习状态里所有匹配的路径字符串递归替换。原答记录、父题来源里含有课件路径时会被改写，`evidence_bindings` 指纹随之失配，`evidence-source` 无法解析原答，之后每次完整状态保存都被拒绝（“已绑定的原答必须保留在同课程”）；记忆纠正的 `target_snapshot` 也会被改写，与 `target_sha256` 不符而被保存校验拒绝。

修复：资料版本事务不再递归替换学习状态。真实归档/首次安装只更新两处——`materials` 资料索引中的实际位置，以及真正指向被移动文件的 Markdown 链接目标；历史原答、来源路径、父题、`evidence_bindings` 和记忆纠正的 `target_snapshot` 一律不改写，因此指纹自然保持有效，不需要也不允许靠重算指纹来掩盖改写。原答、历史来源、资料的 `original_relative_path`/`archived_at` 和 material ID 都保留，不做 ID 重定向。

- 旧资料仍按原 material ID 和原页码读取归档版本（`--include-archived`）；普通搜索/复习只读当前版本。
- 只有旧路径、没有原 material ID 或无法唯一判断版本的引用保留为待核对，不指向同名新版；历史上同一原路径对应两个归档版本时，按 material ID 区分，不按路径猜测。
- 归档/首次安装后的完整状态保存保持可用；人工改答案/条件、伪造绑定仍被拒绝。升级前已经失配的历史记录仍需人工核对，不会自动“修复”。
- 本次不再保留归档时重算原答绑定摘要的旧实现（上一版报告的“原答绑定跨资料归档”方案已被取代），避免把历史改写当作正常迁移。

## 复习选择题选项快照（DeepSeek 版修复）

旧问题：复习题按题干保存到日包时没有保存选择题的 `options`，公开预览和“今日复习”不显示选项；题库后来编辑过时，旧日包也无法可靠还原当时题面。

修复：

- `practice_variants` 中的选择题变式可带 `options`（选项键→文本的字典，插入顺序即展示顺序，至少两项且键和文本均非空）；生成日包时把核验过的选项深拷贝为题目自己的快照。
- `daily.py run` 返回、公开预览和 `Today Review.md` 按 `A. 文本` 原序完整显示选项；作答前仍不显示 `answer_check`、答案或反馈，非待答题目不公开选项。
- 已有日包保存过选项时沿用其原快照，不因题库后来编辑而改写。
- 旧日包缺少有效选项、而题库中的对应变式现在是选择题时，不从可能已编辑的题库补猜：保留原题并标 `needs_review`、`budget_exempt: true`，记录 `options_review_reason` 与 `options_review_required_at`，释放未用预算；可另选已核验、未使用的新变式，没有备选时在 `needs_preparation` 说明。不完整旧题不能获得独立 FSRS 评级；题库后来删除或修改也不会清除待核对原因。
- 选项格式不完整的选择题变式不会被选为新题；非选择题行为不变。

## 同一档案换学校的数字 ID 隔离（DeepSeek 版单独修复）

旧问题：同一 `Study/` 档案静默切换到另一所学校（或同校另一个账号）后，新学校中同编号的课程和模块可能被关联到旧课程，污染聊天选课、旧 CLI 授权、课程资料授权、来源键与 LibrarySync 账本、章节核验基线。

修复（一档一校一账号，fail-closed，不做自动迁移）：

- 选课或授权时在 `settings.profile_account_scope` 记录学校与账号（含时间戳）；章节核对回执记录 `account_scope`。
- 聊天选课、旧 CLI 选课/授权、`library-sync`、`collect`、内容同步、指定资料下载、`refresh` 预检、章节核对和登录取 Token 前，都先核对档案已记录的学校/账号。不一致、记录互相矛盾或无法验证时立即停止，不写状态、不下载、不写回执、不替换凭据，也不重绑旧授权、资料账本或章节核验基线。
- 旧授权缺少账号信息时，不猜测、不补签；同一课程已有账号信息时不会丢失或放宽。`install_batch` 在安装第一个文件前核对本轮快照的账号归属。
- 复用暂存/指定快照前核对快照记录的学校与账号：跨校或跨账号快照停止；没有账号信息的旧自动缓存跳过并改走只读 API，显式指定的旧快照被拒绝。`continue` 采集被该护栏拦下时会向用户说明真实原因，不写失败回执、不标考试待核读，学习状态保持字节不变。
- 换学校或换账号请复制/新建独立项目目录，使用独立 `Study/` 档案重新 `setup` 和登录，不要在同一档案来回切换。
- 范围说明：`course_links.py` 的旧作答映射本身不含独立学校身份，追溯加入账号键会改写历史证据，因此未改；静默换校已经在所有写入入口被拦截，映射不会被重绑到新学校。全新档案、同校继续使用、同校重新选课/授权/同步/章节核对均照常可用。

## 保留的 DeepSeek 专用行为

- Harness 配置、`DSH_SESSION_ID`、主聊天与内部子任务规则；不生成 Codex 配置。
- `course_library_authorization`、`full` / `info_only`、`library-sync`、`subject`/`source_pages` 映射、LibrarySync 账本和 `archive_subdir` 支持。
- `chapter-check --section --mode strict` 的仅核验语义：比较远端内容并写回执，发现变化后走本端资料同步；不写学习状态、不下载到资料库、不建索引。
- 状态锁、快照、原答和历史证据保护；`continue` / `refresh` 默认采集策略未改变。

Codex 的章节存在式锁与 `verify-chapter` 入口本次没有移植：本端章节核验本来就是“只核对并写回执”的语义，不预设它有同样的残锁缺陷；本轮也没有复现出需要该修复的并发/中断问题。若以后发现本端章节核验的并发问题，应单独取证后处理。

## 升级与范围

更新程序时保留 `Study/`、`config.local.json` 和凭据，不需要重建个人档案或重建资料索引。归档不再改写历史记录，因此不会制造新的原答绑定失配；升级前已经失配的旧绑定不会被自动修复，仍需人工核对。重启本项目 MCP 连接后才会出现 5 个新查询工具。没有升级依赖，也没有宣称完成安全审计。

## 验证

Windows 本地、虚构档案、临时材料与模拟学校响应；不连接真实学校、不读取个人凭据、不修改任何个人学习数据。

- 三套完整回归：安装/入口 Python **100** 项、学习工具 Python **535** 项、Node **138** 项，合计 **773** 项通过，**0 失败、0 跳过**（JavaScript 断言与 Python `unittest`/Node test 结果）。学习工具套件有既有的 pypdf 读取噪声输出，不影响结果；未把噪声当通过，也没有把此前的中间失败算作通过。本机系统临时目录受限时，安装/入口 Python 与 Node 套件会出现环境性失败（子进程写系统临时目录被拒）；把临时目录重定向到工作区后全部通过，与本改动无关。
- 聚焦回归：本轮新增/更新——归档历史保护（连续两次归档、首次安装、原答/父题绑定、纠正快照、归档后完整保存、按原 ID 读取归档版本）、复习选项（保存与顺序显示、原快照承接、旧包不补猜与备选、独立评级拒绝、重复准备去重）全部通过；此前的原答绑定归档 9 项、`evidence_bindings` 11 项、`material_versions` 14 项及学习处理/按需查询/跨校隔离/快照账号等回归保持通过。
- 真实操作链（由独立验证脚本执行，不是只改同一材料 ID 的哈希）：
  1. 保存原答+绑定 → 真实资料换版归档（真实文件移动、旧 ID/哈希保留）→ 解析原答（模块与真实 `evidence-source` CLI）→ 完整状态再次保存；历史记录与绑定字节不变，10 类篡改仍全部拒绝且状态字节不变。
  2. 登记模拟卷 → 真实资料换版归档 → 检查旧卷失效 → 登记新卷（旧源被拒、新版源可登记）→ 准备复习（旧源待核验、预算释放、更新前真实作答可补录、更新后独立复评被拒）。
  3. 复习选择题 → `daily.py run` 生成日包（选项按题库顺序快照）→ 预览与 `Today Review.md` 完整显示且不泄露答案 → 暂停/换日旧包处理 → `daily.py attempt` 记录真实原答。
- 独立验证还覆盖：曝光先后与更早回填、计划适用起止日/补课/过期任务优先级、考试来源图片/链接/附件/内嵌文件/编辑时间变化、日历与通知分页及只读边界、跨校同编号正常与拒绝路径。
- 真实本地 `Study/` 只读审计：状态哈希检查前后一致（revision 408），394 份资料版本备份形成连续写入历史；两处问题均未发现受影响的真实记录（归档事务只改 `materials` 行；复习队列与日包为空），因此未做任何恢复；该审计报告只保存在本地，不进入公开仓库。
- 真实学校 SSO/MFA、新机完整安装、真实学校接口权限和交互式 CLI 真实网络链路仍未验证。

## 限制

- 归档/首次安装只更新资料索引中的实际位置和 Markdown 实际链接目标，不改写历史原答、父题、证据绑定或记忆纠正快照；升级前已经失配的记录仍需人工核对，保存会继续拒绝。只有旧路径、无法唯一判断版本的引用保留待核对，不指向同名新版。
- 一档一校一账号是刻意隔离，不提供自动迁移；已有课程/资料/授权但完全缺少学校或账号证据的旧档案会 fail-closed，需恢复可证明账号的备份，或在独立目录使用独立档案。
- 旧包缺少有效选项、而题库仍能证明其对应变式是选择题时，标为待核对并释放预算；若题库在首次准备前已经删除或改到无法再证明选项内容，只能保留原记录待人工核对，不猜测补写。已标记或仍能核对出的不完整旧题不能获得独立 FSRS 评级。
- 复用暂存快照或显式指定 `--snapshot` 前会核对快照记录的学校/账号；没有账号信息的旧自动缓存会被跳过并改用只读 API 现场读取，显式指定的旧快照会被拒绝。真实学校旧快照（没有 `account_scope` 字段）因此需要重新采集一次，不能直接当作本轮文字来源。
- 同题识别保守：题干改写、缺少来源或页码变化会当作新题，不会因“看起来像”而合并。
- 按需查询的完整性只描述所选接口与时间范围，不代表所有学校事件，也不代表 AI 已核读。
- 离线测试不能证明真实学校连接、SSO/MFA、权限范围或新机安装一定成功；`configuration_ok` 不等于 Harness 实际运行已验证。

## English summary

This update only changes the public repository's `deepseek` (DeepSeek Harness) edition; the personal local deployment, real learning data and the `codex` edition are untouched. Learning improvements were ported feature by feature (not as a bulk merge) from the personal edition as recorded by Codex commit `c259be5`.

- Same-question answer-exposure checks now work across review packages and mock papers (course + prompt + option text + source pages), keep chronological ordering, allow earlier-exposure backfill, and never invalidate an earlier real independent answer. This is exact identity matching, not semantic similarity.
- Recurring timetables honor `applicable_from`/`term_end`; explicitly dated makeups outside the term are preserved; overdue non-exam tasks stay priorities only with an explicit plan for that day plus positive unfinished evidence; submitted/group-submitted items and historical exams are not reopened.
- Exam-source indexes track non-text evidence (images, links, attachments, inline files, edit timestamps); such changes require re-review while preserving previous sources, URLs, site and course mapping.
- After real material archiving/replacement, old papers become stale, new papers reject old sources, and pending old-source review questions are marked as needing verification with budget released; original prompts, sources and history are kept, real answers completed before the update remain backfillable at their original time, and material IDs are never silently retargeted.
- Five on-demand read-only MCP queries were added (calendar, grades, notifications, resources, site info; 10→15 read tools). Calendar reports honest completeness and pages by event cursor; notifications paginate without marking read; resources are metadata only with no download and no authorization bypass; grades use the current account; site info is whitelisted and redacted. Read-only allowlists, reserved-parameter protection and redirect refusal remain; no new write/download tool. Restart the MCP connection after upgrading.
- DeepSeek-only fixes: (1) courseware archiving/first installation updates only the actual location in the material index and real Markdown link destinations; historical original answers, parent-question sources, evidence bindings and memory-correction snapshots are never rewritten, archived versions stay readable through their original material IDs, old-path-only or ambiguous references remain flagged for review instead of being retargeted to a same-named new version, and fingerprints are never recomputed to mask a rewrite; (2) one Study profile keeps one school account: selection, legacy CLI authorization, library sync, collection, content sync, downloads, refresh precheck, chapter checks, reused/supplied snapshot checks and login verify the recorded school/account first and fail closed without writing or re-binding old grants, ledger, chapter baselines or reused snapshot text. Legacy grants without account information are never guessed or back-filled; a `continue` collection stopped by this guard reports the real reason, writes no failed receipt and keeps learning state byte-identical; changing school/account requires a separate project folder and profile; (3) review packages now snapshot valid multiple-choice `options` (label→text dict, insertion order preserved) and show them in order without leaking answer checks, answers or feedback; carried packages keep their original snapshot, old packages missing options are marked for review with budget released and a persistent reason, an unused verified variant may replace them, and incomplete old questions cannot receive an independent FSRS rating once their package has been checked or their choice identity is still verifiable; if the bank can no longer establish that identity before the first preparation, the original record is kept for manual review instead of guessed.
- Preserved DeepSeek-specific behavior: Harness configuration, `DSH_SESSION_ID` and main-chat/subtask rules; `course_library_authorization`, `full`/`info_only`, `library-sync`, `subject`/`source_pages`, LibrarySync ledger and `archive_subdir`; strict `chapter-check --section --mode strict` verification-only semantics; state locks, snapshots, original-answer and history protection; unchanged `continue`/`refresh` collection defaults. Codex's existence-based chapter lock and `verify-chapter` entry were deliberately not ported.
- Validation: offline Windows tests with fictional data and mock school responses: **100** setup/entry Python, **535** study Python and **138** Node tests all pass (0 failures, 0 skips; **773** total). On this machine a restricted system temporary directory caused environment-only failures in the setup/entry Python and Node suites; redirecting temporary files into the workspace passed completely and those failures are unrelated to this change. Independent checks re-ran both real operation chains (save original answer → archive courseware → resolve original answer → save again; register paper → archive courseware → old paper stale → register new paper → prepare review), and a third chain covered choice-option packages (snapshot → preview/view without leakage → old-package handling → real attempt). A read-only audit of the real local `Study/` state and its 394 material-version backups (hashes identical before and after) found no real records affected by either bug, so no recovery was performed. Real school SSO/MFA, fresh-machine installation, real school API permissions and the interactive CLI against a real network remain unverified. Known limitations: history is preserved exactly as recorded, so pre-existing mismatches are never auto-repaired; one profile deliberately maps to one school account (no automatic migration); old cached snapshots without account information need one fresh collection before being reused as a text source; an old package whose choice identity can no longer be established before the first preparation is kept for manual review instead of guessed; same-question identity is conservative (rewritten stems or missing sources are treated as new questions); read-query completeness describes the selected API and time window only.
