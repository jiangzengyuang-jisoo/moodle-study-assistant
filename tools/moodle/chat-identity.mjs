/**
 * Stable conversation identity for the Node entry points.
 *
 * Mirrors tools/study/chat_identity.py: an explicit --chat wins; STUDY_CHAT_ID
 * is the agreed fallback name for clients without a stable ID; the DeepSeek
 * Harness session ID is accepted only in its main-chat form (`session-<uuid>`).
 * A delegated internal session ID (bare UUID) must never become the original
 * chat ID, and no random ID is generated here.
 */

const MAIN_SESSION = /^session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ANY_SESSION = /^(?:session-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function resolveChatId(explicit, env = process.env) {
  const value = String(explicit ?? '').trim();
  if (value) return { chat: value, source: 'argument' };
  const study = String(env.STUDY_CHAT_ID ?? '').trim();
  if (study) return { chat: study, source: 'env:STUDY_CHAT_ID' };
  const dsh = String(env.DSH_SESSION_ID ?? '').trim();
  if (dsh) {
    if (MAIN_SESSION.test(dsh)) return { chat: dsh, source: 'env:DSH_SESSION_ID' };
    throw new Error(
      '检测到 DeepSeek Harness 的内部会话 ID，不能用它当原始聊天 ID：' +
      '那会把当前聊天当成新聊天。内部任务必须显式传入主聊天 ID 并加 --subtask；' +
      '无法取得主聊天 ID 时用 --chat 或 STUDY_CHAT_ID 与本人约定会话名称。');
  }
  return null;
}

export function describeSources(env = process.env) {
  const dsh = String(env.DSH_SESSION_ID ?? '').trim();
  const study = String(env.STUDY_CHAT_ID ?? '').trim();
  return {
    has_study_chat_id: Boolean(study),
    has_dsh_session_id: Boolean(dsh),
    dsh_session_kind: !dsh ? 'none' : MAIN_SESSION.test(dsh) ? 'main'
      : ANY_SESSION.test(dsh) ? 'internal' : 'unknown',
    auto_resolvable: Boolean(study || MAIN_SESSION.test(dsh)),
  };
}
