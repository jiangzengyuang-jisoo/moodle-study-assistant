<p align="center"><strong>DeepSeek Harness edition</strong> · Windows 10/11 · Open source</p>

![Illustrated workflow: course materials and requirements → explanations and independent practice → saved progress → planning and review.](docs/assets/flower-banner.png)

<h1 align="center">Moodle Study Assistant</h1>
<p align="center">Turn your course materials into explanations, practice and a study record you can return to.</p>
<p align="center"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>
<p align="center"><a href="#install-with-ai"><strong>Install with AI</strong></a> · <a href="https://raw.githubusercontent.com/jiangzengyuang-jisoo/moodle-study-assistant/refs/heads/deepseek/docs/tutorials/Moodle-Study-Assistant-Tutorial-English.pdf">Illustrated PDF guide</a> · <a href="https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/tree/codex">Codex edition →</a></p>

## Install with AI

Copy this into **DeepSeek Harness** with local file and command access:

```text
Help me install and set up this Moodle Study Assistant (DeepSeek Harness edition):
https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/tree/deepseek

Read the repository's installation instructions and carry out setup.
Preserve existing learning records. Tell me when I need to sign in,
approve access or choose my courses, then show me how to use it.
```

[Illustrated setup guide](docs/新手使用指南.md) · [Upgrade without losing records](docs/新手使用指南.md#从旧版更新)

**Source edition:** this download is a Harness workspace, not an installable plugin. The frozen trial.8 package does not contain the latest source changes. [Plugin status](docs/market/CURRENT_CANDIDATE.md).

## Your course. Your next step.

📖 **Learn from your own materials.** Read slides and handouts, follow your instructor’s methods, and trace explanations to source pages.

✍️ **Practise where you get stuck.** Start with an example, try a new problem, and use your actual answers to guide review.

↗ **Continue in another chat.** Keep your study position, mistakes and next step in the same workspace. Hearing an explanation is kept separate from independent mastery.

## Try one of these

> **Learn** — “Use my slides to explain this topic. Show the relevant pages, then let me try a new problem.”

> **Continue** — “Read my records and pick up where I stopped. Start with the point I struggled with.”

> **Plan** — “I have 90 minutes today. Check relevant deadlines and use my progress to plan the session.”

<details>
<summary><strong>Before you connect · compatibility & privacy</strong></summary>

- Requires your own AI client/account and a Moodle site with suitable API access. HKU is the default; other schools depend on their permissions. Browser login assistance is experimental.
- Materials and records stay in your local project and are excluded from normal Git commits. Content read by the assistant may reach its model provider: local storage is not offline inference.
- Moodle tools are read-only: no assignment submission, school quiz answers, posts or email. File downloads require your course authorization.
- Downloading and indexing are not content review; an explanation is not proof of mastery. Scans, formulas and model answers still need checking.

[Privacy](docs/PRIVACY.md) · [School connection help](docs/新手使用指南.md#连接失败或已有官方-token)

</details>

## Recent updates

Scanned PDFs now keep successful sync timestamps and explicitly request original-page visual reading; rendering is not a claim of review or mastery.

2026-11-16: Teaching uses chapter timestamps to avoid unchanged downloads, pauses on sync failures, and retires replaced originals only after validating the new body. [Details](CHANGELOG.en.md). Source update only; no plugin package/install.

This source maintenance fixes chapter verification, missing-version recovery and failed receipts, and aligns learning-flow rules; plugins remain unchanged.

Current public version: **0.5.0**.

This update covers source installations; no Harness plugin is published. Preserve your learning profile and local credentials, rerun setup after updating, then fully restart Harness.

Full history: [English changelog](CHANGELOG.en.md) · [中文更新日志](CHANGELOG.md).

| Date | Change |
| --- | --- |
| [2026-11-17](docs/UPDATE_REPORT_2026-11-17-deepseek.en.md) | Publish chapter timestamp sync, authorized acquisition, failure recovery and scanned-page reading; preserve public tutorials. Source only, no new plugin package |
| [2026-11-16](CHANGELOG.en.md#2026-11-16) | Verify current teacher notices for exams and deadlines; match the class and lead with uncertainty when verification fails; consolidate rules and clarify notice rechecks, timely saves and read-only maintenance; require reviewed courseware content before teaching and limit partial practice to reviewed materials; align plugin-loaded courseware rules and first-only update notices; Automatically acquire missing lesson materials under existing course grants, without repeated download permission prompts Fix ambiguous chapter discovery and acquisition across mixed-grant pages; align release materials with the frozen trial.8 package without claiming it contains today's source fixes. |
| [2026-11-14](docs/UPDATE_TERMS_ALERTS_2026-11-14.md) | Additional candidate `trial.8` adds bilingual terms and prominent AI-targeted instruction alerts to the checkout and plugin; see the [update note](docs/UPDATE_TERMS_ALERTS_2026-11-14.md). Not installed or published. |

Offline tests do not validate real school login or every installation environment. See the [changelog](CHANGELOG.en.md) for validation details and known limitations.

---

[Material library](docs/LIBRARY.md) · [Teaching & practice](docs/TUTORING.md) · [Learning memory](docs/MEMORY.md) · [Exam preparation](docs/EXAM_PREP.md) · [Troubleshooting](docs/START.md) · [Report an issue](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/issues)

<sub><a href="LICENSE">MIT License</a> · <a href="THIRD_PARTY_NOTICES.md">Third-party notices</a> · Community project; not officially affiliated with OpenAI, DeepSeek, HKU or Moodle. Most detailed guides are in Chinese.</sub>
