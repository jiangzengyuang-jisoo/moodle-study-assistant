"""Transactional material versions; history keeps its original evidence and paths.

A version move changes two things only: the physical files and the material index
rows plus actual Markdown link destinations. Historical answers, sources, parent
questions, evidence bindings and memory-correction snapshots are never rewritten,
so their fingerprints stay valid without being recomputed.
"""
from state_io import locked
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlparse
import uuid


POLICY = {"enabled": True, "archive_days": 30, "cleanup_on_use": True,
          "referenced_versions": "keep", "modified_versions": "keep",
          "background_task": False}

# One directory level only; a course page may reuse a filename across modules.
ARCHIVE_SUBDIR = re.compile(r"[A-Za-z0-9._-]{1,80}")


def archive_subdir_name(value):
    """Validate a single safe directory segment used to separate same-name files."""
    if value is None:
        return None
    if (not isinstance(value, str) or not ARCHIVE_SUBDIR.fullmatch(value)
            or value in {".", ".."}):
        raise ValueError("Unsafe archive subdirectory name")
    return value


def current(row):
    return not row.get("superseded_by") and row.get("version_status") not in {"archived", "deleted"}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def instant(value=None):
    return datetime.fromisoformat(value) if value else datetime.now(timezone.utc)


def safe(study, relative):
    study = Path(study).resolve()
    declared = study / relative
    resolved = declared.resolve()
    if resolved != declared or not resolved.is_relative_to(study) or resolved == study:
        raise ValueError("Material path escapes study or uses a link")
    if any(x.lower() in {".git", ".private", ".secrets"} for x in resolved.relative_to(study).parts):
        raise ValueError("Protected path")
    return resolved


def keys(row):
    result = set()
    for source in row.get("moodle_file_sources", []):
        module = source.get("module_id")
        name = source.get("remote_filename") or unquote(urlparse(source.get("url") or "").path.rsplit("/", 1)[-1])
        if module and name:
            # A folder may contain multiple files; filename and remote subpath matter.
            identity = (row.get("course_id"), str(module), source.get("filepath", "/"), name)
            result.add(identity + ((str(source["post_id"]),) if source.get("post_id") is not None else ()))
    return result


def verify(study, row):
    path = safe(study, row["relative_path"])
    if any(p.casefold() in {"_system", "backups", "错题本"} for p in path.relative_to(study).parts):
        raise ValueError("Learning records and system files cannot be versioned as course originals")
    if not row.get("sha256") or not path.is_file() or sha(path) != row["sha256"]:
        raise ValueError("Material is missing or locally modified: " + row["id"])
    from learning_library.extract import extract_document
    if row.get("structure_validation") != "deferred" and path.suffix.lower() in {".pdf", ".pptx", ".docx"}:
        result = extract_document(path)
        if not result.get("pages"):
            raise ValueError("New material has no readable document structure")
    return path


def _notes(study):
    for path in study.rglob("*.md"):
        parts = path.relative_to(study).parts
        if any(x in {"Backups", "Library", "_Archive", ".git"} for x in parts):
            continue
        safe(study, path.relative_to(study))
        yield path


def _note_edits(study, old, archived, previous=()):
    """Migrate actual Markdown link targets only; historical prose stays literal."""
    edits = dict(previous)
    for path in _notes(study):
        original = edits.get(path, path.read_text(encoding="utf-8-sig"))
        def target(match):
            raw = match.group(1)
            bare = raw.strip("<>")
            if "://" in bare:
                return match.group(0)
            link, _, anchor = bare.partition("#")
            try:
                resolved = (path.parent / unquote(link)).resolve()
            except (OSError, ValueError):
                return match.group(0)
            if resolved != old:
                return match.group(0)
            destination = Path(os.path.relpath(archived, path.parent)).as_posix()
            return "](<" + destination + ("#" + anchor if anchor else "") + ">)"
        updated = re.sub(r"\]\((<[^>]+>|[^)]+)\)", target, original)
        if updated != original:
            edits[path] = updated
    return list(edits.items())


