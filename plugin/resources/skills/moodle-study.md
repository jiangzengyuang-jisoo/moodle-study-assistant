---
name: moodle-study
description: "使用本插件在 DeepSeek Harness 聊天中完成 Moodle 学习：首次设置、读取本地课程资料、按章节教学、核对本章文件、保存教学位置，以及在需要时更新学校作业/公告信息。用于学生询问课程学习、复习、课件内容、首次连接学校账号或设置资料位置时；不用于普通网页检索或其他学校事务。"
---

# Moodle 学习助手（DSH 插件）使用说明

本插件提供两类工具。MCP 工具在界面中显示为 `mcp__moodle_study__<名称>`（下文用短名）；Host 工具 `moodle_study_chat`、`moodle_study_status` 始终可用。

- **Moodle 只读工具**：`moodle_list_courses`、`moodle_get_course`、`moodle_list_assignments`、`moodle_get_assignment`、`moodle_list_quizzes`、`moodle_get_quiz_attempts`、`moodle_list_forums`、`moodle_get_forum_discussions`、`study_all_discussions`、`study_discussion_posts`、`moodle_get_calendar_events`、`moodle_get_grades`、`moodle_get_notifications`、`moodle_list_resources`、`moodle_get_site_info`、`study_check_chapter`。
- **本地学习工具**：`study_local_context`、`study_chapter_prep`、`study_material_read`、`study_material_render`、`study_teaching_event`、`study_local_status`、`study_refresh`、`study_job_status`。
- **设置工具**：`study_setup_status`、`study_setup_location`、`study_setup_runtime`、`study_connect_school`、`study_setup_courses`、`study_setup_select_courses`、`study_setup_sync`、`study_setup_finish`。

所有工具都在 Harness 内直接调用，不需要让学生运行命令、修改配置文件或理解技术术语。

## 首次使用：逐步引导

学生第一次说“设置 Moodle 学习助手”或学习请求时工具缺失，按下面顺序进行；每完成一步再给下一步，不要一次堆给用户：

1. 调用 `study_setup_status`，读取 `settings`、`next_steps`、`job`。
2. **选择资料位置**：告诉学生默认位置（`dataDirDefault`，通常是“文档\MoodleStudy”），询问是否使用默认或指定其他文件夹；得到明确答复后调用 `study_setup_location`（自定义路径传 `data_dir`）。如果学生以前用过开发版并希望继续用原资料，把原项目文件夹作为 `data_dir`，工具会沿用已有记录、不会覆盖。学校地址可用 `moodle_url` 指定（HTTPS），不确定时保持默认。
3. **安装运行依赖**：调用 `study_setup_runtime`，然后每隔一段时间用 `study_job_status`/`study_setup_status` 查看进度；成功前不要声称安装完成。失败时读日志尾部的真实错误，提示学生检查网络后重试。
4. **连接学校账号**：调用 `study_connect_school`（默认用系统 Edge）。告诉学生“请在本机弹出的学校网页完成登录（可能需要验证码/双重验证）”。登录由学生本人完成；不要索要密码、验证码或 Token。用 `study_job_status` 轮询，成功后 `study_setup_status` 的 `school.connected` 应为 true。
5. **选择课程与资料范围**：调用 `study_setup_courses` 列出课程，把课程名称和 shortname 展示给学生，让学生本人选择；然后询问资料范围：“完整资料同步”（会下载并索引所选课程资料）或“只同步课程信息”（不下载）。学生明确确认后调用 `study_setup_select_courses`（`course_ids`、`mode`）。不得替学生选择或默认授权下载。
6. **首次同步**：先调用 `moodle_study_chat` 取得当前聊天 `chat_id`，再调用 `study_setup_sync`（传入 `chat_id`）；用 `study_job_status` 轮询。完整同步可能较慢；下载成功不等于已核读。
7. **完成设置**：调用 `study_setup_finish`。返回 `ready: true` 后工具会自动重连；提示学生“工具正在重新连接，几秒后可以开始学习”。若返回未完成原因，按 `next_steps` 继续，不要谎称设置成功。

学生已有本插件设置（本机已有资料目录）时，直接进入学习流程；若工具提示未初始化，才走上面的设置流程。需要更换资料位置或学校时，重新调用设置工具并在完成后重新连接。

## 正常学习：章节讲解

1. 先判断当前请求：章节讲解、复习已学内容、考前复习、查询考试范围、制定学习计划或直接答疑是不同任务，按请求选择；保存过的考试信息不把普通讲解变成备考。
2. 学生指定章节且本地已有资料时：
   - 用 `study_local_context` 读取本地接续（可选 `course_id`）；
   - 用 `study_chapter_prep` 一次批量准备本章材料/目录/正文/图表候选页，按返回的 `omitted` 提示用 `study_material_read` 补读，需要核对公式图表时用 `study_material_render` 看原页；
   - 用 `study_check_chapter` 严格核对本章文件。参数：`course_id`（本地课程 ID）、`section` 或 `module_ids`、必填 `chat_id`（先调用 `moodle_study_chat` 取得）；同一聊天同一章且本地资料未变时会复用回执，不要额外传 `force`；只有学生明确要求重新核对才传 `force: true`。
3. 材料够用后立即输出实质讲解，不要先做维护、保存或补跑计划页面。默认一次讲透一个适量知识点（具体例子 → 概念/符号/条件 → 示范 → 一个相关问题），等学生回答后再推进；学生明确要求整章讲义、完整答案或不要互动时按其要求一次交付。
4. 讲解确实输出后，用 `study_teaching_event` 保存本轮教学位置（`chat_id` + 只含本轮变化的 `event`）；不要手写整份学习状态。
5. 学生需要作业要求、截止日期、公告、考试安排，或本地缺该章资料时，才用 `study_refresh`（先取 `chat_id`）采集最新学校信息；普通讲解不查询或汇报无关的公告、在线练习、旧章未答题。

## 请求范围与边界

- 学生指定章节时只处理该章；不要自动扩大为全部课程刷新、在线练习核对、作业提交检查或旧章节追问。
- 只读学校接口：不提交作业、不代替作答学校测验、不发帖、不修改完成状态、不发邮件。
- 普通讲解回答末尾不要堆技术日志、核对回执路径、错误码或无关历史事项；只有当资料缺失或差异实际影响讲解时，用一两句话说明影响和需要学生做的下一步。
- 数学公式用可渲染 LaTeX（`$...$` / `$$...$$`）。
- 不要读取或展示 `.private/`、浏览器 Cookie、Token；不要让学生粘贴密码或验证码。

## 故障恢复

- Moodle 工具缺失或调用失败：先调用 `moodle_study_status` 与 `study_setup_status` 查看具体原因和 `next_steps`，按提示修复（重新安装依赖、重新连接学校、重新选择课程）。工具重连或重启 Harness 后再继续。
- `STUDY_MCP_RESTART_REQUIRED`：说明学校连接或授权已变化，需要重连；提示学生重新加载 Moodle 工具或重启 Harness，不要用旧连接继续核对。
- 网络或学校接口错误：保留真实失败事实，不把缓存当最新结果；不要自动反复重试或切换其他路径。只有影响当前讲解或需要学生处理时才简短说明。
