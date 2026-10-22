# 开源组件说明

本项目组合现有开源库和本地工作流，不宣称重新实现 Moodle、FSRS 或符号计算引擎。依赖安装到本机，源码仓库不携带 node_modules、Python 环境或浏览器二进制；各依赖仍遵循其自身许可证。

| 组件 | 用途 | 许可证/来源 |
| --- | --- | --- |
| moodle-mcp 0.2.0 | MoodleClient 基础及课程、作业、测验、论坛 MCP 工具 | MIT；[上游](https://github.com/1alexandrer/moodle-mcp) |
| @modelcontextprotocol/sdk | 本地 STDIO MCP 协议 | MIT；[上游](https://github.com/modelcontextprotocol/typescript-sdk) |
| Zod | 工具参数校验 | MIT；[上游](https://github.com/colinhacks/zod) |
| py-fsrs | 基于真实独立作答安排复习 | MIT；[上游](https://github.com/open-spaced-repetition/py-fsrs) |
| SymPy | 符号计算核验 | BSD；[上游](https://github.com/sympy/sympy) |
| Playwright Python | 本机浏览器官方授权辅助 | Apache-2.0；[上游](https://github.com/microsoft/playwright-python) |
| pypdf | PDF 逐页文字抽取 | [上游及许可证](https://github.com/py-pdf/pypdf) |
| pypdfium2 / PDFium | 无需另装 Poppler 的原 PDF 页渲染 | [上游及所含第三方许可证](https://github.com/pypdfium2-team/pypdfium2) |
| Pillow | 原页图像保存与验证 | [上游及许可证](https://github.com/python-pillow/Pillow) |

资料缓存也可使用用户已安装的 Poppler `pdftoppm`；Windows OCR 使用操作系统已有组件及语言包。仓库不分发 Poppler、OCR 语言包或课程资料，OCR 候选仍须对照原页核验。

`tools/moodle/patch-upstream.mjs` 针对固定的 moodle-mcp 0.2.0 做四处兼容调整：作业 cmid 映射、论坛 instance ID、不发送不支持的论坛排序参数、作者字段 userfullname。上游升级必须复核补丁和测试。

moodle-mcp 的相关版权声明：

```text
MIT License

Copyright (c) 2026 Alexandre Ribeiro

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
