"""Derived question identity and dated exposure; never rewrite learning records."""
from datetime import datetime
import json


def _text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def question_key(course_id, question):
    """Match a question across container IDs, not merely a shared topic or answer.

    Source annotations, marking keys and IDs are not question content. Option
    order/letters do not make a new question; different option text does.
    Missing course/source identity is left unmatched rather than guessed.
    """
    prompt = _text(question.get("prompt") or question.get("stem"))
    sources = question.get("sources") or [question.get("source")]
    if not course_id or not prompt or not sources:
        return None
    locations = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("material_id") or not isinstance(source.get("pages"), list):
            return None
        locations.append((source["material_id"], tuple(sorted(set(map(str, source["pages"]))))))
    options = question.get("options") or {}
    if not isinstance(options, dict):
        return None
    content = [course_id, prompt, sorted(_text(v) for v in options.values()), sorted(set(locations))]
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))


def review_key(state, item):
    course = item.get("course_id")
    if not course:
        course = next((q.get("course_id") for q in state.get("review_queue", [])
                       if q.get("id") == item.get("review_id")), None)
    return question_key(course, item)


def occurred_by(shown_at, attempted_at):
    if not shown_at:
        return False
    try:
        shown = datetime.fromisoformat(shown_at.replace("Z", "+00:00"))
        return shown <= attempted_at
    except (ValueError, TypeError, AttributeError):
        # An explicit exposure with an unknown time cannot establish independence.
        return True


def was_exposed(state, key, at):
    """Read existing session/variant/mock evidence without migrating old state."""
    if key is None:
        return False
    for session in state.get("review_sessions", []):
        for item in session.get("items", []):
            if review_key(state, item) == key and occurred_by(item.get("answer_shown_at"), at):
                return True
    for item in state.get("review_queue", []):
        for variant in item.get("practice_variants", []):
            if question_key(item.get("course_id"), variant) == key and occurred_by(variant.get("answer_shown_at"), at):
                return True
    for mock in state.get("mock_exams", []):
        matching = {q.get("id") for q in mock.get("questions", [])
                    if question_key(mock.get("profile_snapshot", {}).get("course_id"), q) == key}
        if matching and any(matching.intersection(e.get("question_ids", []))
                            and occurred_by(e.get("shown_at"), at)
                            for e in mock.get("answer_exposures", [])):
            return True
    return False
