"""Current-use eligibility, kept separate from immutable historical references."""
from datetime import datetime

from learning_library.versions import current


def material_issue(material, *, attempted_at=None):
    """Return a reason to recheck a source, without retargeting its material ID.

    An actual answer completed before a documented replacement can still be
    recorded against its original source. This exception never selects new work.
    """
    if material is None:
        return "课件来源缺失，需要重新核验题目"
    if current(material):
        return None
    archived_at = material.get("archived_at")
    if attempted_at is not None and archived_at:
        try:
            archived = datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
            if attempted_at < archived:
                return None
        except (ValueError, TypeError, AttributeError):
            pass
    return "课件已归档或被新版替代，需要按当前资料重新核验题目"


def source_issue(state, source, *, attempted_at=None):
    identity = source.get("material_id") if isinstance(source, dict) else None
    material = next((m for m in state.get("materials", []) if m.get("id") == identity), None)
    return material_issue(material, attempted_at=attempted_at)
