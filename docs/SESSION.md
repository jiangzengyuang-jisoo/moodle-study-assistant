# 聊天入口、保存与恢复

这是给使用本仓库的同学和 AI 助手的第一阶段升级。保留原有课程、进度、错题、FSRS、资料版本及原页核验流程，不要求重新建档。

## 日常入口

先运行 `setup.cmd`。之后按任务使用下列入口。普通章节教学先本地准备，再优先调用 Moodle MCP `study_check_chapter`；下方 `chapter-check` 是异常 CLI 后备，不是正常教学的必经命令：

```powershell
.\study.cmd --chat "原始用户聊天ID" continue
.\study.cmd --chat "原始用户聊天ID" today
.\study.cmd --chat "原始用户聊天ID" refresh
.\study.cmd --chat "原始用户聊天ID" status
.\study.cmd --chat "原始用户聊天ID" chapter-check --course 课程ID --section "第8章" --mode strict
.\study.cmd --chat "原始用户聊天ID" teaching-event     # 标准输入一个小 JSON，只交本轮教学位置变化
```

- `continue`：只在当前请求需要校务信息（作业要求、截止日期、公告、讨论）、学习计划或考前准备时使用；普通章节讲解且本地已有该章资料时不用它（走 `library.cmd context --read-only` + `library.cmd chapter-prep` + `study_check_chapter`）。先输出最新本地学习接续，本聊天首次调用再完整检查学校信息，后续复用。
- `today`：上述流程加上今日复习与页面更新。学校证据未核读时标明 `school_evidence_pending`，不能当作刚核验过的计划。
- `refresh`：本人明确要求检查最新学校信息或重试失败时使用。
- `status`：查看采集、待核读、中断、失败或已核读状态，不联网。
- `study_check_chapter`：正常章节核对的 Moodle MCP 工具，固定 strict；必填 `course_id`、原主聊天 `chat_id`，用 `section` 或 `module_ids` 指明本章；仅明确重查时 `force: true`。工具本机保存章节回执，不改学习状态或学校信息。详见 [工具合同](TOOLS.md#章节核对工具study_check_chapter)。
- `chapter-check`：仅工具缺失或 MCP 服务启动/传输故障时的 CLI 后备；相同范围与聊天 ID，不因业务拒绝或审批拒绝换路径。它只核对某一章已有资料与学校文件是否一致（`strict` 比较内容哈希，`quick` 只看大小/修改时间），联网范围限于该课程目录和本章文件；先用本地资料索引与来源映射定位本章模块，匹配不唯一时返回候选和原因，不按数字相同选择整个区块。默认 `strict`；`--modules` 可直接指定模块，本人明确要求重查时加 `--force`。同一聊天、账号、课程、范围和本地文件内容哈希未变时复用最近一次严格回执，不重复联网；本地新增本章资料只核对新增部分，失败或部分完成不算全部核验成功，也不因普通追问自动重试。受限环境的 `STUDY_TOKEN_DECRYPT_FAILED` 例外按 [DSH 单次授权恢复](DSH.md#授权解密失败时申请单次重试) 主动申请原命令重试，获批才执行，最多一次；不因此改用 `refresh` 或 `--force`。不读作业、公告、论坛或日历，不写学习状态、不建索引、不产生核读回执；已有本地资料时开始新一章用它，校务信息仍走 `continue`。判定和处理见 [WORKFLOW.md](WORKFLOW.md#单章资料核对不触发完整采集)。
- `teaching-event`：只交本轮教学位置变化的小事件保存入口；程序读最新状态、合并、校验、原子保存并至多重建一次视图，成功后不必再跑 `daily.py run/check`。完整字段、禁止项与幂等语义见下文[教学位置事件](#教学位置事件teaching-event)。
- 教或复习某一章的资料准备用 `tools/study/library.cmd chapter-prep --course 课程ID --section "Topic 6"`（或用 `--modules`）：一次只读返回本章材料/unit 清单、headings 目录、预算内整页正文与 `omitted` 补读命令、公式图表候选页（复用哈希渲染缓存）和本章接续，减少“读一页—再决策”的往返。它的 `strict_check.last_receipt` 只是上次严格回执背景，**不是本轮核验**；本章文件仍须优先用 `study_check_chapter` 严格核对；`strict_check.command` 是 CLI 后备，工具成功后不再双跑，公式/图表仍看原页，整章覆盖仍要逐 unit 核对；批量准备整章不代表一轮讲完整章，保存只对应本轮实际讲解单元。详见 [LIBRARY.md](LIBRARY.md#一次准备一章) 与 [TOOLS.md](TOOLS.md#一次准备一章chapter-prep)。

无关问答不调用以上入口，也不预读学习状态。只维修连接时使用 `diagnose.cmd`（已授权联网才加 `-Online -NoPause`）；确需了解接续时用 `status` 或 `tools/study/library.cmd context --read-only`，不调用会触发完整采集的 `continue/today/refresh`。这些只读查看和诊断不会占用首次采集机会，同一聊天以后开始学习仍正常采集。

任务范围由助手按请求判断，命令本身不识别自然语言；直接执行 `continue` 仍有首次采集语义，`study_check_chapter` 与 CLI 后备 `chapter-check` 则始终只核对指定章节、不触发完整采集。保存了考试信息或长期目标包含考试表现，不把普通章节讲解自动转成备考；讲解范围由当前请求与本章资料决定，不由考试背景决定。新版规则不强制改写已有档案的偏好或重建会话，明确的用户自定义安排继续保留。

普通接续复用已有索引；实际采集后的资料阶段只处理有课程级完整授权的范围，按变化下载并增量索引，不降低原页、公式、图表及全章覆盖要求。第一行 `local_ready` 表示本地接续可用，**不代表联网已完成**；不要等网络结束才读取这行输出。资料阶段有独立超时和结果，失败不冒充初始化完成，也不撤销已成功采集的快照；见 [COURSE_CONTENT.md](COURSE_CONTENT.md)。`chapter-check` 的回执不是独立快照，不能用于 `review`、考试来源或覆盖检查，只能说明本次核对的那些文件；匹配不唯一时返回的候选也不是核对结论。

### 聊天身份必须明确

`--chat` 必须由当前客户端/主助手提供原始用户线程 ID。重新打开、跨日、压缩上下文都沿用原 ID；真正的新聊天使用新 ID。内部任务继承原始 ID，并加 `--subtask`，只可 `continue/status`，不会触发联网或学习写入。不要拿子任务自己的进程/线程 ID 作为新用户聊天。

不自动猜测环境变量，也不生成随机聊天 ID。无法取得稳定 ID 的客户端，首次由用户明确选择一个会话名称，后续继续沿用；需新会话时明确更换。这是入口调用约定，不宣称兼容所有客户端的自动线程识别。工作区由所在目录隔离，同名聊天不会串到另一个项目。

### DeepSeek Harness（DSH）

- DSH 由客户端在每次 shell 调用中注入 `DSH_SESSION_ID`：主聊天为 `session-<uuid>`，同一聊天跨轮、重开和上下文压缩保持不变；内部子会话/子代理得到的是裸 UUID，不能当原始聊天 ID。
- 未显式传 `--chat` 时，`study.cmd`/`session.py` 依次使用：显式参数 → 环境变量 `STUDY_CHAT_ID`（与本人约定的会话名称，作为无法取得稳定 ID 时的可恢复替代）→ DSH 主聊天 ID（自动）。仍然不生成随机 ID，也不接受所有聊天共用一个固定 ID。
- 内部任务必须由主助手显式传入主聊天 ID 并加 `--subtask`；入口检测到自动推断的内部会话 ID 会拒绝执行子任务，避免把同一聊天拆成新的首次采集。
- DSH 的一次性 MCP/技能接线、重启和诊断步骤见 [DSH.md](DSH.md)。

## 采集和核读分开

每轮先固定 `Study/_System/Runtime/runs/<run_id>/request.json` 中的课程映射、站点和加密凭据指纹，以及采集需要的已登记任务来源链接，再将结果写入同目录的 `snapshot.json`。采集器直接使用固定请求，不重新读取可能变化的选课状态；快照回写请求哈希，核读时核对实际课程覆盖。清单保存哈希、范围指纹、序号与阶段；快照写完后不修改。`refresh-latest.json` 和 `refresh-last-complete.json` 仅为兼容旧工具的副本，不能用来核读尚在处理中的某一轮。

1. 阅读入口返回的**本轮独立快照**，比较已保存来源，逐项核读新增/修改内容及必要原页。完整采集不等于完成核读。
2. 再读最新 `state.json`，保留无关内容、学习进度、错题与历史。用 `continue` 返回的 `state_sha256`，或 `state_io.digest` 对实际读取的完整状态计算同一哈希；不要使用过期的首次输出哈希。
3. 在本地临时 JSON 文件中准备完整草稿（文件留在被忽略的 `Study/` 或 `tmp/` 下）：

```json
{
  "reviewed": true,
  "base_sha256": "本次合并依据的完整状态哈希",
  "state": {"这里应为完整的最新状态及核读更新": "不要只传增量"}
}
```

4. 用 UTF-8 标准输入交给 `study.cmd --chat "原始用户聊天ID" review --snapshot-hash "本轮哈希"`。它会重新核对状态、快照、账户/课程范围和轮次，执行考试来源变化同步，然后保存核读回执并重建页面。示例 JSON 仅表示结构，不能直接运行。

不完整采集不能提交“完整核读”回执；旧来源和学习记录保留，既有考试规格会标为待核读，阻止直接沿用出完整模拟卷。失败标记有独立回执，重试不会重复保存。课程或账户范围改变时会显示学校证据待核读，即使这段聊天曾核读成功；仍需明确刷新，不自动重新联网。已有新轮次时旧轮次不能回写。状态变化则重读并重新合并，不自动覆盖。通用完整状态草稿只能原样保留考试规格；需要修改 `exam_profiles` 时必须使用绑定快照的专用考试登记或会话核读入口。单独执行 `daily.py exam-sync/exam-profile`（以及任何带快照的来源写入）也会在写锁内核对当前轮次、请求绑定、快照内容和账户/课程范围，旧快照不能绕过统一入口覆盖新要求。启用过新协议后缺少或损坏运行记录时会拒绝来源写入，需恢复记录或明确重新刷新；只有从未启用新协议的旧档案保留旧入口兼容。回执只证明工具验证了来源绑定和保存过程，无法证明 AI 是否认真阅读；教学证据标准仍必须执行。

## 保存不重复、冲突不覆盖

- Python 学习写入、资料升级/清理以及 Node 建档/选课统一使用工作区操作系统文件锁。顺序是聊天锁 → 工作区锁 → 资料/缓存锁；网络采集、AI 核读不占用工作区写锁。进程结束后锁自动释放，锁文件存在不表示仍有人占用。
- `daily.py attempt/feedback` 自动沿用原 `attempt_id/feedback_id` 作为操作编号。其他需要重试的事件先用 `study.cmd --chat "ID" operation-id` 分配编号，提交给 `daily.py --operation-id "编号" ...`；重试必须沿用相同编号和内容。
- 同一操作的请求摘要与结果随学习状态一起提交。即使运行记录没来得及更新，状态回执仍能判断已保存；重试只补页面，不再计一次作答、反馈或 FSRS 评级。同编号不同内容报冲突。
- 页面生成失败后可重试原命令，或用 `daily.py render` 重建视图；不要重新生成事件编号来“解决”重试。
- 完整草稿不能仅保留编号而遗漏记录详情。原答案、既有来源值、FSRS 历史和已提交学习事件不允许由普通草稿改写；来源可保留原值补充页码、核验详情或追加来源；应通过专用学习工具处理，或追加更正说明。正常章节推进、当前页切换、选课变更和追加记录仍允许。四种当前题字段 `pending_question/pending_check/pending_questions/pending_checks` 使用同一规则：未作答题可替换；已有实际作答的题目必须完整保留在当前记录，或先追加到同课程 `teaching_cursor.checks`，再清空/替换；同一题多次作答可作为不同历史记录追加，已有历史不能删除或改写。不能把原答案放进无关字段或别的课程来绕过保护。
- 所有正式状态写入在共享锁内先检查未完成资料事务，恢复成功后再读状态；遇到本地原件被修改则停止写入并保留事务，不能覆盖用户改动。
- AI 其他状态更新应通过 `tools/study/state_io.py <state路径> replace`，标准输入 `{ "expected": 完整读取的旧状态, "state": 完整合并的新状态 }`。过期草稿会被拒绝。不要直接改写 `state.json`；任意外部编辑器直接写入不受锁保护。**纯教学位置变化不再走这条路**：用下面的 `teaching-event` 只交本轮变化，不再手写整状态脚本或提交完整状态草稿。

### 教学位置事件（teaching-event）

讲完一章、推进或切换教学位置时，用 `study.cmd --chat "原始用户聊天ID" teaching-event` 保存。它接受标准输入的一个 JSON 小事件，只描述本轮变化；程序在共享锁内读最新状态，合并、刷新计划元数据、执行完整草稿校验（`validate_draft`）、原子保存，并在同一事务内至多重建一次 9 份视图。**成功后不需要再运行 `daily.py run/check`**，也不会覆盖别的课程或已有学习证据。

```powershell
# 事件 JSON 放在被忽略的 tmp/ 或 Study/ 下；下面是结构示例，值必须来自本轮实际教学
$event = @'
{
  "course_id": "moodle-100000",
  "fields": {
    "chapter": "Topic 6: Aggregate Production",
    "topic": "总量产出与人均产出的区别",
    "stage": "first_explanation_delivered_pending_check",
    "next_step": "等本人回答理解检查，再按错误补讲",
    "material_id": "moodle-100000-SOURCE-501-<哈希>",
    "pages": [1, 2, 3],
    "pending_check": {"id": "topic6-check-1", "prompt": "为什么人口增长能提高总产出却通常不能提高人均产出？", "status": "unanswered"},
    "user_request": "讲解 Topic 6",
    "coverage_note": "本轮仅讲总量产出与人均产出的区别，其他单元尚未讲解"
  },
  "onboarding": "shown"
}
'@
$event | Set-Content -Encoding UTF8 'tmp/teaching-event.json'
Get-Content -Raw -Encoding UTF8 'tmp/teaching-event.json' | .\study.cmd --chat "原始用户聊天ID" teaching-event
```

- **顶层键**：`course_id`（必填）、`chat_id`（可选；由入口注入，若显式提供必须与当前聊天一致，手填别的聊天会被拒绝）、`event_id`（可选）、`fields`、`checks_append`、`review_backlog_append`、`archive_pending`、`onboarding`。未知顶层键一律拒绝；事件至少要有一项变化。
- **`fields` 允许**：`chapter`、`topic`、`stage`、`next_step`、`material_id`、`pages`、`source`、`pending_check`、`user_request`、`lesson_scope`、`worked_example`、`coverage_note`。其中后四项是描述性课程记录，可创建或更新，**不是掌握证据**；未提供的字段保留旧值，不会被删除。`source` 在同章内只能追加新键，已有来源值不能改写；换章时可整体替换来源（旧来源已随旧位置快照存入 `history`，可追溯）。
- **`checks_append`**：追加已完成记录（含真实作答的完整题目记录），只追加、相同记录自动去重，最多 5 条；不能借它改写已有 `checks/history`。**`review_backlog_append`**：追加旧章待复习项（`id/topic/chapter/status/next_step`，`status` 目前只接受 `pending`），最多 3 条，编号重复且内容不同会拒绝。
- **`pending_check`**：只表示待作答，不能携带实际作答或评分。当前待答题已有实际作答时，必须先在事件里用 `checks_append` 带上该完整记录（或传 `archive_pending: true` 且同时给 `fields.pending_check`，`null` 表示清空并保留旧题），否则拒绝；未作答的旧题可以直接替换。
- **`archive_pending`**：布尔；为 true 时必须同时提供 `fields.pending_check`。
- **`onboarding`**：`shown` 或 `skipped`；只应在教程已实际展示后才登记，已登记过则不重复写入（返回 `onboarding_status: "already_recorded"`）。只有 onboarding 的事件不会创建空教学位置。
- **换章**：`topic` 或 `chapter` 与当前值不同时，自动把旧位置快照追加到 `history`（只追加一次、不递归复制 `checks/history`），旧记录不删；旧章待复习项用 `review_backlog_append` 或历史快照保留。
- **禁止项**：掌握度/评级、既有 `checks`/`history` 改写、`topic_evidence`/`evidence_bindings` 等证据集合、考试与作业记录、其他课程、未知键、超过字段长度/条数上限的巨型事件。需要这些改动时用对应专用入口，不要塞进教学事件。
- **结果与重试**：stdout 一行 JSON，`status` 为 `recorded`（已保存）、`unchanged`（合并后与最新状态相同，不写盘、不重建视图）或 `duplicate`（同内容重试命中上次回执，只补视图）；另有 `course_id`、`operation_id`、`revision`、`state_written`、`views_updated`、`changed_fields`、`pending_check_id`、`history_appended`、`onboarding_status`、`created_cursor`、`pending_archived`、`mastery_updated: false`。内容不变的重试可直接重跑：省略 `--operation-id` 时编号由“聊天 + 事件内容”派生，同内容得到同编号；需要固定编号时先用 `study.cmd --chat "ID" operation-id` 分配，再用 `--operation-id` 提交，重试必须沿用原编号和内容；同编号不同内容报冲突。视图生成中途崩溃时学习状态已提交，重试只补视图，不会重复保存。内部任务（`--subtask`）只允许读取接续和状态，不能写教学事件。

## 中断恢复与维护

重新进入同一聊天时，完整落盘且请求绑定有效的快照可以继续核读；父进程中断、子进程稍后才写完的同轮快照也会在下一次本地检查被识别，不重新采集。尚未落盘则标中断/失败，需明确 `refresh` 重试，不因等待超时而自动联网。升级前没有固定请求绑定的旧快照不能补签成可信核读，需明确刷新一次；个人学习记录继续保留。核读状态已提交、页面尚未完成时，下一次接续自动补页面。

本聊天首次进入学习和明确刷新时执行已授权的归档维护，资料同步也保留原维护入口；同一聊天普通追问走只读接续。可单独运行 `study.cmd --chat "ID" maintain`。未授权开启保留期则不删除；已开启时仍仅清理满 30 天、没有引用、未修改且有有效替代版的归档。没有后台任务。

## 耗时与质量

`Study/_System/Runtime/timings.jsonl` 只记阶段、耗时、成功与否、时间，不记题目、答案、聊天 ID、凭据和错误正文。覆盖本地接续、联网采集、状态保存、页面生成、资料查询/原页渲染、归档维护。失败和重试表现为各次阶段记录，不把工具间空档算成“模型思考时间”。不记录整段聊天首字延迟。

正常 MCP 核对等待 `study_check_chapter` 的原调用；以下仅适用 CLI 后备：`chapter-check` 在 stderr 输出简短阶段进度，stdout 只保留最终 JSON；不要用 `Select-Object -Last 40` 等管道截断隐藏进度，也不要把进度行当结果。真实联网的首次核对较慢时，助手可经 Harness 后台任务运行同一条命令并读取任务输出跟进，继续等待原任务、不重复启动；已严格核对且本地未变的同聊天核对应在数秒内返回且不联网。命令本身不做后台轮询，不因为等待而自动重试失败项；受限执行的授权解密错误仅由助手按 [单次授权恢复](DSH.md#授权解密失败时申请单次重试) 申请一次原命令重试，客户端批准后执行。

快速读取也可用 `tools/study/library.cmd context --read-only`；此选项不修改学习状态或清理资料，但会写本地耗时日志。原 `context` 行为兼容保留。

验证使用虚构档案和模拟学校响应，覆盖重复聊天、子任务、显式刷新、失败不重试、快照被改、并发保存、晚返回的旧轮次、进程被终止及保存后页面失败。真实学校网络速度、客户端重新启动/压缩的 ID 传递和 AI 核读质量仍需实际环境验证，不能从模拟测试推断保证。
