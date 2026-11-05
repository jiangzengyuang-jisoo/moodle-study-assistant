# 课程资料缓存与快速启动

用户继续自然说“接着复习这门课”即可。助手负责以下流程，不需要用户运行命令。示例中的 COURSE101 和资料 ID 必须替换为本人档案中实际存在的编号。

`state.json` 仍是唯一学习状态。这里的缓存只保存资料定位、抽取文字、OCR候选和原页图片，不写进度、错题、FSRS评级或学校状态。所有资料仍以原件为准。

## 跨章节学习证据定位

判断某个概念是否讲过前，可运行 `tools/study/library.cmd evidence --course 课程ID --query "知识点或别名"`。它从最新状态搜索本科的知识观察、历史笔记、教学游标及历史、待复习项、覆盖核对、反馈、错题和复习作答；不按当前章节过滤。

查询按完整短语、明确中英文别名、关键词排序，返回命中类型、展开词、原记录路径及内容指纹；不是向量语义检索。未命中时换关键词并按返回范围补读，不能说“没学过”。默认最多 8 条（`--limit` 为 1–20），输出有大小上限并说明截断。排序不代表掌握程度，特别核对 `corrects` 和 `memory_corrections`，不取最高等级代替当前判断。需要原文时用 `detail path /…` 做内部证据核验，注意完整详情可能含原答或标准答案。

询问“记得我学到哪里、依据是什么”时用 `memory --course ID`，历史问题加 `--query`；纠正通过受版本校验的追加入口保存，详见 [学习记忆](MEMORY.md)。

返回的旧观察如有 `topic_corrections`，必须核读关联更正，不能只看排序靠前的旧等级；即使更正未命中关键词或被结果上限截断，关联提示仍保留。记忆位置的路径指向已选中的接续，顶层原记录另保留入口；纠正快照仅供显式来源核读。

该命令完全只读，不写计时日志、不维护归档、不刷新学校、不修改状态；不会把旧笔记自动迁移成掌握证据。字段与教学意图约定见 [教学规则](TUTORING.md)。

## 每次复习

从工作区根目录运行：

```powershell
& 'tools/study/library.cmd' context --course COURSE101
& 'tools/study/library.cmd' search '示例知识点' --course COURSE101
& 'tools/study/library.cmd' read COURSE101-LECTURE1 --start 7
& 'tools/study/library.cmd' render COURSE101-LECTURE1 --unit 7
```

1. `context` 直接读最新状态，带版本、待答题、教学接续、作业重点和遗漏项路径。没有课程参数时只返回课程总览。不能把摘要当成完整个人历史；相关 `orientation`、`assistant_notes`、讨论与错题用 `detail` 补读。计划另读 schedules/settings 和相关任务。
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

以最新作业/考试范围、全部相关资料、章节和教学接续建立覆盖清单，再逐节读取。目录包含无文字、未索引和失效项目，不把top-k搜索结果、抽取成功或现有page_map当作全覆盖。DOCX/HTML的编号是结构段，PDF是物理页，PPTX是演示顺序slide。

讲义与slides可能互补；原页中被文字抽取遗漏的图形依然存在。抽取/OCR结果始终 `verified:false`。历史原页审阅仅在来源哈希一致时可沿用其明确范围，不等于本次全文检查，更不表示学生掌握。

## 增量维护

```powershell
& 'tools/study/library.cmd' status --check-sources
& 'tools/study/library.cmd' sync --course COURSE101
& 'tools/study/library.cmd' ocr --course COURSE101
```

首次初始化 `sync` 无课程参数，登记现有全部materials；以后只在新增、变更或缓存故障时重建本科。每次使用校验源文件完整SHA-256和缓存摘要，文件大小/修改时间相同也不能绕过；失效时拒用旧文本。`sync` 只重解析变化文件。抽取器行为改变须提高 `EXTRACTOR_VERSION` 后同步，不能无版本变更复用旧结果。

`ocr` 使用Windows本机识别，补PDF无文本页及图片文件，按源哈希、单元、语言与OCR版本复用。默认 `en-US`，需要中文时传 `--language zh-Hans-CN`；未安装语言会报错，不自动安装。OCR保留原图与错误信息，不覆盖抽取正文；数字、符号和表格列序可能有错，必须看原页。识别空字符串也不能证明页面为空。新增DOCX/PPTX的嵌图仍按原件视觉读取。

教学前先用本地 `context/search/outline` 确认本章所有需要的课件 ID，再按 [章节核对](CHAPTER_CHECK.md) 下载并比较实际 SHA-256；时间、大小或名称未变均不能跳过严格核对。同聊天已核对资料复用，新增范围单独核对；失败标“本次未核实更新”，不随追问反复联网。新聊天重新核对。学习计划和最新通知按任务范围查询，确需全面覆盖时才完整 `refresh`；局部结果不推进全量审阅基线。核对可与本地只读准备并行，依赖最新内容的讲解须等核对完成。字节一致、内容已核读与本人已掌握分别判断。

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
