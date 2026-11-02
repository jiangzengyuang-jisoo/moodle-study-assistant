"""Course-level library authorization, batch installation and index rebuild.

The Moodle collector enumerates an authorized course page and stages verified
downloads inside ``Study/_Inbox/.staging``. This module persists the course-level
authorization, installs the whole batch through the existing version transaction
(no per-file approval, no learning-state writes), and rebuilds the local catalog
once afterwards. Every command reads JSON on stdin and writes JSON on stdout;
failures go to stderr with exit code 1.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from library import DEFAULT_STATE, Library, digest, resolve_course
from learning_library import versions
from material_versions import install_download
from state_io import atomic, prepare_state, workspace_lock

SCHEMA = 1
MODES = ("full", "info_only")
MAX_SCOPE = 2000
RUN_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
ARCHIVE_SUBDIR = re.compile(r"[A-Za-z0-9._-]{1,80}")
FIXED_AUTHORIZATION = {"new_course_requires_authorization": True,
                       "per_file_approval_required": False,
                       "background_refresh": False}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def known_courses(state):
    return {course["id"]: course for course in state.get("courses", [])
            if isinstance(course, dict) and course.get("id")}


def save_state(path, state, before_state, at=None):
    """Write state.previous.json first, then the new state atomically.

    ``at`` lets one operation reuse its own timestamp so the authorization block and
    the state row never disagree by a clock tick.
    """
    state["revision"] = before_state.get("revision", 0) + 1
    state["updated_at"] = at or utc_now()
    atomic(Path(path).with_name("state.previous.json"), before_state)
    atomic(path, state)


def authorization_signature(auth):
    """Compare meaningful authorization content; timestamps alone are not a change."""
    if not isinstance(auth, dict):
        return None
    courses = auth.get("courses") if isinstance(auth.get("courses"), dict) else {}
    normalized = {}
    for course_id, entry in courses.items():
        if not isinstance(entry, dict):
            normalized[course_id] = entry
            continue
        normalized[course_id] = {key: entry.get(key)
                                 for key in ("moodle_courseid", "mode", "scope_version")}
    return {"schema": auth.get("schema"), "scope_version": auth.get("scope_version"),
            "scope": auth.get("scope"),
            "new_course_requires_authorization": auth.get("new_course_requires_authorization"),
            "per_file_approval_required": auth.get("per_file_approval_required"),
            "background_refresh": auth.get("background_refresh"),
            "courses": normalized}


def _authorization_draft(state, payload):
    if not isinstance(payload, dict):
        raise ValueError("Authorization request must be a JSON object")
    mode = payload.get("mode")
    if mode not in MODES:
        raise ValueError("Authorization mode must be full or info_only")
    scope = payload.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("Authorization scope must be a non-empty description")
    if len(scope) > MAX_SCOPE:
        raise ValueError("Authorization scope exceeds " + str(MAX_SCOPE) + " characters")
    scope_version = payload.get("scope_version", 1)
    if isinstance(scope_version, bool) or not isinstance(scope_version, int) or scope_version < 1:
        raise ValueError("Invalid authorization scope version")
    requested = payload.get("courses")
    if not isinstance(requested, list) or not requested:
        raise ValueError("At least one course authorization is required")
    courses = known_courses(state)
    settings = state.get("settings")
    previous = settings.get("course_library_authorization") if isinstance(settings, dict) else None
    existing = previous.get("courses") if isinstance(previous, dict) else None
    merged = dict(existing) if isinstance(existing, dict) else {}
    now = utc_now()
    for entry in requested:
        if not isinstance(entry, dict):
            raise ValueError("Each course authorization must be a JSON object")
        course_id = entry.get("course_id")
        if not isinstance(course_id, str) or course_id not in courses:
            raise ValueError("Unknown course: " + str(course_id))
        moodle_courseid = entry.get("moodle_courseid")
        if isinstance(moodle_courseid, str) and moodle_courseid.isdigit():
            moodle_courseid = int(moodle_courseid)
        page_ids = {str(value) for value in courses[course_id].get("moodle_course_ids") or []}
        if (isinstance(moodle_courseid, bool) or not isinstance(moodle_courseid, int)
                or str(moodle_courseid) not in page_ids):
            raise ValueError("moodle_courseid does not belong to selected course: " + course_id)
        entry_mode = entry.get("mode", mode)
        if entry_mode not in MODES:
            raise ValueError("Course authorization mode must be full or info_only")
        merged[course_id] = {"moodle_courseid": moodle_courseid, "mode": entry_mode,
                             "authorized_at": now, "scope_version": scope_version}
    return {"schema": SCHEMA, "scope_version": scope_version, "scope": scope,
            "updated_at": now, **FIXED_AUTHORIZATION, "courses": merged}


def _authorization_response(auth, revision, state_written, mode, requested):
    """Report the pages authorized or refreshed by this call."""
    courses = auth.get("courses") if isinstance(auth, dict) else {}
    courses = courses if isinstance(courses, dict) else {}
    full = sorted(course_id for course_id in requested
                  if isinstance(courses.get(course_id), dict)
                  and courses[course_id].get("mode") == "full")
    info_only = sorted(course_id for course_id in requested
                       if isinstance(courses.get(course_id), dict)
                       and courses[course_id].get("mode") == "info_only")
    return {"saved": True, "revision": revision, "mode": mode,
            "authorized_courses": full, "info_only_courses": info_only,
            "state_written": state_written}


def _requested_course_ids(payload):
    requested = []
    for entry in payload.get("courses") or []:
        course_id = entry.get("course_id")
        if course_id not in requested:
            requested.append(course_id)
    return requested


def authorize(state_path, payload):
    """Persist (or merge) the user's course-level download authorization."""
    path = Path(state_path).resolve()
    with workspace_lock(path):
        prepare_state(path)
        before = path.read_bytes()
        before_state = json.loads(before.decode("utf-8-sig"))
        state = copy.deepcopy(before_state)
        auth = _authorization_draft(state, payload)
        requested = _requested_course_ids(payload)
        settings = state.get("settings")
        if not isinstance(settings, dict):
            settings = state["settings"] = {}
        previous = settings.get("course_library_authorization")
        if previous is not None and authorization_signature(previous) == authorization_signature(auth):
            return _authorization_response(previous, before_state.get("revision", 0), False,
                                           payload.get("mode"), requested)
        settings["course_library_authorization"] = auth
        save_state(path, state, before_state, at=auth["updated_at"])
        return _authorization_response(auth, state["revision"], True, payload.get("mode"), requested)


