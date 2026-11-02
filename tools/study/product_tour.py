"""Resumable product orientation. This is not a subject-mastery assessment."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from state_io import bridge, digest, prepare_state, read, workspace_lock

ROOT = Path(__file__).resolve().parents[2]
VERSION = 1
EXAMPLES = [
    {'say': '我今天有一小时，帮我安排学习', 'does': '结合截止日期、实际进度和可用时间安排任务'},
    {'say': '带我学习这门课，先从我上次的位置继续', 'does': '读取接续和课件原页，讲解后让你独立尝试'},
    {'say': '我把资料放好了，帮我加入课程', 'does': '从 Study/_Inbox/ 核对归档，保留原文件'},
    {'say': '检查这道题哪里错了', 'does': '核对你的原答，保存真实卡点，安排后续复测'},
    {'say': '刷新 Moodle，看看老师有没有新要求', 'does': '重新采集并核读；同一聊天普通追问不会不断刷新'},
    {'say': '你记得我学到哪里吗？依据是什么？', 'does': '查本地记录与原证据；记错时可以直接纠正'},
]


def tour_status(state=None):
    policy = (state or {}).get('settings', {}).get('product_onboarding', {})
    if not isinstance(policy, dict) or not isinstance(policy.get('events', []), list):
        raise ValueError('使用引导记录格式异常，请保留原记录并核对')
    events = policy.get('events', [])
    acknowledged = any(isinstance(event, dict) and event.get('version') == VERSION
                       and event.get('outcome') in ('shown', 'skipped') for event in events)
    return {'version': VERSION, 'pending': not acknowledged,
            'next_action': 'teach_in_current_chat' if not acknowledged else 'continue_user_task',
            'instruction': '安装或课程初始化完成后，本轮主动讲用法并邀请一次实际操作；不让用户另开 CMD。'
                           '工具输出不是已经教过用户；先在聊天展示，再登记 shown。用户明确跳过才登记 skipped。',
            'examples': EXAMPLES,
            'first_practice': '让用户选：安排今天 / 继续一门课 / 加入资料。只问所选任务缺少的信息并实际执行。',
            'boundaries': ['密码和验证码只在学校网页输入', '采集、下载、核读和掌握分别说明',
                           '结束时保存实际进度；新聊天在同一项目接续', '没有后台监控，不自动提交作业或发邮件'],
            'replay': '用户随时说“再教我怎么用”，即使 pending=false 也重新讲解。'}


def record_tour(path, outcome, chat):
    if outcome not in ('shown', 'skipped') or not isinstance(chat, str) or not chat.strip():
        raise ValueError('需要实际展示或本人明确跳过，并传入原始聊天 ID')
    path = Path(path)
    with workspace_lock(path):
        prepare_state(path)
        original = read(path)
        tour_status(original)  # Refuse malformed old records, never reset them.
        state = copy.deepcopy(original)
        policy = state.setdefault('settings', {}).setdefault('product_onboarding', {})
        events = policy.setdefault('events', [])
        event_id = digest({'version': VERSION, 'chat': chat, 'outcome': outcome})
        if any(event.get('id') == event_id for event in events if isinstance(event, dict)):
            return {'status': 'already_recorded', **tour_status(original)}
        events.append({'id': event_id, 'version': VERSION, 'outcome': outcome,
                       'at': datetime.now(timezone.utc).isoformat()})
        bridge(path, 'replace', {'expected': original, 'state': state})
        return {'status': 'recorded', **tour_status(state)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'shown', 'skipped'), nargs='?', default='status')
    parser.add_argument('--chat', default='')
    args = parser.parse_args()
    path = ROOT / 'Study/_System/state.json'
    if args.action == 'status':
        result = tour_status(read(path) if path.exists() else None)
    else:
        result = record_tour(path, args.action, args.chat)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (ValueError, OSError):
        print('使用引导状态未能安全读取或保存；请核对本地档案，未重建学习记录。', file=sys.stderr)
        sys.exit(1)
