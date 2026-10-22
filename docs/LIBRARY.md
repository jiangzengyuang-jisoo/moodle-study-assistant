# 课程资料缓存与快速启动

用户继续自然说“接着复习这门课”即可。助手负责以下流程，不需要用户运行命令。示例中的 COURSE101 和资料 ID 必须替换为本人档案中实际存在的编号。

`state.json` 仍是唯一学习状态。这里的缓存只保存资料定位、抽取文字、OCR候选和原页图片，不写进度、错题、FSRS评级或学校状态。所有资料仍以原件为准。

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
4. `render` 返回缓存的原PDF页图；助手用图像工具实际查看公式、表格、图示、手写和局部“不考”注记。图片文件直接返回原件。PPTX/DOCX返回原文件入口与slide/section编号；本工具没有把它们伪装成已经视觉验证的PDF。

`detail mistakes 记录ID` 或 `detail path /courses/0/teaching_cursor` 精确补读完整状态记录；索引路径按本次返回的state revision使用，变更后重取。`context --detail` 会包含本科完整记录和答案，仅用于需要完整证据的内部核查或已授权批改。默认摘要隐藏结构化答案/评分字段，但自由文本可能提到结论，不能直接当成面向学生的无答案题面。

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

Moodle在每个新对话开始时完整检查一次，用户明确要求刷新或查询最新学校信息时再次检查；同一对话后续消息复用已核验结果，切换课程、跨日、上下文压缩和重新打开旧对话不另触发，不设固定间隔。网络只读采集使用全局最多4个并发请求，各论坛分页顺序、正文/回复数量和权限检查保留。需要刷新时可与本地context/定位并行，依赖此次学校检查的结论须等刷新与变更核读完成。刷新成功不自动表示内容已审阅，复用旧结果不写成当前刚完成检查。

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
