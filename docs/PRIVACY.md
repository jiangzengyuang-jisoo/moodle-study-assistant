# 隐私与使用边界

代码可以公开，个人数据不随代码发布。安装后生成的 `Study/`、`.private/`、`config.local.json` 和 `.codex/` 已列入 Git 忽略规则。忽略规则只保护常规 Git 添加，不保护手动压缩、网盘同步、强制添加或屏幕分享。

## 认证

浏览器辅助仅打开新的临时浏览器会话，让本人在学校网页完成登录，接收官方移动授权回调。程序不读取或导出浏览器 Cookie、不读取网页密码、不使用作者登录状态。回调用本次 passport 与站点标识核对，只保留普通 API Token，丢弃移动协议中的 private token。

Token 保存前向所配置学校的 HTTPS API 验证，随后用 Windows DPAPI 的 CurrentUser 保护。只在使用时于本机进程内解密，不写入命令参数或学习状态。换电脑/Windows 账户后请重新授权。DPAPI 不能抵御同一 Windows 账户下的恶意程序；API Token 权限由学校配置，工具的只读限制不会改变 Token 本身的权限。

## 请求与数据

Moodle 查询只允许列出的读取接口，API Token 通过 HTTPS POST 请求体发送，不跟随 API 重定向。下载、提交、发帖、标记已读和修改完成状态均不开放。响应中的当前 Token 和常见 token 参数会隐藏；这不是通用个人信息匿名化器。

论坛原文、老师/同学姓名、成绩或作业反馈可能包含在个人采集结果里。它们保存在 `Study/`，不是公开仓库的一部分。不把课程受限内容上传公开 Issue 或仓库。

AI 客户端为了回答问题，可能把读入的学习内容发送给它的模型服务商。使用前自行选择允许处理这些内容的客户端/账号；本项目没有独立云端服务器、遥测、邮件发送或后台轮询。

## 移除访问

不再使用时可在学校允许的 Token 管理页面撤销对应授权，具体以学校说明为准。删除本地 `.private/` 可以移除本机缓存，但不等于已向学校撤销授权。不要在聊天里发送 Token；怀疑泄露时重新授权并撤销旧 Token。

官方认证协议参考：[Moodle 移动授权调试说明](https://moodledev.io/general/app/development/network-debug)、[Moodle 安全密钥说明](https://docs.moodle.org/400/en/How_to_get_a_security_key)。
