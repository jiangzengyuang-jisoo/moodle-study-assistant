# 当前冻结插件候选

本页于 2026-11-16 核对。当前本地候选为 **dsh-moodle-study@0.6.0-trial.8**，未公开发布。

- 安装包：[dsh-moodle-study-0.6.0-trial.8.tgz](../../release/npm/dsh-moodle-study-0.6.0-trial.8.tgz)。
- 冻结时间：2026-11-14 02:41（香港时间；包内 UTC 时间 `2026-11-13T18:41:37.727Z`）。构建来源提交未记录，保留 `null`。
- SHA-256：`a6859d078a5d6ff5fcf5e0007ff95fafb51b923c1e0f87cfb95c7bc83195a980`。动态元数据见 [release-manifest.json](../../release/release-manifest.json)。
- 范围：当时的学习引擎及中英术语、AI 定向指令提醒规则。原始验证见 [2026-11-14 更新记录](../UPDATE_TERMS_ALERTS_2026-11-14.md)。
- **不包含 2026-11-16 的自动补齐和本次章节边界修复。** 本次仅对齐索引、说明和生成材料，未重打包、安装或重做真实学校/客户端验收；历史测试仍适用于其原版本。
- 学习数据、凭据、旧候选和当前冻结包原样保留。若要让插件包含今天的源码，必须另建新版本并完成相应打包、验证，不能覆盖这个包。

## 一致性检查与发布边界

维护者使用 `npm run sync:release -- --check` 检查当前文档、市场索引、实际包及生成清单；该命令不验证运行效果。需要重新整理说明时运行 `npm run sync:release`，不重建安装包。

本地包未安装到当前 Harness，也未发布到 npm 或目录站点。发布需要明确授权和针对本候选的验收；当前命令见 [发布清单](PUBLISH_PLAN.md#当前执行入口trial8)。旧说明中的版本命令只保留作历史。

## English scope note

The current local candidate is the unchanged **dsh-moodle-study@0.6.0-trial.8**, frozen on 14 November 2026. It does not contain the 16 November source changes. This repair aligns the index, documentation and generated metadata only; it does not rebuild, install or publish the package, nor transfer historical or current-source test results to this artifact. A later package version and separate verification are required to distribute today's engine fixes.
