# Moodle Study Assistant

**Study with your course materials, your instructor's requirements, and a record of what you have actually learned.**

**English** · [简体中文](README.zh-CN.md)

[Why use it?](#why-use-it) · [Study examples](#study-examples) · [Get started](#get-started) · [FAQ and privacy](#faq-and-privacy) · [Updates](#recent-updates)

A personal course workspace used through a **Codex chat**. Keep slides, handouts, exercises, and learning records on your computer. Connect Moodle to bring in assignment requirements, deadlines, announcements, and discussions, then use that context for explanations, practice, and exam preparation.

Designed for students following university courses over a semester. The documented setup is **Windows 10/11 + Codex local project mode in the ChatGPT desktop app**. You study in the chat; there is no separate web app to deploy.

## Why use it?

### A course library you can keep using

Add your course files and ask the assistant to organize them. It can search registered materials by course, read surrounding pages, and check original figures and formulas when needed. Explanations can be traced to a file and page or slide, so you can compare them with what your instructor actually taught.

The library supports PDF, PPTX, DOCX, HTML, text, and images. Updated materials can become the current version while older notes and mistakes retain their original references. Parsing and OCR still need verification against the source. [How the library works](docs/LIBRARY.md).

### Learning grounded in your own course

The assistant is instructed to use your instructor's terminology and methods, check current assignment requirements, and prepare revision around the exam scope that has actually been published. Missing material or unconfirmed requirements should remain explicit gaps.

**The goal is to stay aligned with your course. This cannot guarantee that every answer is correct or within the syllabus.** An assignment does not define the entire exam scope, and the model still needs to read and verify the sources. [Exam preparation rules](docs/EXAM_PREP.md).

### Progress that survives a new chat

Keep actual answers, mistakes, sticking points, and the next study step in your project. In another chat in the same workspace, the assistant can read those records and resume. It distinguishes hearing an explanation, completing an assignment, answering with hints, and solving a new problem independently.

Practice starts with examples when needed, then gradually removes help. Independent answers and later retests guide review; reading a solution does not count as mastery. [Tutoring approach](docs/TUTORING.md).

Ask what the assistant remembers and what it is based on. Memory lookup supports explicit bilingual aliases across previous chapters; your corrections are appended with their source record preserved. This is local alias-based retrieval, not unrestricted semantic search. [Inspect and correct memory](docs/MEMORY.md).

### Plans connected to coursework

Use Moodle requirements alongside your available time and recorded progress to decide what to study next. Ask for focused practice or a mock exam based on confirmed teacher requirements and the materials you have supplied. Generated papers are original practice, not predictions of the real exam.

### What does this add to a one-off AI question?

| What you need to keep track of | What the workspace maintains |
| --- | --- |
| Which slides, exercises, and handouts belong to this course? | A reusable material index with source locations and version records |
| What does the instructor require now? | Moodle evidence the assistant must read and verify before using |
| Where did I stop, and what did I struggle with? | Saved study position, actual answers, mistakes, and next steps |
| What should I revisit before the exam? | Review records connected to independent attempts and confirmed exam requirements |

You can provide this context manually in a chat. This project organizes it for repeated use across a course, reducing the background you need to reconstruct each time. A fresh installation starts empty: bring your own materials and connect your own school account.

## Study examples

After setup, place your files in `Study/_Inbox/` and ask the assistant to organize them. Then use requests like these:

| Goal | What to say |
| --- | --- |
| Understand this week's lecture | “Use my slides to explain this topic. Follow the instructor's method and identify the relevant pages.” |
| Continue studying | “Read my records and continue from where I stopped. Check the point I struggled with last time.” |
| Study with limited time | “I have 90 minutes today. Use my deadlines, coursework requirements, and progress to plan the session.” |
| Review a weak area | “Give me 15 minutes of practice on my recorded mistakes. Wait for my answer before showing a solution.” |
| Prepare for an exam | “Check the published scope and question types, then use my materials to prepare a practice paper. Tell me what is still unconfirmed.” |
| Check for school updates | “Refresh Moodle and check whether assignment requirements or instructor announcements have changed.” |

An example request combining materials, scope, and progress:

> Use my lecture slides and the current assignment requirements to continue this chapter. Read where I stopped, explain one topic using the instructor's method, cite the relevant pages, then give me a new problem to attempt independently. Save my actual answer and the next step afterward.

This is a sample prompt, not a transcript or a claim that a course has already been checked.

Moodle is collected when a chat first enters course learning, review, or study planning; later turns reuse the checked evidence. Ask explicitly for a refresh to get newer information. Unrelated questions skip the study startup, and connection repair runs only necessary diagnostics. There is no background monitoring or scheduled refresh.

## Get started

### 1. Open a local Codex project

Install the [ChatGPT desktop app for Windows](https://learn.chatgpt.com/docs/app/windows) and sign in. In the app, select **Codex** from the ChatGPT dropdown and choose **Work in → This computer**. See the [official environment guide](https://learn.chatgpt.com/docs/environments/modes); availability depends on your account and workspace permissions.

Download this repository with **Code → Download ZIP**, extract it to a stable location such as `D:\MoodleStudy`, and open the folder containing `README.md`, `AGENTS.md`, and `start.cmd` as the local project. Allow access to that project when prompted. Pasting the repository link into an ordinary chat does not install it on your computer.

### 2. Ask Codex to set it up

You handle necessary permission prompts, school sign-in, and course selection; the assistant can inspect the environment and run the setup steps.

<details>
<summary>Copy this setup request into the project's chat</summary>

```text
Help me install and set up this Moodle Study Assistant.

Read AGENTS.md, docs/WORKFLOW.md, docs/SESSION.md, and the setup documentation.
Check the environment and install the required dependencies from official sources.
If you cannot read this project or run local commands, tell me where to open it.
Tell me when I need to approve installation, click a window, or restart the app.
Preserve existing learning records; do not replace them with an empty profile.

Help me connect my own Moodle account and choose my courses.
I will enter my password and verification codes on the school's page myself.
Do not ask me to paste credentials or tokens into chat or read .private contents.

Use the unified study entry point to collect course information, then read and
verify assignment requirements, deadlines, and announcements.
Ask what I have completed, where I stopped, and how much time I have available.
I will provide course materials; do not download files without my request.
Do not submit assignments, answer school quizzes, post, or send email for me.

Report what is ready, what has been checked, and what I still need to do.
Carry out the available setup steps and explain them in plain English.
```

</details>

The default school is HKU Moodle, where the core API collection has been used. For another school, provide its Moodle home URL; compatibility depends on the APIs and permissions it offers.

**The browser sign-in helper is experimental.** Mock callbacks have been tested, but real school SSO/MFA and a complete online installation on a fresh computer still need validation. See the [fallback connection options](docs/新手使用指南.md#连接失败或已有官方-token) if sign-in fails.

### 3. Add your materials and start learning

Put your files in `Study/_Inbox/`, then ask the assistant to organize them and begin a topic. Ordinary Moodle refreshes do not download course files. To request a download, identify the course and specific material.

Setup is ready when the assistant has verified the local tools and school connection, confirmed your selected courses, and collected **and reviewed** the course information. A successful connection alone does not mean course content has been reviewed.

<details>
<summary>Manual setup or troubleshooting</summary>

Double-click `start.cmd` for the Chinese setup guide or `diagnose.cmd` for local diagnostics. Detailed commands and dependencies are in the [beginner guide](docs/新手使用指南.md) and [diagnostics guide](docs/START.md). The setup menu and most detailed documentation are currently in Chinese; this English README covers the primary workflow.

If setup stalls, share the error text without passwords or tokens and ask the assistant to investigate while preserving your records, original files, and references. Do not delete `Study/` or rebuild an empty profile to fix a setup problem.

</details>

## Use with DeepSeek Harness (optional)

The same project works in a **DeepSeek Harness** desktop chat, and the Codex instructions above keep working:

1. Finish the normal setup (`setup.cmd`) in this folder.
2. In a Harness chat, run `python tools/onboarding/dsh_integration.py install` (running `setup` inside Harness also merges it). It appends an MCP stdio entry and a skill directory to your Harness profile, keeps every existing entry, and writes a backup. It never touches study records or credentials.
3. Restart DeepSeek Harness and open this folder as the chat workspace.
4. No chat ID is typed per message: the stable Harness session ID is used, so continuing the same chat reuses verified information while a genuinely new chat collects once. Internal delegated tasks must receive the original chat ID explicitly and add `--subtask`.
5. Original pages: `library.cmd render` returns a PNG path that the assistant reads with `read_image`. PPTX/DOCX have no page image, so use the `office-render` skill or provide a PDF/screenshot.

See the [DeepSeek Harness guide](docs/DSH.md) (Chinese) for the configuration example, diagnostics, identity rules and known limits. The Harness adaptation was validated locally on Windows with DeepSeek V4.1; other clients and operating systems remain unvalidated.

## FAQ and privacy

**Can I use only the web or mobile app, another AI client, or another operating system?**

The supported onboarding paths are the Windows local Codex project described above and, optionally, DeepSeek Harness desktop through the one-time profile merge described above. Browser-only/mobile-only setup, other AI clients, and other operating systems have not been validated for this workflow.

**Do I need an API key or a separate subscription?**

The code is open source and does not include an AI account or subscription. Account access, usage limits, and charges are separate. This guide does not require a separately purchased API key; see the [official sign-in documentation](https://learn.chatgpt.com/docs/auth).

**Are my learning records uploaded to this GitHub repository?**

Your materials and records are saved locally. `Study/`, credentials, and machine-specific configuration are excluded from normal Git commits. Share the repository link, not your used project folder. Material read by the assistant may be sent to its model provider: **local storage does not mean offline inference**. [Privacy details](docs/PRIVACY.md).

**What does the assistant do on Moodle?**

The tools are read-only. They do not submit work, answer school quizzes, post, change school completion status, or send email. Course downloads require an explicit request. Passwords and verification codes stay on the school's sign-in page.

**What still needs checking?**

Missing permissions, incomplete materials, scanned pages, and complex formulas require attention. Collection is separate from review; software validation cannot prove that an explanation is correct or that you have mastered a topic.

A known issue remains: backdating an answer-exposure event for a mock exam can be ignored as a duplicate. Check historical exposure times before treating such an attempt as independent.

**How do I upgrade without losing my work?**

Ask the assistant to back up and preserve `Study/`, configuration, credentials, and source references before updating the code. Downloading a new copy does not update your existing folder. Follow the [upgrade guide](docs/新手使用指南.md#从旧版更新).

## Recent updates

| Date | Change |
| --- | --- |
| [2026-11-02](CHANGELOG.md#2026-11-02) | Inspect and correct memory; optional DeepSeek Harness desktop support with stable chat identity, validated profile-safe MCP/skill wiring, and Chrome login by default, temporary-folder fixes, and authorization recovery after landing on the Moodle home page |
| [2026-11-01](CHANGELOG.md#2026-11-01) | English and Chinese READMEs explain the course library and study continuity; narrower startup scope and clearer Moodle diagnostics |
| [2026-10-31](CHANGELOG.md#2026-10-31) | Stronger learning-record protection, cross-chapter continuity, and Chinese setup diagnostics |

Offline tests do not validate real school login or every installation environment. See the [changelog](CHANGELOG.md) for validation details and known limitations.

## Documentation and contributing

Detailed guides are currently primarily in Chinese:

[Study workflow](docs/使用须知.md) · [Tutoring](docs/TUTORING.md) · [Material library](docs/LIBRARY.md) · [Version management](docs/MATERIAL_VERSIONS.md) · [Exam preparation](docs/EXAM_PREP.md) · [Session recovery](docs/SESSION.md) · [DeepSeek Harness](docs/DSH.md) · [Tools and tests](docs/TOOLS.md)

For a bug report or improvement, [open an issue](https://github.com/jiangzengyuang-jisoo/moodle-study-assistant/issues). Describe the expected behavior, actual result, and reproduction steps without credentials, private course content, or learning records. Code changes should follow [AGENTS.md](AGENTS.md) and the checks in [docs/TOOLS.md](docs/TOOLS.md#验证与发布).

Licensed under the [MIT License](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for dependencies. This project is not officially affiliated with OpenAI, HKU, or Moodle.
