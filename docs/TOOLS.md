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

`init` 只创建缺失的配置/空白状态，不覆盖已有资料。`courses` 交互选择跟踪页面，保留未选页面的既有历史。`refresh` 采集到 `Study/_System/refresh-latest.json`，完整成功另存 `refresh-last-complete.json`；不直接更新个人掌握、作业完成或 AI 核读状态。退出码2表示部分采集失败。

AI 按新对话开始或用户明确要求调用 `refresh`，不会每条追问重复执行；命令本身始终进行真实采集。采集器全局最多同时执行4个 API 请求，每个论坛的分页仍按顺序读取，完整正文、回复和权限核对保留。不要把没有重新联网的缓存复用记为新的检查。

MCP 暴露课程、作业、测验、论坛只读查询；全论坛分页用 `study_all_discussions`，回复用 `study_discussion_posts`。上游 recent discussions 只有最近20条，不能替代完整采集。不注册学校写操作；默认刷新和普通 MCP 查询不会下载文件。

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

所有写入均以最新状态为基础，保留上一版本、验证临时 JSON、检查并发冲突后保存。状态写入必须顺序执行，不能并行提交多个命令。

## 考前覆盖与模拟卷

```powershell
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-sync --snapshot 'Study/_System/refresh-latest.json'
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-intake --snapshot 'Study/_System/refresh-latest.json'
```

采集候选不是确认官方要求。`exam-profile` 登记实际核读的要求，`mock-register` 登记 AI 已核验的原创卷，`mock-attempt` 保存真实作答，`mock-answers` / `mock-answer-shown` 记录答案/提示曝光。输入结构、三种试卷模式和来源变更处理见 [EXAM_PREP.md](EXAM_PREP.md)。这些命令不会自动阅读附件、编题或检查证明。

## 数学核验

向 `.venv/Scripts/python.exe tools/study/math_check.py` 的标准输入传入 JSON，例如：

```json
{"operation":"limit","expression":"(x**2-1)/(x-1)","variable":"x","point":"1"}
```

支持 simplify、evaluate、differentiate、integrate、limit、equivalent。用 `**` 表示乘方；默认符号为实数，可用 `symbols: {"x": "positive"}` 指定正数条件。equivalent 需提供 other；evaluate 可提供 values。

默认双侧极限会分别检查左右侧。化简相等仅指共同定义域，不自动证明两式定义域相同。只接受白名单数学表达式，不用 eval；这不是不受信任输入的资源隔离沙箱，特别复杂的符号运算仍可能耗时。保留题设，结果不替代证明审查；会计借贷和经济模型条件也需 AI 按老师方法核对。

## 验证与发布

```powershell
npm test
& '.venv/Scripts/python.exe' -m unittest discover -s tests -p 'test_*.py'
& '.venv/Scripts/python.exe' -m unittest discover -s tools/study -p 'test_*.py'
```

测试使用虚构课程和临时状态，不连接学校，不读取个人 Token。可选 `tests/browser_smoke.py` 只访问本机模拟服务，使用无界面 Edge，不验证学校真实 SSO；真实连接需本人登录后运行 `node tools/moodle/run.mjs check`。

发布前从通用源码文件清单建立提交，检查 `git status`。不得加入 `Study/`、`.private/`、本地配置、依赖环境、个人附件、真实题库或状态备份，不用 `git add -f` 绕过忽略规则。Node 依赖锁在 package-lock.json；Python 固定版本见 requirements.txt。
