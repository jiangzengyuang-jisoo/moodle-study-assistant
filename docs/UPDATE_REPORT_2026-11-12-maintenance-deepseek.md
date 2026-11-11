# DeepSeek Harness 维护核对报告 / Maintenance audit

日期 / Date: 2026-11-12（香港时间 / HKT）

范围 / Scope: `deepseek` 源码；起点 / baseline `b0de6a1`；开始时工作树干净。仅目标仓库修改，不切分支、不 cherry-pick、不推送。

参考核对：个人版 `81b24dc`、`0aaaaf4`、`b113506` 与公开版 `74e589f`、`e689499`、`dffddd8` 的提交范围/相关补丁只读查看，按本端实际行为取舍；未把参考版验收算成本版通过。

## 中文结论

**确有问题，已实施修复**：旧档案可进入旧复习写入分支；重复原子文件写入；内容未变仍替换页面；新鲜度检查重复生成计划；现行 Moodle CLI 的未知命令/错误参数可能先触碰凭据与连接。教学进度与保存已有等价实现，个人专用脚本不存在，不为同步而新增。

### 九项逐项结果

| 项目 | 判定与实际处理 |
| --- | --- |
| 1 单课程重复采集 | 不适用。没有个人版两个 `collect-econ*` 或同类单课程独立入口；现有完整采集器不是单课程入口，不将它改成单课程接口或新增平行入口。保留作业、测验目录、论坛、警告及无截止日期测验信息。未改变全量采集/核读基线。 |
| 2 参数错误 | 修复本端实际发现的问题：[run.mjs](../tools/moodle/run.mjs) 在任何档案、凭据、网络操作前做现行命令语法预检。区分未知命令、多余/重复参数、缺值、错误枚举和数字格式；固定安全示例，不回显未知输入，不输出堆栈/令牌。保留课程存在性、配置、账户与授权的原校验层，不把学校拒绝或网络错误改写为参数错误。选课 `--select` 是 Moodle 页面数字 ID；本端章节核对本来支持本地 ID、数字页面 ID 或唯一课程名，不错误禁用合法数字 ID；本地 ID 提示原大小写。没有添加个人单课程采集命令。 |
| 3 旧下载退役 | 不适用。没有已替代的个人旧下载脚本；[download.mjs](../tools/moodle/download.mjs) 的明确指定模块下载和课程持续同步用途不同，全部保留。没有转换历史清单为授权，没有执行下载。 |
| 4 旧复习分支 | [review.py](../tools/study/review.py) 删除无人调用的旧选题/页面生成实现，保留 FSRS 核心、保存与公共写入兼容别名。`due` 一律使用当前选题器的深拷贝只读预览；缺少 enabled 标志也执行来源/曝光校验。`render` 经统一 daily 页面入口并保留 `status: rendered`，`init` 保留 configured 回执。`record` 在读取状态和 stdin 之前非零拒绝并提示 `daily.py attempt`。当前预览的状态、日包、预算等返回字段保持不变；旧不安全的 `questions` 列表不再输出。 |
| 5 安全写入 | [state_io.py](../tools/study/state_io.py) 统一 bytes/text/JSON 原子写入，复用于状态、资料缓存、资料版本和页面。保留共享锁、二次冲突检查、原备份/日志/提交顺序及恢复机制。同目录临时文件、flush/fsync、替换失败保原件、只清理本次临时文件、JSON 校验均有测试。二进制恢复不改变 BOM/换行；文本页面兼容 UTF-8 BOM 和 CRLF。 |
| 6 页面与计划 | [daily.py](../tools/study/daily.py)、[planning.py](../tools/study/planning.py) 对未变化页面不替换；check 复用同一轮 expected 计划，仅生成一次。保留状态保存与页面生成的分阶段恢复，故障后用同一事件 ID 重试不重复作答/FSRS。只主张减少多余操作，未测量整体性能提升。 |
| 7 论坛媒体预览 | 不适用。没有两份个人预览脚本，不复制写死学校地址的实现，不改现行通用资料下载/预览机制。本轮没有新增媒体预览测试或声称真实图片/PDF 在线验收。 |
| 8 一次性旧脚本 | 不适用。目标未发现所列两份旧脚本或需要本轮退役的对应入口；没有归档、删除或迁移，因此无新增归档 SHA-256。没有清理旧 tmp、备份或验收工程，没有删除资料。 |
| 9 教学进度保存 | 已具备，保留。[teaching_progress.py](../tools/study/teaching_progress.py) 与 [context.py](../tools/study/learning_library/context.py) 统一选择实际发生位置；作业要求与掌握证据分开；同时间冲突/缺时间待核对，不按最高章节或保存时间推断。共享锁内追加事件、来源/帮助条件校验、revision 拒绝、幂等与保存回读已有实现；已答/跳过/答案曝光不留作当前待答。[MEMORY.md](MEMORY.md)、[DSH.md](DSH.md) 和根规则保留按 Harness 能力核对相关原聊天、无法核实时只报告记录范围的要求。回执不证明所有未调用保存入口的聊天已记录。 |

