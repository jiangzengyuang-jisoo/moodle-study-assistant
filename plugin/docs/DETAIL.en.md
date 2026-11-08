# Moodle Study Assistant

Study your Moodle course chapters from materials saved on your own computer, inside a DeepSeek Harness chat. The plugin verifies chapter material against the school copy, saves the teaching position locally, and reads current assignment, announcement and exam information only when a request needs it. School access is read-only: it never submits work, answers quizzes, posts to forums or changes completion state.

## Who it is for

- Students who already study in their school Moodle and want to work through their own course materials in chat.
- Students who want their materials, credentials and learning records kept on their own machine and in a folder they choose.

## Install

1. Open **Plugins** in DeepSeek Harness and choose **Add plugin**.
2. Paste the absolute path of the trial package folder (or its `.tgz` file), read the third-party notice and click **Install**.
3. Keep the plugin switch **On**, open a new chat and say "help me set up the Moodle study assistant".

No Node, Python, pnpm or configuration-file edits are required; the plugin uses the runtime shipped with Harness or prepares its own.

## First use

1. **Choose a data location** (default `Documents/MoodleStudy`). An existing study folder is reused, never overwritten.
2. **Install the local runtime** (about 20 MB, one-time download).
3. **Connect the school account**: the school page opens in the installed browser and the student completes SSO/MFA personally. The plugin never reads passwords or browser cookies.
4. **Choose courses and material scope**: full sync downloads and indexes the selected courses; information-only sync does not download materials.
5. **First sync**: collect current school information and sync authorized materials, then start studying.

Each step reports its status in chat. A failed or interrupted step can be retried from the settings tools without deleting existing records.

## Teaching behaviour

- Explains one focused concept at a time: concept, notation, conditions, a worked example, then one relevant understanding check. Delivers a whole-chapter handout or a complete answer when the student explicitly asks for one.
- Renders mathematics as LaTeX.
- When a chapter is named, prepares and teaches only that chapter; no automatic whole-course refresh or old-chapter follow-ups.
- Chapter verification runs through the in-chat Moodle tool, reuses the strict receipt for the same chat/chapter while local files are unchanged, and needs no repeated command permission.
- No technical logs or verification receipts at the end of a teaching reply; only a material gap that actually affects the explanation is mentioned briefly.

## Data and privacy

- Personal study data lives only in the student-chosen data folder: courses, notes, mistakes, review history, teaching position and material originals.
- The school API token is stored with the existing Windows DPAPI mechanism under `<data>/.private/` and is decryptable only by the current Windows user. It never appears in chat, logs or configuration.
- The plugin install folder contains program files and shared rules only; upgrades never touch learning data.
- Uninstalling the plugin keeps the data folder by default. Removing it completely is a separate explicit action by the student (data folder plus `%LOCALAPPDATA%\MoodleStudyAssistant` for runtime and logs).

## Known limitations

- Windows only (sign-in and credential protection rely on Windows DPAPI and an installed Edge/Chrome).
- The first setup needs network access to download Python dependencies; failures are reported with a retry path.
- The school must enable the Moodle Mobile Web Service; permissions are controlled by the school.
- DOCX/PPTX files have no page images, so only extracted text is available; verify formulas against PDF originals.
- The plugin exposes no programming or arbitrary command tool and never downloads content outside the authorized scope.

## Uninstall and recover

Uninstall from **Plugins**. Your data folder and the Harness support folder are kept. Reinstall the plugin, choose the same data folder and reconnect the school account if the saved sign-in has expired.
