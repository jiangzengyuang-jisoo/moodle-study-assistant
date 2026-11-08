# 课程级资料授权与自动同步

本页说明“选课时一次授权，之后自动初始化和增量同步课程资料”的规则，面向上手同学与执行任务的 AI。示例中的页面 ID、课程 ID 必须替换为本人档案的实际值。

目标：本人在选课时确认一次“课程级资料授权”后，助手可枚举所选课程页面中当前账号可访问、可直接获取的资料，下载、归档、登记并建立索引，同时逐项记录缺口；之后的同步沿用该授权增量补齐，不逐份请示。新增课程页面必须单独授权。

## 在聊天中初始化（默认）

在 Harness 当前项目聊天里说“帮我选择课程并初始化”。助手执行 `courses --list` 后在聊天列出真实课程，你按课名选择，并确认完整初始化或只同步信息。助手用 `courses --select-file` 一次保存选课与本端资料授权，继续本轮学校采集、核读及 full 模式的 `library-sync --snapshot`。不需要打开 CMD；安装结束助手主动讲用法并邀请实际操作。完整流程见 [CHAT_ONBOARDING.md](CHAT_ONBOARDING.md)。

新入口校验学校、账号与状态版本，拒绝过期选择；保留历史记录，授权同时覆盖已选公共页与班级页。`info_only` 会停止所选页后续自动资料下载，已下载内容保留。

## 手动备用：一次授权，两种模式

在项目根目录运行：

```powershell
node tools/moodle/run.mjs courses
```

也可以双击 `choose-courses.cmd`，或使用 `start.cmd` 向导的第 6 项“调整课程与资料授权”，三者是同一入口。

1. 命令先列出当前账号可访问的课程（Moodle 页面 ID、名称、短名）。
2. 输入本学期要跟踪的页面 ID（逗号分隔；公共课程页和个人班级页都要选，直接回车取消）。
3. 命令显示“课程级资料授权范围”：
   - 所选课程当前账号可访问、可直接获取的全部课程资料（课件、作业题目附件、阅读材料、练习文件、论坛附件、正文内联图片、可直接下载的音视频），以及公告、作业要求、讨论文字及准确出处；
   - 不开始测验尝试、不提交作业、不发帖、不修改学校状态；
   - 流媒体、外部网站、额外登录或权限受限内容只保留入口和原因。
4. 选择模式：`1` 完整初始化（同步课程信息 + 下载/归档/建立索引）；`2` 只同步课程信息（不下载资料）；`0` 取消（不保存授权）。
5. 再输入 `Y` 确认；确认后保存所选页面和课程级授权。

### 完整初始化做什么

- 先同步课程信息：若本机已有 6 小时内完整学校快照（`Study/_System/refresh-latest.json` 或 `refresh-last-complete.json`），直接复用；否则先运行一次只读采集 `node tools/moodle/run.mjs refresh`。
- 随后枚举所选页面、下载/归档资料、登记 `state.materials`，最后统一重建本地索引。
- 采集与核读仍分离：命令完成只表示资料已落地、索引已建，不代表已核读或已掌握。AI 还要按本轮快照核读作业要求、公告和讨论，再安排学习。
- 采集不完整时仍尽量同步，缺失项逐项写入缺口清单；退出码 2 表示部分失败。
- 只同步信息模式同样复用 6 小时内完整快照或补跑只读采集，但绝不下载任何资料。
- 取消选课不会删除学习记录或已有授权记录；同步只处理当前已选页面。
- 没有后台刷新、定时任务或常驻服务；授权只保存在本地 `Study/_System/state.json`。

新增课程页面时重新运行 `courses` 并选入新页面、再次确认授权。未授权页面不会被下载：若本次仍有可同步页面，报告会把未授权页面标为 `blocked`，原因包括 `course_not_authorized`、`authorization_info_only`、`authorization_scope_changed`；全部页面都不可同步时命令直接报错退出。

### 非交互入口（仅供 AI 在用户已明确授权后使用）

```powershell
node tools/moodle/run.mjs courses --select 146265,142648 --mode full --yes
node tools/moodle/run.mjs courses --select 146265,142648 --mode info_only --yes
```

- `--select`：Moodle 课程页面 ID，逗号分隔，必须来自上一步列出的可访问课程。
- `--mode full|info_only`：含义同上；省略会进入交互式选择。
- `--yes`：表示用户已在**当前对话**明确授权这些课程页面和该模式，不是程序替用户授权。AI 不得在用户未明确同意时使用；使用前应先说明范围、模式和将自动执行的动作。
- 可选：`--refresh` 忽略 6 小时快照，先重新只读采集；`--no-sync` 只保存授权、不执行初始化；`--chat 原始聊天ID` 把原始用户聊天 ID 传给采集，子任务必须显式传主聊天 ID 并加 `--subtask`（见 [SESSION.md](SESSION.md)）。
- 非交互入口不显示授权范围全文，确认责任在 AI：必须让用户看过范围后再加 `--yes`。

