"""Resolve the stable conversation identity used by the study entry points.

The conversation ID belongs to the client that runs this project, not to the
tools. The rules below preserve the existing protocol in docs/SESSION.md:

* an explicit ``--chat`` always wins (Codex and other clients keep passing the
  original thread ID);
* ``STUDY_CHAT_ID`` is the documented fallback for clients that cannot expose a
  stable chat ID: the learner states one name once and keeps using it;
* DeepSeek Harness exposes the persistent session ID of the calling agent in
  every shell call. A main chat session ID has the form ``session-<uuid>``.
  Delegated internal sessions receive a bare UUID, which must never be used as
  the original chat ID: that would start a separate first collection. Internal
  tasks must receive the main chat ID explicitly and add ``--subtask``.

No random ID and no shared constant is generated here. When the identity cannot
be determined with confidence the caller gets a clear, recoverable error that
lists the two supported ways to name a conversation.
"""

import argparse
import json
import os
import re
import sys

DSH_MAIN_SESSION = re.compile(
    r'^session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
DSH_ANY_SESSION = re.compile(
    r'^(?:session-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

MISSING_HELP = (
    '未提供原始用户聊天 ID，也没有可用的稳定会话标识。'
    '请在命令中显式传入 --chat "原始聊天ID"；无法取得稳定 ID 的客户端，'
    '请与本人约定一个会话名称并通过 --chat 或环境变量 STUDY_CHAT_ID 沿用同一名称，'
    '不要随机生成，也不要让所有聊天共用同一个固定 ID。')


class ChatIdentityError(ValueError):
    """The caller must fix the conversation identity before any study write."""


def resolve_chat_id(explicit=None, env=None):
    """Return ``(chat_id, source)`` using only stable, non-random identities.

    ``source`` is one of ``argument``, ``env:STUDY_CHAT_ID``,
    ``env:DSH_SESSION_ID``. It is used by diagnostics and by the subtask gate.
    """
    environment = os.environ if env is None else env
    value = (explicit or '').strip()
    if value:
        return value, 'argument'

    value = (environment.get('STUDY_CHAT_ID') or '').strip()
    if value:
        return value, 'env:STUDY_CHAT_ID'

    value = (environment.get('DSH_SESSION_ID') or '').strip()
    if value:
        if DSH_MAIN_SESSION.match(value):
            return value, 'env:DSH_SESSION_ID'
        raise ChatIdentityError(
            '检测到 DeepSeek Harness 的内部会话 ID（{value}），不能用它当原始聊天 ID：'
            '那会把当前聊天当成新聊天并另做一次首次采集。内部任务必须显式传入主聊天 ID '
            '并加 --subtask；若确实无法取得主聊天 ID，请用 --chat 或 STUDY_CHAT_ID '
            '与本人约定会话名称后沿用。'.format(value=value))

    raise ChatIdentityError(MISSING_HELP)


def describe(env=None):
    """Return a safe, shareable description of the identity sources (no secrets)."""
    environment = os.environ if env is None else env
    dsh = (environment.get('DSH_SESSION_ID') or '').strip()
    study = (environment.get('STUDY_CHAT_ID') or '').strip()
    if study:
        kind = 'study-chat-id'
    elif not dsh:
        kind = 'none'
    elif DSH_MAIN_SESSION.match(dsh):
        kind = 'dsh-main-chat'
    elif DSH_ANY_SESSION.match(dsh):
        kind = 'dsh-internal-session'
    else:
        kind = 'dsh-unknown-format'
    return {
        'kind': kind,
        'has_study_chat_id': bool(study),
        'has_dsh_session_id': bool(dsh),
        'dsh_session_kind': 'main' if DSH_MAIN_SESSION.match(dsh) else (
            'internal' if DSH_ANY_SESSION.match(dsh) else 'unknown'),
        'auto_resolvable': kind in ('study-chat-id', 'dsh-main-chat'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chat', help='显式原始聊天 ID；给出时不读取环境变量')
    args = parser.parse_args(argv)
    result = describe()
    try:
        chat, source = resolve_chat_id(args.chat)
    except ChatIdentityError as error:
        result.update(status='unresolved', message=str(error))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    result.update(status='resolved', chat_id=chat, source=source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    sys.exit(main())
