# 工具入口与验证

以下命令在用户的学习项目根目录运行。普通同学通过自然语言提出任务；本页供 AI 和维护者参考。不要在共享技能安装目录运行个人初始化或保存学习状态。

## Moodle

```powershell
node tools/moodle/run.mjs init
node tools/moodle/run.mjs check
node tools/moodle/run.mjs courses
node tools/moodle/run.mjs library-sync
node tools/moodle/run.mjs chapter-check --course 课程ID --section "第8章" --mode strict
node tools/moodle/run.mjs refresh
node tools/moodle/run.mjs mcp
```

`init` 只创建缺失的配置/空白状态，不覆盖已有资料。`courses` 交互选择跟踪页面，并确认课程级资料授权（完整初始化或只同步课程信息），保留未选页面的既有历史；完整初始化、非交互入口和授权保存位置见 [课程级资料授权与自动同步](COURSE_LIBRARY.md)。`library-sync` 对已保存完整授权的课程页面增量枚举、下载、归档、登记并建立索引；学校接口仍只用只读调用，不开始测验尝试、不提交作业、不发帖、不修改学校状态。`chapter-check` 只枚举一门课并比对某一章已有文件（`--mode quick|strict`），不写状态、不装文件、不建索引，回执在 `Study/_System/ChapterChecks/`，回执同时记录本次学校/账号归属；判定见 [单章资料核对](WORKFLOW.md#单章资料核对不触发完整采集)。`refresh` 采集到 `Study/_System/Runtime/runs/<run_id>/snapshot.json`，完整成功另存 `refresh-last-complete.json`；不直接更新个人掌握、作业完成或 AI 核读状态。退出码2表示部分采集失败。一个 `Study/` 档案只对应一所学校和账号：选课、旧 CLI 授权、资料同步、采集、下载、刷新预检、章节核对和登录会先核对档案已记录的学校/账号，不一致时停止且不写入；换学校请复制/新建独立项目目录并使用独立档案，见 [课程级资料授权](COURSE_LIBRARY.md#一档一校一账号)。

AI 在需要校务信息或用户明确要求时调用 `continue/refresh`，不会每条追问重复执行；单章教学用 `chapter-check`，不会触发完整采集。命令本身始终进行真实采集。采集器全局最多同时执行4个 API 请求，每个论坛的分页仍按顺序读取，完整正文、回复和权限核对保留。不要把没有重新联网的缓存复用记为新的检查。

MCP 暴露课程、作业、测验、论坛、日历、成绩、通知、资源目录和站点信息只读查询（合计 15 个读取工具）；全论坛分页用 `study_all_discussions`，回复用 `study_discussion_posts`。上游 recent discussions 只有最近20条，不能替代完整采集。不注册学校写操作；普通 MCP 查询不下载文件。正常刷新在采集后为有课程级完整授权的范围自动下载、归档和索引；没有授权的范围只同步信息。选课授权、结果和重试入口见 [COURSE_CONTENT.md](COURSE_CONTENT.md)。

`moodle_get_calendar_events` 支持课程、时间范围和分页限制，先看 `complete` 再判断是否覆盖所选事件；分页不前进或到页数上限返回 `complete:false`，应缩小时间范围继续查，不能把部分结果当成全部。`moodle_get_notifications` 用 `limit/offset`、`has_more`/`next_offset` 续读，不把通知标为已读。成绩只查当前账号；资源目录只列可见 resource/folder/url 模块元数据，不下载、不扩大授权；站点信息只返回必要身份与能力字段并脱敏凭据。接口未启用时返回 `api_not_enabled`，不等于没有数据。完整性只描述所选接口和时间范围，不等于所有类型的学校事件，也不等于 AI 已核读。升级后需重启本项目 MCP 连接。范围与限制见[更新报告](UPDATE_REPORT_2026-11-07-deepseek.md#按需只读学校查询)。

### 课程级授权与资料初始化

在选课入口 `courses` 中，命令先列出课程，再让本人选择页面并确认“课程级资料授权”模式：`1` 完整初始化（同步课程信息 + 下载/归档/建立索引）、`2` 只同步课程信息（不下载）、`0` 取消。确认后授权保存到 `state.settings.course_library_authorization`；之后日常同步沿用授权增量补齐，新增课程页面须重新授权。完整初始化若本机有 6 小时内完整快照则直接复用，否则先跑只读 `refresh`，再执行资料同步；采集与核读仍分离，下载/解析成功不等于已核读或已掌握。详细范围、归档、学科合并和缺口规则见 [COURSE_LIBRARY.md](COURSE_LIBRARY.md)。

非交互入口仅供 AI 在用户当前对话已明确授权后使用，`--yes` 表示用户已确认该课程范围，不是程序自行授权：

```powershell
# 完整初始化；只同步信息时把 full 换成 info_only
node tools/moodle/run.mjs courses --select 146265,142648 --mode full --yes
# 可选参数：--refresh 忽略 6 小时快照重新采集；--no-sync 只保存授权；
# --chat 原始聊天ID 把原始用户聊天 ID 传给采集（子任务用主聊天 ID 并加 --subtask）
```

日常增量同步：

```powershell
node tools/moodle/run.mjs library-sync [--course 课程ID ...] [--snapshot 路径] [--no-snapshot] [--max-bytes N] [--batch-size 1-20] [--dry-run]
```

`--course` 使用 state 课程 ID（如 `moodle-146265`），可重复；`--snapshot` 必须位于 `Study/` 内，省略时优先使用最近 30 分钟内的完整快照，没有完整快照则用只读 API 现场读取，`--no-snapshot` 强制使用只读 API；`--max-bytes` 默认 64 MiB；`--batch-size` 1–20，默认 8；`--dry-run` 只统计不下载。逐项状态写入 `Study/_System/LibrarySync/ledger.json`，最近报告和缺口清单为同目录的 `latest.json`、`资料缺口.md`。中途停止后再次运行只补未完成/新增/变更项，失败项自动重试。

### 明确授权的资料下载

已保存课程级完整授权的课程页面由资料同步自动处理，不需要逐份下载。用户明确请求授权范围以外或点名单份“某章课件/某份老师说明”时，先从所选课程的实际模块列表识别对应资源，再调用独立入口：

```powershell
node tools/moodle/run.mjs download 123 --authorized
```

`123` 只是示例模块编号，不能照抄；多个模块必须逐个明确列出。`--authorized` 表示助手已经收到用户对这些资料的明确下载请求，不是程序替用户授权。仅限当前所选课程的 resource/folder 模块；不扩大成课程级授权范围外的抓取，不通过输入任意 URL 获取其他文件。

下载先落到 `Study/_Inbox/`，保留官方文件名，按内容哈希去重，同名不同内容保留为不同文件。成功下载不代表已经核读或掌握。AI 随后检查实际文件、课程归属和页数，按用户目录约定归档并更新 `materials`；下载器本身不替 AI 作语义判断。账号、权限或站点限制导致失败时如实说明，不绕过限制。

## 复习、复测、反馈和当前计划

资料初始化和快速读取另见 [LIBRARY.md](LIBRARY.md)：`tools/study/library.cmd context` 直接读取最新状态，`sync` 构建或更新资料缓存，`search/read/render/outline` 定位并核对完整原页。课程资料的自动初始化、增量同步与缺口见 [COURSE_LIBRARY.md](COURSE_LIBRARY.md)。这些命令不写学习进度、作答或 FSRS。

```powershell
& '.venv/Scripts/python.exe' 'tools/study/daily.py' run
& '.venv/Scripts/python.exe' 'tools/study/daily.py' check
```

默认状态为 `Study/_System/state.json`。`--state 路径` 和带时区的 `--at 时间` 放在子命令前，仅测试/迁移需要；不把演示日期或测试作答写进正式状态。

- `run` 重建当前计划并选择核验过的复习新题。首次默认预算15分钟；以后省略 `--minutes` 沿用当天预算，明确减量才传更小值，包括0。预算计入原学习时间，不因重复运行叠加。新档案无题库时显示待准备。
- `render` 只重建当前状态视图，不创建题包或作答。
- `check` 检查学习时区的日期、状态版本、9份当前系统页面、已登记试卷与模拟题错题区；失败后重读状态并重新运行。不启动后台刷新。
- 队列 `review_queue` 项通过 `mistake_id` 关联真实问题，具体题在 `practice_variants`。每题需有 id、完整 prompt、非空字符串列表 `answer_check`、`estimated_minutes`、材料依据 `source` 和 `verified: true`。`source` 包含 `material_id`、`pages` 与 `detail`。AI 必须实际核验答案；不能只把标志改成 true。答案只供批改，不提前出现在今日待答题单。
- 复习选择题沿用考试题库的 `options` 协议：`practice_variants` 中的选择题变式带 `options`，即“选项键 → 选项文本”的字典，键的插入顺序就是展示顺序；至少两项，键和文本都须为非空字符串。`daily.py run` 生成日包时把核验过的选项深拷贝为题目自己的 `options` 快照，`prepare` 公开预览和 `Today Review.md` 会按原顺序逐行显示 `A. 文本`，作答前仍不显示 `answer_check`、答案或反馈；非待答题目（已讲解、已作答等）不公开选项。已生成日包中的选项快照按原样沿用，题库变式之后被编辑也不改写。旧日包若没有有效选项快照、而题库中的对应变式已是选择题，不从题库补猜：原题保留为历史并标为 `needs_review`、`budget_exempt: true`，记录 `options_review_reason` 与 `options_review_required_at`；该条不再占用当天预算，可另选未使用的新变式，没有备用变式时在 `needs_preparation` 说明。这类未补全选项的旧选择题只能作历史或辅助记录，独立 FSRS 评级会被拒绝。

### 保存作答

将 JSON 通过标准输入传给 `daily.py attempt`。必填字段如下；编号取自当前题包，以下是字段说明，不能作为本人作答录入：

| 字段 | 内容 |
| --- | --- |
| attempt_id | 本次真实尝试唯一编号，同一次重试复用 |
| session_id / review_id / variant_id | 当前题包中的三个关联编号 |
| answer / feedback / evidence | 本人原答、针对原答的核对、真实对话或文件页定位 |
| independent / answer_seen_before_attempt | 是否独立、作答前是否看过提示/答案，均为布尔值 |
| rating | 仅独立尝试填写 again / hard / good / easy |
| reviewed_at / actual_minutes | 可选真实带时区时间和用时；用时未知为 null |

`again` 是未独立答出或实质错误；`hard` 是独立答对但困难；`good` 是独立答对；`easy` 还需要本人明确反馈轻松。讲解后的答案用 `independent: false`，不填 rating。重复记录去重，冲突拒绝覆盖；未答不评级，晚交答案仍保留原题关联。FSRS 只安排间隔，不修改整门课掌握程度。

展示提示或解答时，以 `daily.py answer-shown` 记录 `session_id`、`review_id`、`variant_id` 和实际 `evidence`，可选 `shown_at`。这是曝光记录，不是虚构一次作答；下一次独立检查换新变式。

统一学习流程启用后，旧 `review.py record` 拒绝绕过题包和曝光校验的直接写入。`review.py due --minutes 15` 可作只读预览，与正式题包共用当天剩余容量规则且不写状态。不要绕过 `daily.py` 直接调用内部函数给真实状态评级。

### 保存学习反馈

通过标准输入调用 `daily.py feedback`。必填 `feedback_id`、`completed`、`evidence`；可选 `date`、`recorded_at`、`course_id`、`task_ids`、`actual_minutes`、`blocker`、`next_step`。

`time_scope: session` 表示明确互不重叠的一次学习段；`cumulative` 表示当天累计时间；不明确则省略或为 `unknown`。未知范围不求和，最近一次累计报告不再叠加每题复习时间。自报完成不等于学校提交或独立掌握。一次用时不会自动训练排程参数。

当天有明确总预算和可靠用时反馈时，主计划、正式复习题包（`daily.py` 的 `run/attempt/feedback/answer-shown/mock-attempt` 准备）和 `review.py due` 只读预览共用剩余容量：`planning.remaining_study_minutes` 按档案学习时区，用当天已知总预算（容量折算的学习预算，以及计划中直接记录的总时长；课程范围预算不当作当天总容量）减去可靠用时反馈；反馈按最近一次 `cumulative` 或明确互不重叠且编号唯一的 `session` 合计，逐题复习用时已计入累计反馈时不再重复扣除。容量耗尽后停止新增题，未开始题标为 `deferred` 且 `budget_exempt`，保留已作答、已曝光和 FSRS 历史，不记失败；重复运行或切换课程只沿用当天额度，不增加；未知用时范围或未知总容量返回空值，不猜测。

所有写入均以最新状态为基础，保留上一版本、验证临时 JSON、检查并发冲突后保存。状态写入必须顺序执行，不能并行提交多个命令。

## 考前覆盖与模拟卷

```powershell
# 将 <run_id> 替换为本轮入口返回的运行编号，使用返回的 snapshot_path。
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-sync --snapshot 'Study/_System/Runtime/runs/<run_id>/snapshot.json'
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-intake --snapshot 'Study/_System/Runtime/runs/<run_id>/snapshot.json'
```

采集候选不是确认官方要求。`exam-profile` 登记实际核读的要求，`mock-register` 登记 AI 已核验的原创卷，`mock-attempt` 保存真实作答，`mock-answers` / `mock-answer-shown` 记录答案/提示曝光。输入结构、三种试卷模式和来源变更处理见 [EXAM_PREP.md](EXAM_PREP.md)。这些命令不会自动阅读附件、编题或检查证明。

## 数学核验

向 `.venv/Scripts/python.exe tools/study/math_check.py` 的标准输入传入 JSON，例如：

```json
{"operation":"limit","expression":"(x**2-1)/(x-1)","variable":"x","point":"1"}
```

支持 simplify、evaluate、differentiate、integrate、limit、equivalent。用 `**` 表示乘方；默认符号为实数，可用 `symbols: {"x": "positive"}` 指定正数条件。equivalent 需提供 other；evaluate 可提供 values。

默认双侧极限会分别检查左右侧。化简相等仅指共同定义域，不自动证明两式定义域相同。只接受白名单数学表达式，不用 eval；这不是不受信任输入的资源隔离沙箱，特别复杂的符号运算仍可能耗时。保留题设，结果不替代证明审查；会计借贷和经济模型条件也需 AI 按老师方法核对。

## DeepSeek Harness（DSH）

```powershell
# 只读状态：profile 位置、MCP/技能是否已指向当前项目、聊天身份类型
& '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py status --json

# 合并 DSH profile 配置：只追加 MCP 客户端与 customSkillDirs，保留已有配置并写备份
& '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py install

# 查看聊天身份解析结果（不写任何文件）
& '.venv/Scripts/python.exe' tools/study/chat_identity.py
```

`install` 以**实际 YAML 结构**处理 profile patch（依赖 PyYAML，由 setup 安装）：注释不算配置，合法的空列表 `[]` 会被正常归一化；写入前完整解析并校验合并结果，解析或校验失败时保留原文件不替换；用 `# >>> ... >>>` 标记段幂等更新，重复运行只改本段。遇到同名 `moodle_study` MCP 或用户自定义 `skill-filesystem` 覆盖时默认不覆盖，只提示手动合并；确认旧条目是本项目旧部署时，可用 `install --update-existing` 只更新该条目的 `run.mjs` 路径。`status --json` 的 `configuration_ok` 只表示配置结构正确，`runtime_loaded` 恒为 `unverified`——配置检测不等于运行成功，必须重启 DSH 后在新聊天实际确认。修改后需重启 DSH 才加载。`study.cmd`/`session.py` 的 `--chat` 可省略：按“显式 `--chat` → 环境变量 `STUDY_CHAT_ID`（与本人约定的会话名）→ DSH 主聊天 `DSH_SESSION_ID`”解析；DSH 内部子会话的裸 UUID 会被拒绝，子任务必须显式传主聊天 ID 并加 `--subtask`。完整说明见 [DSH.md](DSH.md)。

在受限 shell（例如 DSH 自己的沙箱）里，`node --test`、部分 Python `tempfile` 测试，以及需要 Node 子进程、DPAPI 解密或网络下载的 `courses` 完整初始化和 `library-sync`，可能因管道/临时目录/进程权限失败；这不代表实现错误。助手应先按 Harness 实际提供的审批机制请求执行同一具体操作，并在当前聊天继续；不把打开 CMD 作为默认解决方法，不自行关闭安全限制。确实无法由宿主执行时才报告具体阻碍，并说明可选的手动备用方式。

## 验证与发布

面向同学的 `start.cmd` 提供中文上手向导，`diagnose.cmd` 默认只做本地诊断；`diagnose.cmd -Online -NoPause` 才会验证一次学校连接，不刷新、不写学习状态。`-Json` 输出不含个人内容的摘要，不自动上传。详见 [START.md](START.md)。新增入口测试随下面的 tests 目录 Python 测试执行。

```powershell
npm test
& '.venv/Scripts/python.exe' -m unittest discover -s tests -p 'test_*.py'
& '.venv/Scripts/python.exe' -m unittest discover -s tools/study -p 'test_*.py'
```

测试使用虚构课程和临时状态，不连接学校，不读取个人 Token。课程级授权与资料同步的新增回归为 `tests/course-library.test.mjs`（Node）和 `tools/study/test_course_library.py`（Python）；按章核对的新增回归为 `tests/chapter-check.test.mjs`（Node）和 `tools/study/test_session.py` 的 `ChapterCheckTests`（Python），都随上面的命令执行。本轮的按需查询回归为 `tests/read-queries.test.mjs`（Node），学习处理回归为 `tools/study/test_question_exposure.py`、`test_material_learning_integration.py`，原答绑定归档回归为 `tools/study/test_evidence_archive_binding.py`，跨校数字 ID 回归分布在 `tests/chat-course-selection.test.mjs`、`tests/course-selection.test.mjs`、`tests/chapter-check.test.mjs`、`tools/study/test_course_library.py` 和 `tests/test_auth.py`。可选 `tests/browser_smoke.py` 只访问本机模拟服务，使用无界面 Edge，不验证学校真实 SSO；真实连接需本人登录后运行 `node tools/moodle/run.mjs check`。

发布前从通用源码文件清单建立提交，检查 `git status`。不得加入 `Study/`、`.private/`、本地配置、依赖环境、个人附件、真实题库或状态备份，不用 `git add -f` 绕过忽略规则。Node 依赖锁在 package-lock.json；Python 固定版本见 requirements.txt。


资料升级、历史引用与经授权的 30 天归档清理见 [MATERIAL_VERSIONS.md](MATERIAL_VERSIONS.md)。默认搜索只读当前版；`context/sync` 可触发已启用的归档维护，实际删除时会更新资料删除标记，学习证据保持不变。


考试来源写入与统一聊天入口共用快照校验。已有较新轮次、范围改变或快照被修改时，旧的 `exam-sync/exam-profile` 会拒绝保存；重读最新独立快照并核读后再提交，不使用旧草稿回退已核验要求。