def _write(path, value):
    # Do not import the scheduler (and its FSRS dependency) into library startup.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.encode("utf-8") if isinstance(value, str) else value
    if path.suffix == ".json":
        json.loads(data.decode("utf-8-sig"))
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@locked
def recover(state_path):
    path = Path(state_path).resolve()
    journal = path.parent / "material-version-transaction.json"
    if not journal.exists():
        return
    tx = json.loads(journal.read_text(encoding="utf-8"))
    study = path.parent.parent
    present = sha(path)
    if present == tx["after_sha"]:
        journal.unlink()
        return
    if present != tx["before_sha"]:
        raise ValueError("Interrupted version transaction conflicts with newer state; manual recovery required")
    for edit in tx["edits"]:
        target = safe(study, edit["path"])
        backup = safe(study, edit["backup"])
        if sha(target) not in {edit["before_sha"], edit["after_sha"]}:
            raise ValueError("Note changed during recovery; retain transaction for review")
        _write(target, backup.read_bytes())
    for move in reversed(tx["moves"]):
        source, destination = safe(study, move["from"]), safe(study, move["to"])
        if source.exists() and sha(source) == move["sha256"]:
            continue
        if destination.exists():
            if source.exists() or sha(destination) != move["sha256"]:
                raise ValueError("Version move conflict; no file overwritten")
            source.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(source)
        else:
            raise ValueError("Version transaction source is missing; retain journal for review")
    journal.unlink()


