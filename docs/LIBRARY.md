# 课程资料缓存与快速启动

用户继续自然说“接着复习这门课”即可。助手负责以下流程，不需要用户运行命令。示例中的 COURSE101 和资料 ID 必须替换为本人档案中实际存在的编号。

`state.json` 仍是唯一学习状态。这里的缓存只保存资料定位、抽取文字、OCR候选和原页图片，不写进度、错题、FSRS评级或学校状态。所有资料仍以原件为准。

课程资料的自动初始化、增量同步和缺口记录见 [课程级资料授权与自动同步](COURSE_LIBRARY.md)：选课时保存课程级完整授权后，`node tools/moodle/run.mjs library-sync` 负责从 Moodle 枚举、下载、归档并登记资料，最后统一重建下列 catalog；`library.cmd sync` 只按 state.materials 重建解析缓存。两者都不写掌握状态。

## 跨章节学习证据定位

判断某个概念是否讲过前，可运行 `tools/study/library.cmd evidence --course 课程ID --query "知识点或别名"`。它从最新状态搜索本科的知识观察、历史笔记、教学游标及历史、待复习项、覆盖核对、反馈、错题和复习作答；不按当前章节过滤。

查询按完整短语、明确中英文别名、关键词排序，返回命中类型、展开词、原记录路径及内容指纹；不是向量语义检索。未命中时换关键词并按返回范围补读，不能说“没学过”。默认最多 8 条（`--limit` 为 1–20），输出有大小上限并说明截断。排序不代表掌握程度，特别核对 `corrects` 和 `memory_corrections`，不取最高等级代替当前判断。需要原文时用 `detail path /…` 做内部证据核验，注意完整详情可能含原答或标准答案。

询问“记得我学到哪里、依据是什么”时用 `memory --course ID`，历史问题加 `--query`；纠正通过受版本校验的追加入口保存，详见 [学习记忆](MEMORY.md)。

返回的旧观察如有 `topic_corrections`，必须核读关联更正，不能只看排序靠前的旧等级；即使更正未命中关键词或被结果上限截断，关联提示仍保留。记忆位置的路径指向已选中的接续，顶层原记录另保留入口；纠正快照仅供显式来源核读。

该命令完全只读，不写计时日志、不维护归档、不刷新学校、不修改状态；不会把旧笔记自动迁移成掌握证据。字段与教学意图约定见 [教学规则](TUTORING.md)。

