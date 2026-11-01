"""Memory corrections share the public workspace's lock and state validation."""
from pathlib import Path
from state_io import workspace_lock, prepare_state, read, bridge
from learning_library.memory import apply_correction


def save_correction(path, course_id, payload):
    path = Path(path)
    with workspace_lock(path):
        prepare_state(path)
        original = read(path)
        draft, changed = apply_correction(original, course_id, payload)
        if changed:
            bridge(path, 'replace', {'expected': original, 'state': draft})
        return {'saved': changed, 'already_saved': not changed,
                'revision': draft.get('revision'), 'correction_id': payload['id'],
                # A retry may follow a successful save whose view rebuild failed.
                'original_evidence_preserved': True, 'views_need_refresh': True}
