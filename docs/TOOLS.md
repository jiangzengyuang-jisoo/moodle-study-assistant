# 工具入口与验证

以下命令在项目根目录运行。普通同学优先使用 README 中的双击入口；本页供 AI 和维护者参考。

## Moodle

```powershell
node tools/moodle/run.mjs init
node tools/moodle/run.mjs check
node tools/moodle/run.mjs courses
node tools/moodle/run.mjs refresh
node tools/moodle/run.mjs mcp
```

`init` 只创建缺失的配置/空白状态，不覆盖已有资料。`courses` 交互选择跟踪页面，保留未选页面的既有历史。`refresh` 采集到 `Study/_System/refresh-latest.json`，完整成功另存 `refresh-last-complete.json`；不直接更新个人掌握、作业完成或 AI 核读状态。退出码2表示部分采集失败。

MCP 暴露课程、作业、测验、论坛只读查询；全论坛分页用 `study_all_discussions`，回复用 `study_discussion_posts`。上游的 recent discussions 只有最近20条，不能替代完整采集。本版没有下载工具，也不注册学校写操作。

## 智能复习

```powershell
.venv/Scripts/python.exe tools/study/review.py init
.venv/Scripts/python.exe tools/study/review.py due --minutes 15
.venv/Scripts/python.exe tools/study/review.py render
```

状态入口默认 `Study/_System/state.json`；测试或迁移时可在子命令前加 `--state 路径`。初始化只保存 FSRS 设置，不虚构复习历史。

先由 AI 根据本人问题建立 `mistakes` 和关联的 `review_queue` 项目。队列至少有 id、course_id、mistake_id、status、due_date、independent_prompts；题目提示不提前包含答案。工时为助手估算。

真实独立作答后，将 JSON 经 stdin 传给 `review.py record`，不要把题目或凭据拼入执行表达式：

```json
{
  "review_id": "已有的队列ID",
  "attempt_id": "本次真实尝试的唯一ID",
  "rating": "good",
  "prompt": "实际独立检查题",
  "answer": "本人真实答案",
  "feedback": "针对这次答案的核对结果",
  "evidence": "本次对话中的实际作答依据",
  "independent": true
}
```

这只是格式示例，不能作为本人作答录入。可选 `reviewed_at` 必须有时区；默认当前时间。rating 为 again / hard / good / easy，依次表示未独立答出 / 答出但困难 / 独立答出 / 轻松答出。同一 attempt_id 重试不重复评级，内容冲突拒绝覆盖。不会自动修改课程掌握情况。

## 数学核验

向 `.venv/Scripts/python.exe tools/study/math_check.py` 的 stdin 传入 JSON，例如：

```json
{"operation":"limit","expression":"(x**2-1)/(x-1)","variable":"x","point":"1"}
```

支持 simplify、evaluate、differentiate、integrate、limit、equivalent。用 `**` 表示乘方；默认符号为实数，可用 `symbols: {"x": "positive"}` 指定正数条件。equivalent 需提供 other；evaluate 可提供 values。

默认双侧极限会分别检查左右侧。化简相等仅指共同定义域，不自动证明两式定义域相同。只接受白名单数学表达式，不用 eval；这不是不受信任输入的资源隔离沙箱，特别复杂的符号运算仍可能耗时。保留题设，结果不替代证明审查。

## 验证

```powershell
npm test
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"
.venv/Scripts/python.exe tools/study/test_study_tools.py
```

这些测试使用虚构课程和临时状态，不连接学校，也不读取个人 Token。可选的 Windows 浏览器模拟测试：

```powershell
.venv/Scripts/python.exe tests/browser_smoke.py
```

该测试只访问本机模拟服务，使用无界面 Edge，不验证学校真实 SSO。真实连接验证需本人登录后执行 `node tools/moodle/run.mjs check`。

发布前从源码文件清单建立提交，检查 `git status`，不得加入 `Study/`、`.private/`、本地配置、依赖环境或个人附件。不要用 `git add -f` 绕过忽略规则。Node 依赖锁在 package-lock.json；Python 固定版本见 requirements.txt。
