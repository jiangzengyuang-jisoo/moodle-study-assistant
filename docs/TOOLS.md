# 工具入口与验证

以下命令在用户的学习项目根目录运行。普通同学通过自然语言提出任务；本页供 AI 和维护者参考。不要在共享技能安装目录运行个人初始化或保存学习状态。

## Moodle

```powershell
node tools/moodle/run.mjs init
node tools/moodle/run.mjs check
node tools/moodle/run.mjs courses
node tools/moodle/run.mjs refresh
node tools/moodle/run.mjs mcp
```

`init` 只创建缺失的配置/空白状态，不覆盖已有资料。`courses` 交互选择跟踪页面，保留未选页面的既有历史。`refresh` 采集到 `Study/_System/Runtime/runs/<run_id>/snapshot.json`，完整成功另存 `refresh-last-complete.json`；不直接更新个人掌握、作业完成或 AI 核读状态。退出码2表示部分采集失败。

AI 教学前使用下述 `verify-chapter` 入口，不因新聊天自动完整 `refresh`。计划/最新通知通过 MCP 按相关课程和信息类型查询；只有明确完整检查或当前任务确需全面覆盖时才用 `refresh`。完整采集器仍最多同时执行4个 API 请求，各论坛分页按顺序读取，保留正文、回复和权限核对。缓存复用保留真实核对时间，不记为新的学校检查。

MCP 暴露课程、作业、测验、论坛、日历、成绩、通知、资源目录和站点信息只读查询；全论坛分页用 `study_all_discussions`，回复用 `study_discussion_posts`。上游 recent discussions 只有最近20条，不能替代完整采集。不注册学校写操作；普通 MCP 查询不下载文件。正常刷新在采集后为有课程级完整授权的范围自动下载、归档和索引；没有授权的范围只同步信息。选课授权、结果和重试入口见 [COURSE_CONTENT.md](COURSE_CONTENT.md)。

