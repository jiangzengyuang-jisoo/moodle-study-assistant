# 资料更新与旧版归档

正常课程目录、`context` 的资料列表、`search` 和 `outline` 使用当前版本。旧版移入 `课程/_Archive/`，保留原 material ID、哈希、原页码和历史审阅证据；错题、作答、进度不会迁成对新版的掌握记录。引用原文件的状态路径和 Markdown 链接随原件迁移。`.ignore` 让普通文件搜索跳过归档，显式查看历史时才进入。

## 启用与更新

经本人同意后运行：

```powershell
.venv/Scripts/python.exe tools/study/material_versions.py configure --days 30
```

这会启用版本管理和使用时的清理检查；没有后台任务，也不增加下载授权。普通 Moodle 刷新仍只读信息。用户明确授权下载的已登记资料，以及**已保存课程级完整授权**的课程资料同步，都按“课程 + Moodle 模块 + 远程子目录 + 文件名”匹配来源，先写 `_Inbox` 暂存，验证内容哈希和文件签名，再切换当前版本。下载相同字节时复用现有版本。新模块、改名或同名的不同资料需要核读来源和内容；不能仅凭题名、日期或文件大小自动判定旧版。

课程级同步的归档补充规则（详见 [COURSE_LIBRARY.md](COURSE_LIBRARY.md)）：

- 同一课程、同一目录下同名不同内容使用 `module-<模块编号>` 子目录（必要时再附加短哈希），绝不覆盖；旧版仍进入 `_Archive`。
- 相同字节、不同来源只登记一条并记 `duplicate_of`；相同来源、相同字节报告为 `identical`，不重复登记。
- 同一来源产生新字节时建立 `supersedes` 关系，保留原 material ID、原文件名、来源页面、SHA-256 和历史引用；自动同步不替 AI 判断内容语义，下载/解析成功不等于核读或掌握。

旧材料保留原 ID 和哈希，并不等于仍有当前使用资格。旧模拟卷的新鲜度检查、新模拟卷登记和新一轮复习选题都会检查来源是否已归档或被替代：旧卷失效，新卷拒绝旧源，复习跳过旧源变式；已安排但未作答的旧源题在下次准备时转为“题源待核验”并释放预算。原题、原来源和历史作答保留；更新前真实完成的作答可按原时间补录，更新后的旧源复习题不能获得新的独立复测评级，辅助练习记录仍可保留。按新版原页重新核验后再登记新的练习或试卷，不能只替换 material ID 来声称已经核读。

已核实的本地版本关系使用：

```powershell
.venv/Scripts/python.exe tools/study/material_versions.py promote --old OLD_MATERIAL_ID --new NEW_MATERIAL_ID
```

`NEW_MATERIAL_ID` 必须已在 materials 中登记并指向真实新文件。也可将已验证的下载元数据以 JSON 传给 `install-download`，由它登记不可变的新 ID。未能确认的资料留在 `_Inbox` 等待核读，失败不会用新文件覆盖旧原件。补充讲义、个人批注稿不会按相似文件名自动归档。

## 历史引用与恢复

追溯错题时，用旧 ID 和原页码显式读取：

```powershell
tools/study/library.cmd read OLD_MATERIAL_ID --start 7 --include-archived
tools/study/library.cmd render OLD_MATERIAL_ID --unit 7 --include-archived
```

答案文件仍另外需要 `--include-solutions`。历史引用不会静默跳到新版同页；题目重排后页码可能不同。新一轮复习使用当前版本，历史作答证据保留原版本。

状态与链接修改均有备份；移动、链接更新和状态替换有事务记录。发生中断后用 `material_versions.py recover` 恢复，再执行 `library.cmd sync`。恢复遇到更新的状态、批注或文件冲突时停止，保留现场，不覆盖用户的新改动。

知识观察的 `evidence_bindings` 由保存工具维护。真实归档/移位会在同一资料版本事务内，对“旧摘要唯一命中且新记录正好等于旧记录按同一组路径替换”的已绑定原答重算摘要与父记录校验，使原答仍可解析、之后的完整状态仍能保存；未绑定、已失配或有歧义的记录保持原样，不能通过删除绑定或放宽原答校验绕过。升级前已经失配的历史记录仍需人工核对。

## 清理规则

- 从**移入归档的时间**计算，至少保留 30 天。
- `library.cmd context`、`library.cmd sync` 或已授权资料安装时检查；同一天复用检查结果，无消息不运行。
- 仅清理受管理的 `_Archive` 原文件；必须存在可验证的当前替代版本，且旧文件哈希未变、学习状态和笔记没有引用。含糊的引用按保留处理。
- 保留版本 ID、来源、哈希及删除时间，以便解释历史记录；不会清除错题、作答、进度或用户修改的旧版。
- 手动核查可用 `material_versions.py status`；立即按相同保护规则检查可用 `material_versions.py cleanup`。保留期不因手动检查而缩短。

资料缓存仍仅是可重建的派生数据。启用清理后，`context/sync` 的归档维护可能更新资料的删除标记；不会修改掌握程度。迁移或实际清理改变 state 后，按工作流重建计划视图。