def _validate_filepath(value):
    if value in (None, ""):
        return "/"
    if (not isinstance(value, str) or len(value) > 500 or "\\" in value
            or "\x00" in value or not value.startswith("/")):
        raise ValueError("Unsafe source filepath")
    if ".." in value.split("/"):
        raise ValueError("Unsafe source filepath")
    return value


def _source_keys(course_id, source):
    module = source.get("module_id")
    name = source.get("filename")
    if module in (None, "") or not name:
        return set()
    return {(course_id, str(module), source.get("filepath") or "/", name)}


def _duplicate_material(state, course_id, sha256, incoming_keys):
    """A current material with the same bytes from another source is a duplicate.

    The same source and bytes stay with install_download, which reports identical
    without rewriting state.
    """
    same_content = [material for material in state.get("materials", [])
                    if versions.current(material) and material.get("course_id") == course_id
                    and material.get("sha256") == sha256]
    if any(versions.keys(material) & incoming_keys for material in same_content):
        return None
    return same_content[0] if same_content else None


def _item_result(item, **values):
    result = {"item_key": item.get("item_key") if isinstance(item, dict) else None,
              "status": None, "material_id": None, "relative_path": None,
              "duplicate_of": None, "archived_id": None, "error": None}
    result.update(values)
    return result