@locked
def _commit(path, before, state, moves=(), edits=(), at=None):
    study = path.parent.parent
    state["revision"] = state.get("revision", 0) + 1
    state["updated_at"] = instant(at).isoformat()
    after = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    backup = path.parent / "Backups" / ("material-versions-" + uuid.uuid4().hex)
    backup.mkdir(parents=True)
    _write(backup / "state.json", before)
    operations = []
    for index, (target, text) in enumerate(edits):
        saved = backup / (str(index) + ".md")
        _write(saved, target.read_bytes())
        operations.append({"path": target.relative_to(study).as_posix(),
                           "backup": saved.relative_to(study).as_posix(), "before_sha": sha(target),
                           "after_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    tx = {"before_sha": hashlib.sha256(before).hexdigest(),
          "after_sha": hashlib.sha256(after.encode("utf-8")).hexdigest(),
          "moves": [{"from": a.relative_to(study).as_posix(), "to": b.relative_to(study).as_posix(), "sha256": sha(a)} for a, b in moves],
          "edits": operations}
    if path.read_bytes() != before:
        raise ValueError("State changed; reread before updating versions")
    journal = path.parent / "material-version-transaction.json"
    _write(journal, json.dumps(tx, ensure_ascii=False, indent=2))
    try:
        for source, destination in moves:
            safe(study, destination.relative_to(study))
            if destination.exists():
                raise ValueError("Version destination already exists")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
        for target, text in edits:
            _write(target, text)
        if path.read_bytes() != before:
            raise ValueError("State changed during version transaction")
        _write(path.with_name("state.previous.json"), before)
        _write(path, after)
        journal.unlink()
    except Exception:
        recover(path)
        raise


@locked
def promote(state_path, incoming, old_id=None, at=None):
    """Incoming is a registered local file or a verified download staged in Study."""
    from library import build_lock
    path = Path(state_path).resolve()
    study = path.parent.parent
    with build_lock(path.parent / "MaterialVersions"):
        recover(path)
        before = path.read_bytes()
        state = json.loads(before.decode("utf-8-sig"))
        rows = state.setdefault("materials", [])
        incoming = copy.deepcopy(incoming)
        source = verify(study, incoming)
        existing_new = next((r for r in rows if r["id"] == incoming["id"]), None)
        if existing_new and (existing_new["sha256"] != incoming["sha256"] or existing_new["relative_path"] != incoming["relative_path"]):
            raise ValueError("Material IDs identify immutable versions; use a new ID")
        if existing_new and not current(existing_new):
            raise ValueError("An archived material cannot silently become the latest version")
        candidates = [r for r in rows if current(r) and r["id"] != incoming["id"] and keys(r) & keys(incoming)]
        if old_id:
            old = next((r for r in rows if r["id"] == old_id), None)
            if not old or old["course_id"] != incoming["course_id"]:
                raise ValueError("Old version missing or belongs to another course")
            if old["id"] == incoming["id"]:
                raise ValueError("A material cannot replace itself")
            if old.get("version_status") == "archived" and old.get("superseded_by") == incoming["id"]:
                return {"status": "already_promoted", "material_id": incoming["id"], "state_written": False}
        elif len(candidates) == 1:
            old = candidates[0]
        elif candidates:
            raise ValueError("Multiple source matches; version relationship needs review")
        else:
            old = None
        if old and old["sha256"] == incoming["sha256"]:
            verify(study, old)
            return {"status": "identical", "material_id": old["id"], "state_written": False}
        course, category = incoming["course_id"], incoming.get("category")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", course) or category not in {"Lectures", "Tutorials", "Assignments", "Practice", "Other"}:
            raise ValueError("Classify the incoming material before promoting it")
        name = incoming.get("filename") or source.name
        if Path(name).name != name or any(c in name for c in '/\\:<>|?*'):
            raise ValueError("Unsafe original filename")
        subdir = incoming.get("source_subdirectory")
        if subdir is not None and (not isinstance(subdir, str) or not re.fullmatch(r"source-[a-f0-9]{24}", subdir)):
            raise ValueError("Invalid source subdirectory")
        archive_subdir = archive_subdir_name(incoming.get("archive_subdir"))
        if subdir and archive_subdir and subdir != archive_subdir:
            raise ValueError("Conflicting source subdirectory formats")
        subdir = subdir or archive_subdir
        target = safe(study, Path(course) / category / subdir / name if subdir else Path(course) / category / name)
        moves, edits = [], []
        archived = None
        if old:
            if not current(old) and old.get("superseded_by") != incoming["id"]:
                raise ValueError("Old version has already been replaced by another material")
            original = verify(study, old)
            if any(r["id"] not in {old["id"], incoming["id"]} and r.get("relative_path") == old["relative_path"] for r in rows):
                raise ValueError("File is shared by multiple registered materials; review before moving")
            stamp = hashlib.sha256(old["id"].encode()).hexdigest()[:12]
            archived = safe(study, Path(course) / "_Archive" / (stamp + "-" + old["sha256"][:12]) / original.name)
            if original != archived:
                moves.append((original, archived))
                edits = _note_edits(study, original, archived)
            old.update(relative_path=archived.relative_to(study).as_posix(),
                       version_status="archived", superseded_by=incoming["id"],
                       archived_at=instant(at).isoformat(), archive_sha256=old["sha256"],
                       original_relative_path=old.get("original_relative_path", original.relative_to(study).as_posix()))
        vacated = {a for a, _ in moves}
        if source != target:
            if target.exists() and target not in vacated:
                raise ValueError("Same filename is not proof of supersession; specify the verified old material")
            moves.append((source, target))
            edits = _note_edits(study, source, target, edits)
        incoming.update(relative_path=target.relative_to(study).as_posix(), version_status="current")
        incoming.pop("superseded_by", None)
        if old:
            incoming["supersedes"] = old["id"]
            if old.get("contains_solutions") is True:
                incoming["contains_solutions"] = True
        if existing_new:
            existing_new.update(incoming)
        else:
            rows.append(incoming)
        _commit(path, before, state, moves, edits, at)
        return {"status": "promoted", "material_id": incoming["id"], "archived_id": old["id"] if old else None,
                "references_relocated": len(edits), "state_written": True}


def _referenced(state, row, study):
    needles = [row["id"], row["relative_path"], row.get("original_relative_path", ""), row.get("filename", "")]
    needles = [n.casefold() for n in needles if n]
    ignored = {"materials", "settings", "verification", "activity_log", "moodle_resources", "moodle_audit", "discussion_review", "integrations"}
    text = json.dumps({k: v for k, v in state.items() if k not in ignored}, ensure_ascii=False).casefold()
    if any(n in text for n in needles):
        return True
    for other in state.get("materials", []):
        if other["id"] == row["id"]:
            continue
        content = json.dumps({k: v for k, v in other.items() if k not in {"supersedes", "superseded_by"}}, ensure_ascii=False).casefold()
        if row["id"].casefold() in content or row["relative_path"].casefold() in content:
            return True
    return any(any(n in unquote(p.read_text(encoding="utf-8-sig")).casefold() for n in needles) for p in _notes(study))


@locked
def cleanup(state_path, at=None, force_check=False):
    """Authorized opt-in cleanup on use; never delete referenced or modified originals."""
    from library import build_lock
    path = Path(state_path).resolve()
    study = path.parent.parent
    with build_lock(path.parent / "MaterialVersions"):
        recover(path)
        before = path.read_bytes()
        state = json.loads(before.decode("utf-8-sig"))
        policy = state.get("settings", {}).get("material_version_policy", {})
        if not policy.get("enabled") or not policy.get("cleanup_on_use"):
            return {"deleted": [], "enabled": False}
        now = instant(at)
        marker = path.parent / "Library/version-cleanup.json"
        last = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        if not force_check and last.get("date") == now.date().isoformat():
            return {"deleted": [], "already_checked": True}
        days = policy.get("archive_days", 30)
        if not isinstance(days, int) or days < 30:
            raise ValueError("Automatic retention cannot be shorter than 30 days")
        deleted, kept, files = [], [], []
        for row in state.get("materials", []):
            if row.get("version_status") not in {"archived", "deleted"}:
                continue
            archived_at = row.get("archived_at")
            if not archived_at or now - instant(archived_at) < timedelta(days=days):
                continue
            target = safe(study, row["relative_path"])
            if "_Archive" not in target.relative_to(study).parts:
                kept.append({"id": row["id"], "reason": "not_managed_archive"}); continue
            successor = row
            visited = set()
            while successor.get("superseded_by") and successor["id"] not in visited:
                visited.add(successor["id"])
                successor = next((m for m in state["materials"] if m["id"] == successor["superseded_by"]), {})
            try:
                if not successor or not current(successor):
                    raise ValueError("No current replacement")
                verify(study, successor)
            except (ValueError, OSError, KeyError):
                kept.append({"id": row["id"], "reason": "replacement_unavailable"}); continue
            if not target.is_file() or sha(target) != row.get("archive_sha256") or _referenced(state, row, study):
                kept.append({"id": row["id"], "reason": "referenced_missing_or_modified"}); continue
            row.update(version_status="deleted", deleted_at=now.isoformat())
            files.append((target, row["archive_sha256"]))
            deleted.append(row["id"])
        if deleted:
            _commit(path, before, state, at=at)
        committed = path.read_bytes()
        # State keeps the identity, hash and tombstone. No recursive deletion.
        for target, expected in files:
            safe(study, target.relative_to(study))
            if path.read_bytes() != committed:
                raise ValueError("State changed during cleanup; remaining archives preserved")
            if target.is_file() and sha(target) == expected:
                target.unlink()
        marker.parent.mkdir(parents=True, exist_ok=True)
        _write(marker, json.dumps({"date": now.date().isoformat(), "deleted": deleted}, ensure_ascii=False))
        return {"deleted": deleted, "kept": kept, "retention_days": days}