### 验证证据

全部使用本项目代码及合成临时档案，未用参考仓库测试结果替代。

- 修改前项目虚拟环境基线：复习、daily、planning、事务恢复 **61/61**。
- 修改后重点回归（第一轮）**71/71**；最终新增预览等价/跨时区覆盖后 daily **23/23**。
- 完整学习工具测试：**686/686**；包含新增 **11** 项（共享写入 6、daily 3、旧入口 2）。
- Python 安装/认证/DeepSeek 接入测试：**133/133**。
- Node 完整回归：**299/299**，无跳过；新增 **39** 项 CLI 参数测试，覆盖非法结构零副作用、现行合法语法、ID 提示、真实子进程错误码与安全提示。既有账户/未授权课程/快照/缺配置/HTTP 403、503、认证、超时等回归仍通过。
- 最终完整套件合计 **1,118** 项；基线、重点测试和重复运行不重复计数。
- 同一合成档案、固定时间与基线 `b0de6a1` 做前后对照：**状态字节相同、9 份页面字节相同**；旧版第二次运行重写页面，新版保持修改时间；两版 check 都 fresh。
- 香港、UTC、伦敦、纽约：保持各档案自己的日期语义；旧档案预览与当前选题器逐字段一致（含有效题源/缺题源），并验证不改状态、不曝光评分答案。不是要求不同时区显示相同日历日期。
- 故障注入：fsync、原子替换失败不损坏原文件，不清理他人临时文件；状态保存成功后页面替换失败，重试恢复页面且作答、FSRS 历史各只有一条。
- 实际进程启动 **5 条**：临时档案 daily run/check、library.cmd context --read-only、study.cmd --help、review due。另执行 DSH status：配置正确、识别当前 Harness 主聊天；其 `runtime_loaded` 仍为 unverified，不据此声称完整重启验收。
- `git diff --check`、Node 语法检查通过。媒体脚本未修改，无本轮媒体专项验收；没有归档项可核验。

### 环境失败与复测

最初系统 Python 缺 fsrs/sympy，不能算代码失败或测试通过；改用项目 `.venv` 后基线通过。最初 Python 安装测试有 1 个 setup error、Node 全套有 8 个失败，均为 Windows 系统 TEMP 的写权限问题。按项目验收文档将 TEMP/TMP 设为项目内专用临时目录，仅改变测试进程环境，完整重测分别 133/133、299/299。未修改 ACL、全局权限或凭据。两个外部审计子代理未能启动其沙箱；由本端工具与队友完成代码审计，不采用失败子代理作为证据。

复现（PowerShell，项目根目录；临时目录不存在时创建）：

```powershell
$env:TEMP = Join-Path $PWD 'tmp/maintenance-test-temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:STUDY_PYTHON = Join-Path $PWD '.venv/Scripts/python.exe'
$env:PYTHONIOENCODING = 'utf-8'
& $env:STUDY_PYTHON -B -m unittest discover -s tools/study -p 'test_*.py'
& $env:STUDY_PYTHON -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
```

### 升级、边界与风险

本轮是**源码修复**，不重建/安装/发布 trial 插件包；旧 `0.6.0-trial.5` 产物不会自动包含这些修改。源码部署更新本次文件即可，原安装、登录、账户授权、DSH 聊天身份与数据目录解析保留。旧直接写入命令会有意失败，应改用当前 daily attempt，不迁移或重算历史。缓存 JSON 序列化改用共享严格写入，格式可能规范化为 LF 与末尾换行，语义不变。