### 授权保存位置

`state.settings.course_library_authorization`（JSON）：

| 字段 | 含义 |
| --- | --- |
| `schema` / `scope_version` / `scope` / `updated_at` | 授权格式、范围版本、范围说明和更新时间 |
| `new_course_requires_authorization` | 固定 `true`：新增课程页面需重新授权 |
| `per_file_approval_required` | 固定 `false`：已授权范围内不再逐份询问 |
| `background_refresh` | 固定 `false`：没有后台刷新或定时任务 |
| `courses.<state课程ID>` | 每门课/页面：`moodle_courseid`、`mode`（`full`/`info_only`）、`authorized_at`、`scope_version`、`account_scope`（学校地址与账号） |

- 授权按已选课程页面逐一记录；同一科的主页面和副页面各自有 state 课程 ID 条目，资料归档时副页面归入主学科（见“学科合并”）。
- 重复保存相同授权不会重复写状态；删除选课不删除已有授权记录，但未选页面不会同步。
- 授权写入不修改任何学习记录、资料行或快照回执。
- 只读查看授权、材料数量和状态版本：`& '.venv/Scripts/python.exe' 'tools/study/course_library.py' status`。

## 一档一校一账号

同一 `Study/` 档案只对应一所学校的同一个账号：Moodle 的数字课程 ID 和模块 ID 只在该账号内有意义，换学校或换账号时相同数字可能指向完全不同的课程和文件。

- 选课或授权成功时，学校与账号记录在 `state.settings.profile_account_scope`；章节核对回执记录本次 `account_scope`。
- 聊天选课、旧 CLI 选课/授权、资料同步、采集、指定资料下载、刷新预检、章节核对和登录取 Token 前，都会先核对档案已记录的学校/账号。不一致、证据互相矛盾或无法验证时立即停止，不写状态、不下载、不写回执、不替换凭据，也不重绑旧授权、资料库账本或章节核验基线。
- 旧授权缺少账号信息时，不猜测、不补签；同一课程已有账号信息时不会丢失或放宽。
- 复用暂存快照或指定 `--snapshot` 时，先核对快照记录的学校/账号：跨校/跨账号快照直接停止；没有可核对账号信息的旧快照只作为“不可验证”的自动缓存跳过（改用只读 API 现场读取），不作本轮文字来源。
- 换学校或换账号：复制或新建独立项目目录，使用独立 `Study/` 档案重新 `setup` 并登录，不要在同一档案来回切换。已有课程/资料/授权但完全缺少学校或账号证据的旧档案会 fail-closed，需恢复能证明账号的备份，或在独立目录使用独立档案。
- 同校继续使用、重新选课、改同步方式、增量同步和章节核对照常可用。

## 日常增量同步

```powershell
node tools/moodle/run.mjs library-sync [--course 课程ID ...] [--snapshot 路径] [--no-snapshot] [--max-bytes N] [--batch-size 1-20] [--dry-run]
```

- 只处理已保存 `full` 授权的页面；`info_only`、未授权或授权范围已变化的页面不下载。若本次仍有可同步页面，报告会把其余页面标为 `blocked` 并写明原因；所有页面都不可同步时命令报错退出。
- `--course` 使用 state 中的课程 ID（如 `moodle-146265`），可重复传多个；省略时处理全部已选课程。副页面课程 ID 会沿 `merged_into` 解析到主学科。
- 默认优先使用最近 30 分钟内的**完整**学校快照作为公告/作业/讨论文字来源；没有近期完整快照时用只读 API 现场读取。`--snapshot` 指定本轮独立快照（必须在 `Study/` 内）；`--no-snapshot` 强制不用快照。
- `--max-bytes` 单文件大小上限（默认 67108864 字节，即 64 MiB）；`--batch-size` 每批安装数量（1–20，默认 8）；`--dry-run` 只枚举与统计，不下载、不写 state 或 ledger。
- 网络只读：不开始测验尝试、不提交作业、不发帖、不发邮件、不修改学校状态；不读取或导出 Cookie。
- 输出 JSON 摘要（`totals`、`blocked`、`index`）并写报告；部分失败时退出码 2。
- 助手按 [WORKFLOW.md](WORKFLOW.md) 的时机（需要校务信息时本聊天首次完整采集、用户明确刷新或要求同步、资料新增/变化）在已授权范围内运行增量同步，不逐份请示；没有后台轮询。

## 只核对某一章（不建索引）

```powershell
node tools/moodle/run.mjs chapter-check --course 课程ID --section "Topic 5" [--mode quick|strict] [--modules 501,502] [--chat-key 聊天摘要] [--force] [--no-persist]
```

