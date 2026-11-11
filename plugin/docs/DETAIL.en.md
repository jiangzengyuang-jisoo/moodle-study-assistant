# Moodle Study Assistant

Study your Moodle course chapters from materials saved on your own computer, inside a DeepSeek Harness chat. The plugin verifies chapter material against the school copy, saves the teaching position locally, and reads current assignment, announcement and exam information only when a request needs it. School access is read-only: it never submits work, answers quizzes, posts to forums or changes completion state.

## Who it is for

- Students who already study in their school Moodle and want to work through their own course materials in chat.
- Students who want their materials, credentials and learning records kept on their own machine and in a folder they choose.

## Install

After the npm release, the recommended way is **Plugins → Discover**: add the community plugin index, then install **Moodle Study Assistant (Windows trial)**. You can also install the npm package `dsh-moodle-study@0.6.0-trial.5` directly. For the offline trial package:

1. Open **Plugins** in DeepSeek Harness and choose **Add plugin**.
2. Paste the absolute path of the trial package folder (or its `.tgz` file), read the third-party notice and click **Install**.
3. Keep the plugin switch **On**, open a new chat and say "help me set up the Moodle study assistant".

No Node, Python, pnpm or configuration-file edits are required; the plugin uses the runtime shipped with Harness or prepares its own.

## First use

1. **Choose a data location** (default `Documents/MoodleStudy`). An existing study folder is reused, never overwritten.
2. **Install the local runtime** (about 20 MB, one-time download).
3. **Connect the school account**: the school page opens in the installed browser and the student completes SSO/MFA personally. The plugin never reads passwords or browser cookies.
4. **Choose courses and material scope**: full sync downloads and indexes the selected courses; information-only sync does not download materials.
5. **First sync and finish**: collect current school information and sync authorized materials; setup completes only when real sync evidence matches the current account and course authorization, and studying can start right away.

Each step reports its status in chat. After a successful sign-in or a data-location change the tools reconnect automatically (a few seconds; tool names stay the same). Setup progress, materials and sign-in state are kept locally: **if any step is interrupted, reopening the chat continues from the current step** without starting over and without deleting existing records.

**Completion is based on real sync evidence**: setup is marked complete only when the school collection snapshot and material-sync report exist and match the current account, courses and material scope. With full material sync the school collection itself must also be complete (for example an assignment/announcement query must not have failed); downloaded files are kept but setup is not marked complete when collection failed. An empty data folder, a sync that never started, failed, was interrupted, is partial, has an incomplete collection or is stale is never reported as complete; instead the assistant explains the available range and remaining issues per course, and a re-run sync fills the gaps incrementally without re-downloading successful content.

## Settings maintenance and recovery

- **Change courses or material scope**: say what to adjust in chat; the assistant lists the courses again and saves the new selection after you confirm. Previously confirmed materials are kept.
- **Change the data location**: switch to another folder; the old folder is left untouched. The old folder's sign-in credential is not copied to the new location, so if the new folder has no sign-in of its own you reconnect the school there once.
- **Repair the runtime**: if an install was interrupted or the environment is damaged, the assistant can re-run dependency installation; completed steps are skipped and no plugin reinstall is needed.
- **Keep studying offline**: while the school is temporarily unreachable, the assistant can still read local materials, teach chapter by chapter and continue the teaching record. When you need current assignments, announcements or exam arrangements, it says the result is not the latest online information instead of claiming a completed sync or fresh verification.
- **Connection state vs. signing in again**: a temporary network/API failure only needs a reconnect retry, not a new sign-in; a new sign-in is needed only when the saved sign-in has expired or cannot be decrypted.
- **Incremental sync**: after an interruption or partial failure, re-running the sync fills in what is missing without re-downloading content that already succeeded.

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
- The data folder cannot be the plugin package folder or any folder inside it: plugin upgrade, repair or uninstall replaces the whole package. Choose an independent data folder instead; existing independent folders are unaffected.
- Uninstalling the plugin keeps the data folder by default. Removing it completely is a separate explicit action by the student (data folder plus `%LOCALAPPDATA%\MoodleStudyAssistant` for runtime and logs).

## Known limitations

- Windows only (sign-in and credential protection rely on Windows DPAPI and an installed Edge/Chrome).
- The first setup needs network access to download Python dependencies; failures are reported with a retry path.
- The school must enable the Moodle Mobile Web Service; permissions are controlled by the school.
- DOCX/PPTX files have no page images, so only extracted text is available; verify formulas against PDF originals.
- **Local storage does not mean offline inference**: during an outage only local material access and the local teaching record are guaranteed; model calls still depend on the model service Harness currently uses.
- The plugin exposes no programming or arbitrary command tool and never downloads content outside the authorized scope.
- This is a trial version: real school sign-in and installation on other computers or Windows accounts have not all been verified. If a problem occurs, follow the recovery entry points and keep the data folder.

## Uninstall and recover

Uninstall from **Plugins**. Your data folder and the Harness support folder are kept. Reinstall the plugin, choose the same data folder and reconnect the school account if the saved sign-in has expired. Upgrading in the middle of setup does not lose progress either: reopening the chat continues from the current step.

