# 从老师要求和课件准备模拟卷

用户可以说“看看考前还缺什么”“按老师的题型出一套模拟卷”“批改这份卷”“复盘这一周”。AI 负责核读来源、编题与批改，程序负责结构核验、来源关联、记录和页面重建。没有材料时不会仅凭文件名自动生成可靠试题。

## 核读当前考试要求

每次按 [WORKFLOW.md](WORKFLOW.md) 刷新 Moodle，然后执行：

```powershell
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-sync --snapshot 'Study/_System/refresh-latest.json'
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-intake --snapshot 'Study/_System/refresh-latest.json'
```

`exam-intake` 只找候选内容，不自动认定官方要求。实际核对课程、班别、考试轮次、发布日期和老师/助教身份。学生推测、另一班或往年的题型不覆盖当前教师说明；“written test”不能推成已公布选择题或长题。样卷不必然保证正式题量。

若要求在附件内，读取已有本地文件，或在用户明确授权后获取那份说明，见 [下载入口](TOOLS.md#明确授权的资料下载)。默认刷新不下载。核对图表、公式和幻灯片原页，保留物理页码；不能只凭附件标题确认要求。

通过标准输入传入考试档案，调用 `daily.py exam-profile --snapshot ...`。字段约定：

| 字段 | 内容 |
| --- | --- |
| id / course_id / title | 本次考试编号、已建档课程编号、名称 |
| exam_date / duration_minutes / total_marks | 有依据的日期、时长和总分；未知保留 null |
| scope_status / format_status | confirmed、partial 或 unknown，不把助手假设升级为 confirmed |
| topics | 每项有 id、label、material_ids；缺资料时保留空映射，不虚构来源 |
| sections | 每项有 id、question_type、count、total_marks；按已公布信息补 marks_per_question、option_count、allowed_topic_ids |
| sources / review_evidence | 实际官方来源与本轮核读记录 |
| excluded_topics / notes / instructions | 不考内容、老师强调和允许方法等；不把“更重要”编成指定分值 |

题型支持 `multiple_choice`、`short_answer`、`long_answer`、`fill_blank`、`true_false`。范围复杂时按学习单元细化 `topics`；范围是否齐备由 AI 实际阅读判断，不是程序从标签猜测。

来源定位有以下形式：

- `post:编号`：本轮论坛原文，`authority: teacher`、作者 `author_id` 与实际 `role_evidence`，摘录 `excerpt` 须匹配原文。
- `module:编号` 或 `section:课程数字ID:section数字ID`：本轮官方课程正文，`authority: official_course`，摘录须匹配。
- `material:本地资料ID`：已有官方课件或考试说明，附 `material_id`、物理 `pages`、实际 `excerpt`。资料须属于本科。
- `attachment:模块编号`：仅在另有授权且可用的能力实际完成在线内存核读时使用。附 `module_id`、实际页码、SHA256、`verification_method: online_in_memory_assessment_preview`、`course_file_saved: false` 和 `preview_evidence`。不要把附件文字冒充模块 HTML。若已下载，应索引为本地 material。此定位不是附件读取工具。

新公告、原文变更、来源消失或本次采集失败会将相关档案标为 `needs_review`，旧卷暂不可用。后续一次“内容没变”的刷新不会自动清除待核读标记，需实际重新核读并登记。普通核对时间变化不使卷过期。附件入口没变也不能证明附件内容没变，出新卷前仍要核读相关附件。

## 按课件出题

先读课程 orientation、教学位置、相关讨论与 `materials`，再查看考试范围内的本地原课件。保留老师术语、方法限制和公式条件。每题先编写完整题干、正确答案、评分点，再核验计算、唯一选项、定义域、逻辑与原页依据。数学可用 `math_check.py` 辅助；它不能替代证明、会计借贷逻辑或经济条件检查。

试卷作为 JSON 经标准输入传给 `daily.py mock-register`。顶层需要 id、exam_id、title、mode、questions；可加 assumptions。每题需要 id、section_id、question_type、stem、marks、topic_ids、sources、answer、rubric、verified 和 verification。选择题另有 options；answer 为其选项键。

三种模式不得混用：

| 模式 | 何时使用 | 必须说明 |
| --- | --- | --- |
| full_mock | 老师已确认范围和完整结构，本地材料齐备，覆盖全部登记单元 | 匹配已公布的题数、选项数、分值；原创练习不代表真题预测 |
| full_scope_mock | 范围、时长、总分、题型及各题型总分均明确，材料及登记单元覆盖齐备，但部分题数或逐题分值未公布 | `format_status` 保持 partial，所有未公布细节逐节登记为助手安排 |
| targeted_practice | 题型有依据，但范围资料或整卷结构不足 | 标出未覆盖内容、练习题量和分值安排，不能称完整模拟卷 |

题型本身未知时暂停按考试题型出卷；可按用户要求做普通学科练习，不能伪称符合正式考试。所有完整性都相对于已核读并登记的学习单元，不表示穷尽每个子知识点。

`full_scope_mock` 的 `assistant_structure` 只填老师未公布题数或逐题分值的分节。例如虚构说明只给“长题共12分”，本练习可明确安排为：

```json
[{"section_id":"LONG","count":2,"marks":[6,6],"reason":"老师未公布长题题数和逐题分值，本卷由助手安排。"}]
```

这不是任何学校的真实要求。题数和逐题分值须与卷内一致，不能改动老师已公布的安排。系统页面会逐项显示助手安排，而不是依赖一句笼统免责声明。

以下仅说明一个虚构题目的输入结构；必须先在隔离示例中准备对应课程、资料、考点与已核读档案，不能写进真实学习档案或作为某位同学的记录：

```json
{
  "id": "Q1",
  "section_id": "SHORT",
  "question_type": "short_answer",
  "stem": "求函数 f(x)=2x+1 在 x=3 时的值。",
  "marks": 2,
  "topic_ids": ["FUNCTION_VALUE"],
  "sources": [{"material_id":"DEMO_SLIDES","pages":[1],"detail":"原页讲解函数代入；本题数值由助手原创"}],
  "answer": "7",
  "rubric": [{"point":"正确代入并得出 7","marks":2}],
  "verified": true,
  "verification": {"method":"直接计算","detail":"2×3+1=7，已核对题设和原页方法"}
}
```

`verified` 与 `verification` 是 AI 已完成核验的记录，程序不会据此自动证明答案正确。每题来源必须覆盖所关联考点，页码不能超出材料。评分点之和须等于题分。改版使用新卷编号，保留旧卷历史；材料索引哈希或考试要求变化后重新核读，必要时另出新卷。

`Study/_System/Mock Exams.md` 是入口，卷面在 `Study/<course_id>/Practice/`，由状态重建。待答页不含答案；答案、解析和评分点留在私人状态供批改。生成卷不会假造学习用时，也不会默认把整场考试塞进今日预算。

## 批改、答案曝光与后续复测

将真实作答整理后交给 `daily.py mock-attempt`，可以只提交已答题：

```json
{
  "attempt_id":"本次真实尝试唯一编号",
  "mock_id":"已有试卷编号",
  "actual_minutes":null,
  "answers":[{
    "question_id":"题号",
    "answer":"本人实际原答",
    "independent":true,
    "answer_seen_before_attempt":false,
    "evidence":"真实对话或作答文件页定位",
    "feedback":"依据原答和评分点批改后的说明"
  }]
}
```

选择题答案须明确为选项键，程序按标准答案评分。其他题由 AI 对照原答先批改，补入 `awarded_marks` 与按 rubric 顺序排列的 `rubric_scores`；两者须一致且不能超过题分。老师允许只给正确答案得满分时，不自行强求步骤。缺答题不录成已答零分，部分卷只报告已答分母；分数不是实际考试成绩预测。

整卷答案通过 `daily.py mock-answers` 返回，标准输入为 `mock_id` 和实际展示依据 `evidence`。只展示个别题的提示/解答时，用 `mock-answer-shown`，另给 `question_ids`。两个入口先记录曝光再供后续使用；批改反馈同样记曝光。重答同题不能变成未见答案的独立证据。

真实独立错误去重进入 `mistakes` 和 `review_queue`，同步本科错题本管理区，保留既有笔记。它们起初没有复测题，需要 AI 根据卡点准备并核验新的 `practice_variants`，再由统一每日预算安排。真正独立完成新变式时才更新 FSRS；生成卷、看解答、辅助作答均不评级。

## 覆盖检查与每周复盘

`Exam Coverage.md` 分开显示资料是否齐备，以及已登记考点的未独立测试、单次/多次独立做对、仍不稳定和到期复测。只使用明确对应考点的证据；没有映射不代表以前从未学过，一道题答对也不代表整章掌握。

`Weekly Reflection.md` 汇总最近7个学习时区日期的真实记录：独立/辅助作答、跨日复测、重复失误、本人反馈和少量下一步重点。时间未知保持未知；累计时间与逐段时间、模拟用时与反馈用时不重复相加。没有足够记录就说明不足，不拟造趋势。

视图随对话中的运行更新，没有后台任务。每次状态合并后运行 `daily.py run`、`daily.py check`。空白安装不附任何作者的考试档案、老师原文、课件、模拟题或个人作答。