- 只处理这一门课：先用本地资料索引与来源映射定位本章模块，再只获取并比对本章文件；本地索引无法确定时才退回课程目录中明确的章节名/模块名匹配。Moodle 区块位置、日期和 Tutorial 编号不会被当成章节编号；匹配不唯一时返回候选和原因，不选择整个区块。
- 默认 `strict`，重新获取本章文件并比较内容哈希；`quick` 只比较服务器记录的大小与修改时间，不建立可复用的严格回执。工具只读，不写 `state.json` 或 ledger，不下载到 `_Inbox`，不重建 catalog；回执写在 `Study/_System/ChapterChecks/<课程>/<章节>.json`，记录本次学校/账号归属、已定位模块、本地文件内容哈希和聊天摘要（跨校时会被“一档一校一账号”检查拦下）。
- 不需要新的课程级授权：`quick` 只读课程元数据；`strict` 只允许已保存 `full` 授权的页面，`info_only` 或未授权页面会被明确拒绝，而不是静默下载。
- `--chat-key` 由统一入口 `study.cmd` 自动传入；同一聊天、账号、课程、资料范围和核对强度且本地文件未变时复用严格回执，不联网；本地新增本章资料只核对新增部分。新聊天、换账号、本地文件变化或 `--force` 会重新核对。失败、缺基线或部分完成不算全部核验成功，也不自动重试。
- 适合“开始教某一章前确认课件没变”；它不安装新文件、不补新增章节，也不读公告、作业、讨论或日历。发现变化后仍要按上面的增量同步或完整刷新处理，判定见 [WORKFLOW.md](WORKFLOW.md#单章资料核对不触发完整采集)。

## 下载范围与缺口

### 会下载/保存的内容

- `resource`、`folder`、`page` 等模块中当前账号可访问、可直接获取的文件；作业说明附件、论坛附件、正文内联图片、可直接下载的音视频；常见可直接下载格式（PDF、Office、文本、图片、音视频、压缩包）按原文件归档，压缩包不自动解压。
- 公告和课程页面结构（章节说明、模块入口）、作业要求、论坛讨论文字保存为文本捕获（Markdown），含课程页面 URL、模块、章节、采集时间、快照编号等出处。
- 文件先落到 `Study/_Inbox/.staging/<run_id>/`，校验大小、SHA-256 和文件签名后才登记；成功项清理暂存，失败项保留已下载字节到 `Study/_Inbox/<课程>/<模块>/` 供人工核读。

### 只保留入口和原因（不下载、不算完成）

这些项在报告中为 `skipped`（部分为 `blocked`），带原因码和模块入口，不因授权完整就把它们算成已获取：

| 原因码 | 含义 |
| --- | --- |
| `quiz_questions_require_attempt_not_started` | 测验题目需在尝试中查看；按授权不自动开始测验 |
| `external_site_not_downloaded` | 外部网站或云盘不能经学校文件接口直接获取 |
| `external_streaming_not_downloaded` | 流媒体/录像需站点内登录或播放器 |
| `restricted_by_course_permission` | 当前账号无权访问（含对学生隐藏的模块） |
| `activity_without_downloadable_content` | 该活动没有可直接下载的文件或正文 |
| `label_body_not_exposed_by_api` | 标签正文未通过课程接口返回 |
| `folder_contents_not_exposed` | 文件夹没有通过接口返回文件 |
| `no_files_exposed_by_api` | 资源模块没有通过接口返回可下载文件 |
| `unsupported_activity_type` | 该活动类型不在自动获取范围内 |
| `unsupported_file_type` | 文件类型不在可安全归档的白名单内 |
| `unsafe_filename` | 文件名不安全，不能写入本地目录 |
| `file_source_not_direct` | 文件地址不是学校文件接口，不能直接获取 |

## 归档规则

- 目标路径：`Study/<state课程ID>/<category>/<原文件名>`；category 为 `Lectures`、`Tutorials`、`Assignments`、`Practice`、`Other`，按章节、模块和文件名关键词自动归类，未识别归 `Other`。
- `Study/_Inbox/` 只做暂存；旧版移入 `Study/<课程>/_Archive/`；普通搜索、`read` 和复习只读当前版本。
- 同一课程、同一目录下出现同名不同内容时，使用 `module-<模块编号>` 子目录（必要时再附加短哈希后缀），绝不覆盖。
- 相同字节、不同来源：只登记一条并记 `duplicate_of`，不重复占空间；相同来源、相同字节：报告为 `identical`，不写 state。
- 同一来源产生新字节：新登记版本用 `supersedes` 指向旧版，旧版进入 `_Archive`，保留原 material ID、原文件名、来源页面、SHA-256 和历史引用。
- 材料记录保留 `source_pages`（各页面/章节/模块来源）和 `moodle_file_sources`（模块、远程目录、原文件名）；版本关系和历史引用见 [MATERIAL_VERSIONS.md](MATERIAL_VERSIONS.md)。

## 学科合并

- 同一科的不同 Moodle 页面归入一个学科目录：MATH1013 页面 `146265`（主）+ `142648`（副）按 `moodle-146265` 组织；ECON1210 `146739`（主）+ `146748`（副）按 `moodle-146739` 组织。
- 主学科课程记录保存 `linked_page_ids`；副页面课程记录用 `merged_into` 指向主学科。
- 材料记录的 `course_id` 是主学科 state 课程 ID；`source_pages` 保留各页面来源，不把副页面材料记成独立课程。
- `library.cmd search/outline --course` 与 `library.py sync --course` 对副页面沿 `merged_into` 解析到主学科；`read`/`render` 按 material ID 读取，材料本身的 `course_id` 已是主学科，无需再换算。
- 公共页和个人班级页都要在选课时选中；只选其中一页时只同步该页，不凭课程名称自动补选。

## 索引与“下载/解析/核读/掌握”区分

- `library-sync` 在所有批次安装完成后统一调用 `build-index` 重建 catalog（`Study/_System/Library/catalog.json`），不是每装一个文件就重建。
- 下载成功（`installed`/`identical`/`duplicate`）只表示本地有原件或已有同内容；`Library.sync` 的 `indexed`/`not_text_indexed` 只表示解析状态；两者都不等于已核读，更不等于已掌握，也不写 `verified`、FSRS 或掌握状态。
- `library.py sync` 只重解析新增或变化的资料；索引按课程、文件、单元页（PDF 物理页、PPTX slide、DOCX/HTML 结构段）定位；`context/search/read/render/outline` 用法见 [LIBRARY.md](LIBRARY.md)。
- OCR 和文字抽取始终未核验（`verified:false`）；公式、图表和题号要核对原页。支持解析 PDF、PPTX、DOCX、HTML、文本、图片；音视频和压缩包只归档不解析，索引中标注 `unsupported_format`、`unsupported_archive` 等警告。
- 下载/解析完成后，AI 仍要按本轮学校快照核读作业要求、公告和讨论，再安排学习。

## 断点续跑与状态记录

- 每项记录 `installed`、`identical`、`duplicate`、`already_current`、`failed`、`skipped`、`blocked` 之一，并带原因和详情。
- `Study/_System/LibrarySync/ledger.json`：逐项指纹和状态（来源的 filesize/timemodified/url，或文本捕获的 SHA-256）、`material_id`、首次成功时间、失败原因。
- `Study/_System/LibrarySync/latest.json`：最近一次完整报告；`Study/_System/LibrarySync/资料缺口.md`：按课程汇总的人工可读缺口清单（未获取/未解析项及原因）。
- 中断后再次同步只补未完成、新增或变更项；已完成且仍是当前版本的项凭 ledger 标为 `already_current`，不重复下载。失败项下次同步自动重试。
- 已完成项不会重复登记材料、不会重复建立版本关系；同名不同内容按归档规则分目录，不覆盖。

## 安全与隐私

- 只调用学校只读 API；不开始测验尝试、不提交作业、不替用户作答学校测验、不发帖、不发邮件、不修改学校完成状态。
- 不读取或导出浏览器 Cookie；密码和 MFA 只由本人在学校网页输入；Token 不写进聊天、状态、日志或 Git。
- 下载内容和文本捕获只保存在本地被 Git 忽略的 `Study/`，不上传；AI 读取内容时可能发送给模型服务商，见 [PRIVACY.md](PRIVACY.md)。
- 不导入或合并其他电脑、作者或他人的个人资料、学习记录与凭据；换电脑或换 Windows 账户按 [使用须知](使用须知.md) 重新登录。
- 失败时如实说明，不绕过学校限制、不额外登录、不抓取授权范围外的内容。

## DeepSeek Harness 沙箱

- 受限 shell（如 DSH 自己的沙箱）可能阻止 Node 子进程、DPAPI 解密或网络下载。真实初始化/同步应在正常终端（`start.cmd`、`choose-courses.cmd`，或经用户批准的完整访问命令）运行；只读的 `library.cmd context --read-only`、`search`、`read` 等可在会话内使用。
- 沙箱里命令失败不等于实现错误；完整回归也应在真实终端执行（见 [TOOLS.md](TOOLS.md#验证与发布) 与 [DSH.md](DSH.md#6-测试)）。

## 相关文档

[TOOLS.md](TOOLS.md) · [LIBRARY.md](LIBRARY.md) · [MATERIAL_VERSIONS.md](MATERIAL_VERSIONS.md) · [WORKFLOW.md](WORKFLOW.md) · [START.md](START.md) · [PRIVACY.md](PRIVACY.md) · [DSH.md](DSH.md) · [使用须知.md](使用须知.md)