未使用真实学校连接，未全科刷新、批量下载或修改学校状态；未读/复制真实学习记录、凭据、采集快照。真实 SSO/MFA、其他学校、其他电脑、完整客户端重启/安装包验收未在本轮验证。没有测试证据支持保证所有历史聊天都已保存。未发现本轮改动的剩余测试失败；上述部署与验证边界仍存在。

本轮只本地 Git 提交，不推送；提交编号以交付消息和 Git 历史为准（报告不自引用待生成提交）。

## English summary

**Actual fixes, not a blanket port.** This `deepseek` source checkout started clean at `b0de6a1`. Reference repositories remained read-only; no branch replacement or cherry-pick was used.

1. **Duplicate single-course collectors: not applicable.** The personal scripts do not exist here. The existing full collector remains a full collector; no parallel course-only entry, broader collection, download or baseline change was introduced.
2. **CLI validation: fixed a real local defect.** Existing commands now reject unknown commands, extra/duplicate options, missing values, invalid enums and numeric syntax before reading a profile/credential or connecting. Errors include fixed safe examples and do not echo arbitrary arguments. Existing account, course and authorization checks remain in their original handlers. Course selection takes numeric Moodle page IDs; chapter checks still accept a local ID (original case), numeric page ID or unique course name, as supported by this edition.
3. **Legacy downloads: not applicable.** No replaced personal downloader exists. Explicit module downloads and ongoing course synchronization serve different needs and remain intact. Old manifests were not converted to grants.
4. **Legacy review: fixed.** All profiles use the current selector for read-only `due`, including profiles without the loop-enabled flag. Source/exposure checks remain active. `render` delegates to daily; current preview fields/statuses and init/render receipts remain. The unsafe old questions list is no longer emitted. The old `record` CLI refuses before reading state/stdin and directs callers to `daily.py attempt`. Imported FSRS and save functions remain.
5. **Safe writing: unified.** Shared byte/text/JSON writers keep same-directory temporary files, validation, flush/fsync and atomic replacement. Locks, conflict checks, backup/journal order and recovery are unchanged. Failures preserve originals and remove only owned temporary files; binary restoration and legacy text encodings/newlines remain supported.
6. **Redundant work: reduced.** Unchanged pages retain modification times; freshness checks reuse the same round's expected plan. Saved-state/view-failure retries do not duplicate answers or FSRS history. No overall speedup claim is made.
7. **Duplicate forum preview scripts: not applicable.** No personal preview tools were copied or added; there is no new live media-preview acceptance claim.
8. **One-off scripts: not applicable.** No corresponding obsolete scripts were found or archived, so no archive hash is due. Existing temporary projects, backups and materials were not cleaned up.
9. **Teaching progress/save: already equivalent, retained.** Shared occurrence-time selection, separate assignments/mastery, conflicts, locked event append, provenance/help checks, stale revision rejection, idempotency, readback and pending-question filtering already exist. Harness chat-review guidance remains; receipts do not prove unsaved chats were recorded.

**Verification on this target:** baseline 61/61; focused initial regression 71/71; final daily 23/23; full study Python **686**, onboarding/auth/DSH Python **133**, Node **299** — **1,118 passing tests**, with no duplicate counting. There are 11 new study tests and 39 new CLI tests. A fixed-time baseline/current comparison produced identical state bytes and all 9 page contents while only the repaired version retained second-run page mtimes. Hong Kong, UTC, London and New York profiles preserve their own timezone semantics. Fault injection covers fsync/replacement failure and a saved-attempt/page-failure retry with exactly one answer and FSRS entry. Five real local command processes ran on a synthetic profile; DSH status confirmed configuration/main-chat identity but explicitly left runtime loading unverified.

**Environment failures were not hidden:** system Python lacked project dependencies; using the project venv fixed that. System TEMP permissions initially caused one Python suite setup error and eight Node failures. Setting process-local TEMP/TMP to the documented project test directory yielded complete 133/133 and 299/299 reruns, without changing Windows permissions. Failed external audit agents supplied no evidence; local tools and the teammate performed the audit.

**Limits and upgrade:** source-only fix; no trial package rebuild/install/publication. Existing trial.5 artifacts do not acquire these changes automatically. Keep the same Harness installation/authentication/chat/data-root workflows; use daily attempt instead of retired direct recording. Cache JSON may normalize formatting, not meaning. No real school connection, full refresh, download or school write was performed. Real SSO/MFA, other machines/schools and complete client restart/package acceptance remain unverified in this round. Only a local Git commit is authorized; nothing has been pushed.