教学接续使用共用的 `teaching_progress` 读取口径；有实际变化时通过 `teaching-save` 保存并回读，漏记的原聊天按需核对。命令和证据边界见 [MEMORY.md](MEMORY.md#教学接续保存与漏记检查)。

## 一次准备一章

教或复习某一章时，先用一次只读调用批量准备，再按需补读：

```powershell
& 'tools/study/library.cmd' chapter-prep --course COURSE101 --section "Topic 6"
& 'tools/study/library.cmd' render COURSE101-LECTURE1 --units 34,35,36
```

`chapter-prep` 一次返回：本章材料/unit 清单（含 `unit_range`、源哈希与 `strict_scope`）、headings 目录、预算内的整页正文（`--text-budget` 默认 60000 字符，超出部分在 `text.omitted` 里给补读命令）、公式图表候选页（`--render auto` 默认渲染优先级最高的 `--render-limit` 页并复用哈希渲染缓存）、本章接续（当前教学位置、本章待答题题干、本科本章相关待复习项；答案隐藏，不返回作业/考试/任务集合）和上次 strict 回执摘要。输出为一行 JSON，便于直接读取。

边界：

- 命令只读：不联网、不写学习状态、不写核读回执、不建索引；只可能在 `Library/renders/` 新建派生页图。与其它 library 命令一样，它仍会写一行不含题目/答案/聊天 ID 的本地耗时日志。`--read-only` 是兼容标志。
- `strict_check.last_receipt` 只是**上次**严格核对的背景，**不是本轮核验**；本章文件仍须优先调用 Moodle `study_check_chapter` 严格核对，并显式传原主聊天 `chat_id`。`chapter-prep` 的 `strict_check.preferred` 为 `mcp`、`strict_check.tool` 给出工具与已知参数；若 `missing_arguments` 含 `chat_id`，补本轮真实主聊天 ID 后再调用，不传占位符。`strict_check.command_role: fallback_only` 说明 `strict_check.command` 是异常 CLI 后备；`strict_check.required: true` 表示仍需实际核对，不是强制走 shell，MCP 成功后不双跑。参数与后备条件见 [TOOLS.md](TOOLS.md#章节核对工具study_check_chapter)。
- `coverage.verified` 恒为 false，`visual_review_required` 只是待看候选；公式、图表、表格、题号必须看原页，整章覆盖仍要逐 unit 核对文字或原页。准备资料不等于核读或掌握。
- 源文件或缓存不新鲜时返回 `needs_sync` 而不返回旧文本，先按 `cache.sync_command` 同步/重建再准备。
- `render --units` 一次校验来源后批量渲染/复用多页；`--unit` 单页用法继续兼容。`search`、`read --start/--end` 仍用于定向补读，不是每次都要逐页往返。

参数、输出块与缓存字段见 [TOOLS.md](TOOLS.md#一次准备一章chapter-prep)。

## 每次复习

快速顺序：`context` 看接续 → `chapter-prep` 一次准备本章材料 → 需要时 `read`/`render --units` 补读或看原页；`search` 用于定向定位。从工作区根目录运行：

```powershell
& 'tools/study/library.cmd' context --course COURSE101
& 'tools/study/library.cmd' chapter-prep --course COURSE101 --section "Topic 6"
& 'tools/study/library.cmd' search '示例知识点' --course COURSE101
& 'tools/study/library.cmd' read COURSE101-LECTURE1 --start 7
& 'tools/study/library.cmd' render COURSE101-LECTURE1 --units 7,9
```

1. `context` 直接读最新状态，带版本、待答题、教学接续、作业重点和遗漏项路径。没有课程参数时只返回课程总览。返回的 `request_policy` 说明当前请求优先于长期课程安排与考试背景；orientation、作业、截止日期和考试信息只是背景，不替代本章教学主线。不能把摘要当成完整个人历史；相关 `orientation`、`assistant_notes`、讨论与错题用 `detail` 补读。计划另读 schedules/settings 和相关任务。
2. `search` 用标题、页码索引、逐页文字、中英常用术语、PPTX备注和可用OCR找候选页。返回来源类型、完整路径、哈希和质量标志。关键词未命中不等于没有该知识；换原文术语、查看章节目录或扩大范围。
3. `read` 返回完整页面，默认前后各一页，不截断正文；题设、定义、证明或图表跨页时继续扩大。`--start 6 --end 8 --around 1` 返回第5–9页。OCR与speaker notes单独标示，不能冒充正文。
4. `render` 返回缓存的原PDF页图；助手用图像工具实际查看公式、表格、图示、手写和局部“不考”注记。图片文件直接返回原件。PPTX/DOCX返回原文件入口与slide/section编号；本工具没有把它们伪装成已经视觉验证的PDF。在 DeepSeek Harness 中，把返回的 `path` 交给 `read_image` 查看；渲染结果 `verified:false`，看到原页不等于核读通过或掌握。

`detail mistakes 记录ID` 或 `detail path /courses/0/teaching_cursor` 精确补读完整状态记录；索引路径按本次返回的state revision使用，变更后重取。`context --detail` 会包含本科完整记录和答案，仅用于需要完整证据的内部核查或已授权批改。默认摘要隐藏结构化答案/评分字段，但自由文本可能提到结论，不能直接当成面向学生的无答案题面。

跨章经历用 `evidence --course COURSE101 --query "知识点"` 定位。真实复习作答即使旧记录没有直接课程 ID，也会按复习会话和题目关联定位；归属冲突时不猜课程。已登记知识观察的原答用 `evidence-source --course COURSE101 --observation 观察ID` 读取：按同课程内容绑定定位，换题归档或课程重排后不盲读旧数组路径。无可靠旧绑定返回待核对，不能据此判断未学或已掌握。两个 evidence 命令均只读、不计时落盘、不联网、不触发维护；`evidence-source` 明确包含原答，不能作为给同学的无答案题面。

答案资料默认不进入搜索、正文和图像展开。用户明确索取答案或进行批改时使用 `--include-solutions`，并按原学习流程记录曝光；工具读取本身不生成作答记录。来源类型为助手整理、第三方或未知时须明确区分，教学结论回到课程原材料。

## 全章与全科复习

```powershell
& 'tools/study/library.cmd' outline --course COURSE101
```

以当前请求范围（本章/作业/考试范围）与全部相关资料、章节和教学接续建立覆盖清单，再逐节读取。目录包含无文字、未索引和失效项目，不把top-k搜索结果、抽取成功或现有page_map当作全覆盖。DOCX/HTML的编号是结构段，PDF是物理页，PPTX是演示顺序slide。

讲义与slides可能互补；原页中被文字抽取遗漏的图形依然存在。抽取/OCR结果始终 `verified:false`。历史原页审阅仅在来源哈希一致时可沿用其明确范围，不等于本次全文检查，更不表示学生掌握。

## 增量维护

```powershell
& 'tools/study/library.cmd' status --check-sources
& 'tools/study/library.cmd' sync --course COURSE101
& 'tools/study/library.cmd' ocr --course COURSE101
```

首次初始化 `sync` 无课程参数，登记现有全部materials；以后只在新增、变更或缓存故障时重建本科。每次使用校验源文件完整SHA-256和缓存摘要，文件大小/修改时间相同也不能绕过；失效时拒用旧文本。`sync` 只重解析变化文件。抽取器行为改变须提高 `EXTRACTOR_VERSION` 后同步，不能无版本变更复用旧结果。

下载成功、解析成功、已核读和已掌握是四个不同状态：`library-sync` 报告 `installed`、`catalog` 状态为 `indexed`，都不等于核读或掌握，也不会写 `verified` 或 FSRS。文字抽取与 OCR 始终未核验，公式、图表和题号要核对原页；音视频与压缩包只归档不解析，索引中标注 `unsupported_format`、`unsupported_archive` 等警告。缺口逐项保存在 `Study/_System/LibrarySync/资料缺口.md` 和 `latest.json`，未列出的资料不等于不存在，未获取项会写入口和原因。

`ocr` 使用Windows本机识别，补PDF无文本页及图片文件，按源哈希、单元、语言与OCR版本复用。默认 `en-US`，需要中文时传 `--language zh-Hans-CN`；未安装语言会报错，不自动安装。OCR保留原图与错误信息，不覆盖抽取正文；数字、符号和表格列序可能有错，必须看原页。识别空字符串也不能证明页面为空。新增DOCX/PPTX的嵌图仍按原件视觉读取。

`Study/_System/Library/catalog.json` 是全库累计索引，包含当前版本行和 `_Archive` 归档版本行；报告里的 `materials` 总数不等于某一门课的当前材料数，单科当前材料以 `state.materials`、`outline --course` 或缺口清单中的逐课程统计为准。

需要校务信息的任务在每个新对话首次进入时完整检查一次，开始教某一章时只核对本章文件（优先 `study_check_chapter`，`chapter-check` 为异常 CLI 后备，见 [WORKFLOW.md](WORKFLOW.md#单章资料核对不触发完整采集)）；用户明确要求刷新或查询最新学校信息时再次完整检查。同一对话后续消息复用已核验结果，切换课程、跨日、上下文压缩和重新打开旧对话不另触发，不设固定间隔。网络只读采集使用全局最多4个并发请求，各论坛分页顺序、正文/回复数量和权限检查保留。需要刷新时，只有把刷新命令作为 Harness 后台任务（`run_in_background`）启动后，才能在等待期间用 `job_output` 读取结果并继续本地 context/chapter-prep 准备；前台 shell 调用是独占屏障，多个 `pwsh` 不会并行，别把“同一消息发两条命令”当作并行。依赖此次学校检查的结论须等刷新与变更核读完成。刷新成功不自动表示内容已审阅，复用旧结果不写成当前刚完成检查。

## 学科合并与缺口

同一科的公共课程页和个人班级页可归入一个学科：MATH1013 页面 `146265`（主）+ `142648`（副）按 `moodle-146265` 组织，ECON1210 `146739`（主）+ `146748`（副）按 `moodle-146739` 组织。主学科课程记录保存 `linked_page_ids`，副页面用 `merged_into` 指向主学科；材料的 `course_id` 记主学科，`source_pages` 保留各页面来源。`search`、`outline` 的 `--course` 以及 `library.py sync --course` 传副页面或 state 课程 ID 时会沿 `merged_into` 解析到主学科再处理；`read`/`render` 用 material ID 读取，材料本身的 `course_id` 已是主学科。

资料缺口以 `Study/_System/LibrarySync/资料缺口.md` 为准：逐课程列出未获取、未解析或解析有警告的项目及原因，详细字段在 `latest.json`。缺口表示本次授权范围内没有取得或无法安全获取，不等于课程没有该资料；需要时按 [COURSE_LIBRARY.md](COURSE_LIBRARY.md) 的入口人工处理。

## 文件与恢复

- `Study/_System/Library/catalog.json`：来源清单、页码定位、版本、哈希和缺失状态。
- `cache/`：按文件内容与抽取器版本保存的完整逐页抽取。
- `renders/`：按原文件哈希保存PDF页图与图像校验记录。
- `ocr/`：独立的OCR候选与摘要；不写回原件或学习状态。
- `last-build.json`：最近同步统计。

缓存可从原文件重建；故障时运行 `sync --course … --force`，OCR故障使用 `ocr --course … --force`。不要删除、重置或拿旧state备份覆盖当前学习进度。`--cache-dir` 仅允许指向专用Library目录内部，防止把派生文件混入原材料。

升级前在自己的安全位置备份 `Study/`。仅在确需回退时恢复对应程序和规则；后续学习记录应保留并合并，不能直接用旧 state 覆盖新进度。

`library.cmd` 使用项目自己的 `.venv/Scripts/python.exe`；缺失时先运行 `setup.cmd`。PDF 渲染使用 Poppler 或 pypdfium2，OCR 需要 Windows PowerShell 5.1。不依赖作者的客户端安装路径，不新增联网服务或后台任务。

## 安装与兼容

`setup.cmd` 安装 pypdf、pypdfium2 和 Pillow，并为已登记资料建立缓存；空白档案得到空目录，不带作者数据。已有安装更新代码后重新运行 setup.cmd，保留自己的 Study/，不要覆盖 state.json。PDF 渲染优先使用已安装的 Poppler，缺少时使用随 Python 环境安装的 pypdfium2；两者都从原 PDF 渲染。Windows OCR 需要系统已有语言支持，默认不自动安装语言包。

文档解析覆盖 PDF、PPTX、DOCX、HTML、文本与图像等支持格式；详见命令输出中的类型、遗漏和错误。归档后的材料须先登记到 state.materials；缓存不自动把文件目录当作完整课程范围。所有缓存和渲染保存在被 Git 忽略的 Study/_System/Library/，不应上传。


资料升级、历史引用与经授权的 30 天归档清理见 [MATERIAL_VERSIONS.md](MATERIAL_VERSIONS.md)。默认搜索只读当前版；`context/sync` 可触发已启用的归档维护，实际删除时会更新资料删除标记，学习证据保持不变。
