# 2026-11-05 更新报告：Codex 与 DeepSeek Harness

本次更新让“开始学某一章”先核对本章课件，减少等待全课程采集的时间。同一聊天复用核验结果；课件核对与作业、公告、日历和考试信息的检查分别处理。两端保留各自实现与客户端配置。

## 发布范围

| 分支 | 本次内容 | 对应提交 |
| --- | --- | --- |
| `codex` | 发布章节严格核对、本地接续、初始化说明与维护分工，并补充本报告 | [6d76109](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/6d76109)、[00f2366](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/00f2366)、[8bc8f95](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/8bc8f95) |
| `deepseek` | 保留并复核 DeepSeek Harness 已推送的最新版；本次不新增功能提交 | [f21ed7f](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/commit/f21ed7f13cc93ba6672fa155b975050f06a7fec3) |

本报告按提交标识版本，不新建发布标签或安装包。GitHub 默认分支仍为 `codex`；两端使用独立目录。

## Codex：教学只严格核对指定课件

- `continue` 读取本地接续，`today` 重建本地计划，不再因新聊天自动完整采集 Moodle。
- 助手先从本地资料库确定课程、章节和全部所需资料，再运行 `verify-chapter`。每个新聊天重新获取选定课件并比较 SHA-256；文件名、大小或修改时间相同也不能跳过内容比较。
- 同一聊天已核对的资料复用回执，新增课件只检查新增范围；跨日、重开和上下文压缩沿用原聊天 ID。失败不随追问自动重试，明确重查可用 `--force`。
- 确认同来源更新后，沿用版本安装、归档和历史引用保护；索引只处理本次课件。来源不明、新增同章文件或多个来源冲突时，保留原件并提示核读。
- 学习计划和最新通知按任务范围查询，确需全面检查才执行 `refresh`。章节回执不会推进完整采集的审阅记录，也不触发 `exam-sync`。
- 安装后的主动使用教学和章节入口说明同步更新。Codex 默认只维护本地版及 `codex` 分支，只有用户明确要求才修改 DeepSeek 版本。

详见[章节核对](CHAPTER_CHECK.md)和[分支维护](BRANCHES.md)。

## DeepSeek Harness：保留已发布的章节检查流程

- 最新版使用 `chapter-check --mode strict`，按课程和章节定位本章文件，重新获取内容并比较哈希。工具另提供只比较元数据的 `quick` 模式；教学规则使用严格模式，不能把快速核对说成内容一致。
- 本地已有本章资料时，先读本地接续再核对本章，同一聊天复用回执。章节检查不修改学习状态、不安装资料、不建立索引，也不替代完整采集。
- 发现变化后，先通过现有课程资料同步与版本管理取得新版，再教学。
- 作业、截止、公告、学习计划、考试准备、首次安装或换课等任务仍按该分支规则使用 `continue` 完整采集并在同聊天复用。这与 Codex 的按需查询入口不同。
- 保留 Harness 聊天内初始化、选课、主动使用教学、课程级授权和客户端独立配置。

详见 [DeepSeek 工作流程](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/blob/f21ed7f13cc93ba6672fa155b975050f06a7fec3/docs/WORKFLOW.md)及[该分支更新日志](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/blob/f21ed7f13cc93ba6672fa155b975050f06a7fec3/CHANGELOG.md)。

## 验证结果

| 版本 | Node 测试 | Python 上手及集成测试 | Python 学习工具测试 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| Codex | 120 | 92 | 459 | 671，通过 |
| DeepSeek Harness `f21ed7f` | 119 | 95 | 461 | 675，通过 |

Codex 复用此前完整验证结果；本次确认后续改动仅涉及维护规则和文档，功能代码与测试未变。DeepSeek 对 GitHub 上的确切提交重新运行三套测试，全部通过；结果不沿用其他本地版本的测试数量。

测试使用虚构档案和模拟学校响应，运行于已有依赖、可写临时目录及常规长度的 Windows 源码路径。导出副本曾因路径过长及缺少副本内虚拟环境而失败；回到正常源码目录后通过，未为此修改发布代码。过长路径和受限临时目录仍可能影响 Windows 测试执行。

发布前检查提交范围与被跟踪文件，未包含 `Study/`、`.private/`、`config.local.json` 或个人凭据。未连接真实 Moodle，因此没有实测学校登录、客户端聊天身份传递或实际等待时间。严格模式仍需传输本章文件；同聊天复用也不意味着学校此后不会更新。

## 已有用户怎样升级

1. 选择自己的客户端分支，在原有独立目录中更新程序文件；保留 `Study/`、凭据、个人配置和学习记录。详见[保留记录的升级步骤](新手使用指南.md#从旧版更新)。
2. 回到对应客户端聊天，直接说“用我的课件教我第 8 章”。助手负责确定范围、严格核对和读取原页，不要求用户记住命令。
3. 要查作业、最新通知或安排学习计划时，明确说明需求；要重新检查已核对课件时，说“重新核对这一章”。

课件版本一致、助手核读原页和学生独立掌握仍是三件不同的事。