def _install_item(state_path, state, item, index):
    if not isinstance(item, dict):
        raise ValueError("Batch item " + str(index + 1) + " must be a JSON object")
    item_key = item.get("item_key")
    if not isinstance(item_key, str) or not item_key.strip():
        raise ValueError("Batch item is missing item_key")
    source = item.get("source")
    if not isinstance(source, dict):
        raise ValueError("Batch item source must be a JSON object")
    requested_course = source.get("course_id")
    if not isinstance(requested_course, str) or not requested_course:
        raise ValueError("Batch item is missing source.course_id")
    courses = known_courses(state)
    course_id = resolve_course(state, requested_course)
    if course_id not in courses:
        raise ValueError("Unknown course or subject: " + requested_course)
    module = source.get("module_id")
    if isinstance(module, bool) or not isinstance(module, (int, str)) or str(module) == "":
        raise ValueError("Batch item is missing a stable source module identity")
    filename = source.get("filename")
    if not isinstance(filename, str) or not filename:
        raise ValueError("Batch item is missing source.filename")
    if Path(filename).name != filename or any(character in filename for character in '/\\:<>|?*'):
        raise ValueError("Unsafe original filename: " + filename)
    sha256 = item.get("sha256")
    if isinstance(sha256, str):
        sha256 = sha256.lower()
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Batch item is missing a valid SHA-256")
    relative_path = item.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("Batch item is missing the staged relative_path")
    study = state_path.parent.parent
    staged = versions.safe(study, relative_path)
    if "_Inbox" not in staged.relative_to(study).parts:
        raise ValueError("Downloads must be staged in _Inbox before installation")
    if not staged.is_file():
        raise ValueError("Staged download is missing: " + relative_path)
    if digest(staged) != sha256:
        raise ValueError("Staged download hash does not match the declared SHA-256")
    expected_bytes = item.get("bytes")
    if expected_bytes is not None and (isinstance(expected_bytes, bool)
                                      or not isinstance(expected_bytes, int)
                                      or expected_bytes != staged.stat().st_size):
        raise ValueError("Staged download size does not match the declared byte count")
    filepath = _validate_filepath(source.get("filepath"))
    archive_subdir = item.get("archive_subdir")
    if archive_subdir is not None:
        if (not isinstance(archive_subdir, str) or not ARCHIVE_SUBDIR.fullmatch(archive_subdir)
                or archive_subdir in {".", ".."}):
            raise ValueError("Unsafe archive subdirectory name")
    extra = item.get("extra")
    if extra is not None and not isinstance(extra, dict):
        raise ValueError("Batch item extra must be a JSON object")
    source_pages = item.get("source_pages")
    if source_pages is not None and not isinstance(source_pages, list):
        raise ValueError("Batch item source_pages must be an array")
    install_source = dict(source)
    install_source["course_id"] = course_id
    install_source["filepath"] = filepath
    if archive_subdir is not None:
        install_source["archive_subdir"] = archive_subdir
    install_extra = dict(extra or {})
    if source_pages is not None:
        install_extra["source_pages"] = source_pages
    duplicate = _duplicate_material(state, course_id, sha256, _source_keys(course_id, install_source))
    if duplicate is not None:
        return _item_result(item, status="duplicate", material_id=duplicate.get("id"),
                            relative_path=duplicate.get("relative_path"),
                            duplicate_of=duplicate.get("id"))
    data = {"source": install_source, "relative_path": relative_path, "sha256": sha256,
            "bytes": staged.stat().st_size, "extra": install_extra}
    installed = install_download(state_path, data, sync=False)
    status = installed.get("status")
    if status == "already_promoted":
        status = "identical"
    if status not in {"promoted", "identical"}:
        raise ValueError("Unknown installation result: " + str(status))
    return _item_result(item, status=status, material_id=installed.get("material_id"),
                        relative_path=installed.get("relative_path"),
                        archived_id=installed.get("archived_id"))


def install_batch(state_path, payload):
    """Install every verified staged download; per-item failures never abort the batch."""
    path = Path(state_path).resolve()
    if not isinstance(payload, dict):
        raise ValueError("Install batch request must be a JSON object")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("Invalid batch run_id")
    snapshot = payload.get("snapshot")
    if snapshot is not None and not isinstance(snapshot, dict):
        raise ValueError("Batch snapshot must be an object or null")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("Batch items must be an array")
    results = []
    with workspace_lock(path):
        prepare_state(path)
        state = load(path)
        for index, item in enumerate(items):
            try:
                results.append(_install_item(path, state, item, index))
            except Exception as error:  # one bad item must not discard the verified batch
                results.append(_item_result(item, status="error", error=str(error)))
            state = load(path)
        revision = state.get("revision")
    return {"items": results, "state_revision": revision}


def build_index(state_path, payload):
    """Rebuild the local catalog once for every requested subject."""
    path = Path(state_path).resolve()
    if not isinstance(payload, dict):
        raise ValueError("Build index request must be a JSON object")
    requested = payload.get("courses")
    if not isinstance(requested, list) or any(not isinstance(course, str) or not course for course in requested):
        raise ValueError("courses must be an array of course ids")
    with workspace_lock(path):
        prepare_state(path)
        state = load(path)
    courses = known_courses(state)
    resolved = []
    for requested_course in requested:
        course_id = resolve_course(state, requested_course)
        if course_id not in courses:
            raise ValueError("Unknown course: " + requested_course)
        if course_id not in resolved:
            resolved.append(course_id)
    library = Library(path)
    reports = {course_id: library.sync(course_id) for course_id in resolved}
    return {"courses": reports, "state_revision": load(path).get("revision")}


def status(state_path):
    """Read-only authorization, material counts and last library sync marker."""
    path = Path(state_path).resolve()
    if (path.parent / "material-version-transaction.json").exists():
        raise ValueError("Interrupted material update; recover versions before reading status")
    if not path.is_file():
        raise ValueError("Learning state not found: " + str(path))
    state = load(path)
    settings = state.get("settings") if isinstance(state.get("settings"), dict) else {}
    materials = state.get("materials", [])
    current = sum(1 for material in materials if versions.current(material))
    return {"authorization": settings.get("course_library_authorization"),
            "materials": {"current": current, "archived": len(materials) - current},
            "last_sync": None,
            "state_revision": state.get("revision")}


def stdin_payload():
    sys.stdin.reconfigure(encoding="utf-8")
    return json.load(sys.stdin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("authorize")
    sub.add_parser("install-batch")
    sub.add_parser("build-index")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "authorize":
        result = authorize(args.state, stdin_payload())
    elif args.command == "install-batch":
        result = install_batch(args.state, stdin_payload())
    elif args.command == "build-index":
        result = build_index(args.state, stdin_payload())
    else:
        result = status(args.state)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(1)