`moodle_get_calendar_events` 支持课程、时间范围与分页限制，检查 `complete` 后再判断是否覆盖全部所选事件；`moodle_get_notifications` 用 `limit/offset`、`has_more/next_offset` 继续读取，不标已读。接口缺失不等于没有事件。新增查询的具体范围与限制见 [按需学校查询](UPDATE_REPORT_2026-11-07.md#按需学校查询)。

### 教学前严格核对当前章节

```powershell
study.cmd --chat "original-user-chat-id" verify-chapter --course TEST --chapter 8 --material TEST-CH8-A --material TEST-CH8-B
node tools/moodle/run.mjs verify-chapter --chat "original-user-chat-id" --course TEST --chapter 8 --material TEST-CH8-A
```

示例 ID 为占位，必须先用本地索引找到真实资料。`--material` 可重复或用逗号分隔；`--force` 仅供明确重查。只下载选定课件，比较实际字节；同聊天成功/失败回执复用，不解密凭据或连接学校来重复核对。章节回执不等于全量采集/审阅，不触发考试来源同步。详细合同、权限和异常处理见 [CHAPTER_CHECK.md](CHAPTER_CHECK.md)。

### 明确授权的资料下载

用户明确提出“下载某章课件/某份老师说明”后，先从所选课程的实际模块列表识别对应资源，再调用独立入口：

```powershell
node tools/moodle/run.mjs download 123 --authorized
```

`123` 只是示例模块编号，不能照抄；多个模块必须逐个明确列出。`--authorized` 表示助手已经收到用户对这些资料的明确下载请求，不是程序替用户授权。仅限当前所选课程的 resource/folder 模块；不扩大成全课程抓取，不通过输入任意 URL 获取其他文件。

下载先落到 `Study/_Inbox/`，保留官方文件名，按内容哈希去重，同名不同内容保留为不同文件。成功下载不代表已经核读或掌握。AI 随后检查实际文件、课程归属和页数，按用户目录约定归档并更新 `materials`；下载器本身不替 AI 作语义判断。账号、权限或站点限制导致失败时如实说明，不绕过限制。

## 复习、复测、反馈和当前计划

资料初始化和快速读取另见 [LIBRARY.md](LIBRARY.md)：`tools/study/library.cmd context` 直接读取最新状态，`sync` 构建或更新资料缓存，`search/read/render/outline` 定位并核对完整原页。这些命令不写学习进度、作答或 FSRS。

```powershell
& '.venv/Scripts/python.exe' 'tools/study/daily.py' run
& '.venv/Scripts/python.exe' 'tools/study/daily.py' check
```

默认状态为 `Study/_System/state.json`。`--state 路径` 和带时区的 `--at 时间` 放在子命令前，仅测试/迁移需要；不把演示日期或测试作答写进正式状态。

- `run` 重建当前计划并选择核验过的复习新题。首次默认预算15分钟；以后省略 `--minutes` 沿用当天预算，明确减量才传更小值，包括0。预算计入原学习时间，不因重复运行叠加。新档案无题库时显示待准备。
- `render` 只重建当前状态视图，不创建题包或作答。
- `check` 检查学习时区的日期、状态版本、9份当前系统页面、已登记试卷与模拟题错题区；失败后重读状态并重新运行。不启动后台刷新。
- 队列 `review_queue` 项通过 `mistake_id` 关联真实问题，具体题在 `practice_variants`。每题需有 id、完整 prompt、非空字符串列表 `answer_check`、`estimated_minutes`、材料依据 `source` 和 `verified: true`。`source` 包含 `material_id`、`pages` 与 `detail`。AI 必须实际核验答案；不能只把标志改成 true。答案只供批改，不提前出现在今日待答题单。
- 选择题的 `options` 使用标签到选项文字的字典（例如 `{"A": "选项一", "B": "选项二"}`），不混入答案或批改元数据。选项与题干一同快照保存，并展示在无答案预览和复习页面。旧日包缺选项时保留原记录、标待核对并释放未用预算，再另选已经核验的未用新变式；不从可能已修改的题库猜补，也不据此评为独立作答。

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

统一学习流程启用后，旧 `review.py record` 拒绝绕过题包和曝光校验的直接写入。`review.py due --minutes 15` 可作只读预览。不要绕过 `daily.py` 直接调用内部函数给真实状态评级。

### 保存学习反馈

通过标准输入调用 `daily.py feedback`。必填 `feedback_id`、`completed`、`evidence`；可选 `date`、`recorded_at`、`course_id`、`task_ids`、`actual_minutes`、`blocker`、`next_step`。

`time_scope: session` 表示明确互不重叠的一次学习段；`cumulative` 表示当天累计时间；不明确则省略或为 `unknown`。未知范围不求和，最近一次累计报告不再叠加每题复习时间。自报完成不等于学校提交或独立掌握。一次用时不会自动训练排程参数。

有明确总预算和可靠用时反馈时，计划与复习共用剩余容量：预算耗尽后停止新增题目，未开始题按容量延期，保留已作答与曝光历史。累计反馈中的复习时间不再重复扣除；重复运行不会增加当天题量。

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

在受限 shell（例如 DSH 自己的沙箱）里，`node --test` 与部分 Python `tempfile` 测试可能因管道/临时目录权限失败；完整回归请在真实终端执行。

## 验证与发布

面向同学的 `start.cmd` 提供中文上手向导，`diagnose.cmd` 默认只做本地诊断；`diagnose.cmd -Online -NoPause` 才会验证一次学校连接，不刷新、不写学习状态。`-Json` 输出不含个人内容的摘要，不自动上传。详见 [START.md](START.md)。新增入口测试随下面的 tests 目录 Python 测试执行。

```powershell
npm test
& '.venv/Scripts/python.exe' -m unittest discover -s tests -p 'test_*.py'
& '.venv/Scripts/python.exe' -m unittest discover -s tools/study -p 'test_*.py'
```

测试使用虚构课程和临时状态，不连接学校，不读取个人 Token。可选 `tests/browser_smoke.py` 只访问本机模拟服务，使用无界面 Edge，不验证学校真实 SSO；真实连接需本人登录后运行 `node tools/moodle/run.mjs check`。

发布前从通用源码文件清单建立提交，检查 `git status`。不得加入 `Study/`、`.private/`、本地配置、依赖环境、个人附件、真实题库或状态备份，不用 `git add -f` 绕过忽略规则。Node 依赖锁在 package-lock.json；Python 固定版本见 requirements.txt。


资料升级、历史引用与经授权的 30 天归档清理见 [MATERIAL_VERSIONS.md](MATERIAL_VERSIONS.md)。默认搜索只读当前版；`context/sync` 可触发已启用的归档维护，实际删除时会更新资料删除标记，学习证据保持不变。


考试来源写入与统一聊天入口共用快照校验。已有较新轮次、范围改变或快照被修改时，旧的 `exam-sync/exam-profile` 会拒绝保存；重读最新独立快照并核读后再提交，不使用旧草稿回退已核验要求。
