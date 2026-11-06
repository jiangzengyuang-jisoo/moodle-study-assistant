# 资料更新与旧版归档

正常课程目录、`context` 的资料列表、`search` 和 `outline` 使用当前版本。旧版移入 `课程/_Archive/`，保留原 material ID、哈希、原页码和历史审阅证据；错题、作答、进度不会迁成对新版的掌握记录。资料索引中的实际路径和 Markdown 链接目标随原件迁移；历史原答、来源记录、原答绑定、用户纠正及其快照保持原样，不按全文替换路径，也不重签原答校验。历史记录中的旧路径只是当时的记录，读取原件应通过原 material ID 查询当前归档位置。`.ignore` 让普通文件搜索跳过归档，显式查看历史时才进入。

## 启用与更新

经本人同意后运行：

```powershell
.venv/Scripts/python.exe tools/study/material_versions.py configure --days 30
```

这会启用版本管理和使用时的清理检查；没有后台任务，也不增加下载授权。教学前的 [章节核对](CHAPTER_CHECK.md) 只下载本次选定的已授权课件，完整同步仍只处理已有课程级授权范围。按“课程 + Moodle 模块 + 远程子目录 + 文件名”匹配来源，先写 `_Inbox`，验证内容哈希和文档结构，再切换当前版本。下载相同字节时复用现有版本。新模块、改名或同名的不同资料需要核读来源和内容；不能仅凭题名、日期或文件大小自动判定旧版。

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

旧记录只有路径而没有 material ID 时，先结合课程、原文件哈希和原页内容核对资料索引；同名路径可能已经属于新版，不能直接当成历史原件。匹配不唯一就标待核对，不猜测版本，也不通过改写历史记录或重新计算指纹消除校验错误。

旧卷的新鲜度、新卷登记和新一轮复习选题都会检查来源是否已经归档或被替代；旧 ID 和哈希仍在不代表可以继续默认使用。已安排未答的旧源题在下次准备时标为待核验并释放预算，原题与来源保留。受影响的练习需要按新版原页重新核验，再登记新题或新卷；不能自动替换来源 ID 来冒充已经核验。

状态与链接修改均有备份；移动、链接更新和状态替换有事务记录。发生中断后用 `material_versions.py recover` 恢复，再执行 `library.cmd sync`。恢复遇到更新的状态、批注或文件冲突时停止，保留现场，不覆盖用户的新改动。

## 清理规则

- 从**移入归档的时间**计算，至少保留 30 天。
- `library.cmd context`、`library.cmd sync` 或已授权资料安装时检查；同一天复用检查结果，无消息不运行。
- 仅清理受管理的 `_Archive` 原文件；必须存在可验证的当前替代版本，且旧文件哈希未变、学习状态和笔记没有引用。含糊的引用按保留处理。
- 保留版本 ID、来源、哈希及删除时间，以便解释历史记录；不会清除错题、作答、进度或用户修改的旧版。
- 手动核查可用 `material_versions.py status`；立即按相同保护规则检查可用 `material_versions.py cleanup`。保留期不因手动检查而缩短。

资料缓存仍仅是可重建的派生数据。启用清理后，`context/sync` 的归档维护可能更新资料的删除标记；不会修改掌握程度。迁移或实际清理改变 state 后，按工作流重建计划视图。
