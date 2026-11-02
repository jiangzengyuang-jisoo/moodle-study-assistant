"""Manage current material versions without resetting learning evidence."""
from state_io import locked
import argparse
import copy
import json
from pathlib import Path
import sys
from urllib.parse import urlparse, parse_qs

from library import DEFAULT_STATE, Library, build_lock, is_solution
from learning_library import versions


# Download metadata may enrich a material row, never redefine its identity.
EXTRA_FIELDS = {"kind", "capture", "source_pages"}
RESERVED_FIELDS = {"id", "course_id", "filename", "relative_path", "sha256", "bytes",
                   "page_map", "moodle_file_sources", "version", "archive_subdir",
                   "contains_solutions", "version_status", "supersedes", "superseded_by",
                   "original_relative_path", "archive_sha256", "archived_at",
                   "verified", "mastery"}


def _download_extra(extra):
    """Whitelist non-identity provenance supplied by the course collector."""
    if extra is None:
        return {}
    if not isinstance(extra, dict):
        raise ValueError("Download metadata must be a JSON object")
    fields = {}
    for key, value in extra.items():
        if key in RESERVED_FIELDS:
            raise ValueError("Reserved material field cannot be supplied by download metadata: " + str(key))
        if key not in EXTRA_FIELDS:
            continue
        if key == "kind":
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise ValueError("Invalid material kind")
            fields["kind"] = value.strip()
        elif key == "capture":
            if not isinstance(value, dict):
                raise ValueError("Capture metadata must be a JSON object")
            fields["capture"] = value
        elif key == "source_pages":
            if not isinstance(value, list) or not all(isinstance(page, dict) for page in value):
                raise ValueError("Source page provenance must be a list of objects")
            fields["source_pages"] = value
    return fields


@locked
def configure(path, days=30):
    if days < 30:
        raise ValueError("Retention must be at least 30 days")
    path = Path(path).resolve()
    with build_lock(path.parent / "MaterialVersions"):
        versions.recover(path)
        ignore = versions.safe(path.parent.parent, '.ignore')
        existing_ignore = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
        if '**/_Archive/**' not in existing_ignore.splitlines():
            versions._write(ignore, existing_ignore.rstrip() + '\n# Historical materials: opt in explicitly to inspect.\n**/_Archive/**\n')
        before = path.read_bytes()
        state = json.loads(before.decode("utf-8-sig"))
        policy = {**versions.POLICY, "archive_days": days}
        if state.setdefault("settings", {}).get("material_version_policy") == policy:
            return {"configured": True, "state_written": False}
        state["settings"]["material_version_policy"] = policy
        versions._commit(path, before, state)
        return {"configured": True, "policy": policy, "state_written": True}


def _reuse_identical(path, incoming, material_id):
    """Caller holds the workspace lock; retain identity and only strengthen protection."""
    before = path.read_bytes()
    state = json.loads(before.decode("utf-8-sig"))
    existing = next((m for m in state.get("materials", []) if m["id"] == material_id
                     and versions.current(m) and m.get("sha256") == incoming["sha256"]), None)
    if existing is None:
        raise ValueError("Identical material changed; reread before installing")
    versions.verify(path.parent.parent, existing)
    changed = incoming.get("contains_solutions") is True and existing.get("contains_solutions") is not True
    if changed:
        existing["contains_solutions"] = True
        versions._commit(path, before, state)
    return {"status": "identical", "material_id": existing["id"],
            "relative_path": existing["relative_path"], "state_written": changed}


@locked
def install_download(path, data, sync=True):
    """Only consume an explicitly authorized download already staged inside Study.

    ``sync=False`` lets the course batch stage many verified downloads and run a
    single catalog rebuild afterwards. ``data["extra"]`` carries whitelisted
    collector provenance; identity fields stay derived from the source.
    """
    path = Path(path).resolve()
    cleanup = versions.cleanup(path)
    state = json.loads(path.read_text(encoding="utf-8-sig"))
    source = data["source"]
    module = source.get("module_id")
    if not module:
        raise ValueError("Missing stable source module identity")
    filename = source["filename"]
    file_source = {"course_id": source["course_id"], "module_id": module,
                   "remote_filename": filename,
                   "filepath": source.get("filepath", "/"),
                   "url": source.get("url") or source.get("fileurl") or source.get("source_url"),
                   "activity_url": source.get("module_url") or source.get("activity_url"),
                   "verified_sha256": data["sha256"], "checked_at": versions.instant().isoformat()}
    for address in [file_source.get("url"), file_source.get("activity_url")]:
        if address and any(k.lower() in {"token", "wstoken", "sesskey"} for k in parse_qs(urlparse(address).query)):
            raise ValueError("Credential-bearing URL cannot enter state")
    row = {"id": f"{source['course_id']}-SOURCE-{module}-{data['sha256'][:16]}",
           "course_id": source["course_id"], "filename": filename,
           "title": source.get("title", filename), "category": source.get("category", "Other"),
           "kind": "Moodle course file", "relative_path": data["relative_path"],
           "sha256": data["sha256"], "bytes": data.get("bytes"), "page_map": [],
           "contains_solutions": source.get("contains_solutions")
               if isinstance(source.get("contains_solutions"), bool) else None,
           "moodle_file_sources": [file_source]}
    # A verified subdirectory separates same-name files from different modules.
    subdir = source.get("archive_subdir", data.get("archive_subdir"))
    subdir = versions.archive_subdir_name(subdir)
    if subdir:
        row["archive_subdir"] = subdir
    row.update(_download_extra(data.get("extra")))
    if is_solution(row):
        row["contains_solutions"] = True
    staged = versions.safe(path.parent.parent, row["relative_path"])
    if "_Inbox" not in staged.relative_to(path.parent.parent).parts:
        raise ValueError("Downloads must be staged in _Inbox before promotion")
    identical = next((m for m in state.get("materials", []) if versions.current(m)
                      and m.get("sha256") == row["sha256"] and versions.keys(m) & versions.keys(row)), None)
    if identical:
        result = _reuse_identical(path, row, identical["id"])
    else:
        if any(m["id"] == row["id"] for m in state.get("materials", [])):
            row["id"] += "-" + versions.uuid.uuid4().hex[:10]
        result = versions.promote(path, row, data.get("old_id"))
        if result["status"] == "identical":
            result = _reuse_identical(path, row, result["material_id"])
    result["state_written"] = bool(result.get("state_written") or cleanup.get("deleted"))
    if result["status"] in {"identical", "already_promoted"}:
        # Cached text still uses fresh registry labels in Library.fresh_rows;
        # identical installs stay byte-for-byte state-neutral.
        if "relative_path" not in result:
            final = json.loads(path.read_text(encoding="utf-8-sig"))
            result["relative_path"] = next(m["relative_path"] for m in final["materials"]
                                           if m["id"] == result["material_id"])
        return result
    if sync:
        Library(path).sync(row["course_id"])
    final = json.loads(path.read_text(encoding="utf-8-sig"))
    result["relative_path"] = next(m["relative_path"] for m in final["materials"] if m["id"] == result["material_id"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("configure"); p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("promote"); p.add_argument("--new", required=True); p.add_argument("--old")
    sub.add_parser("install-download")
    sub.add_parser("cleanup")
    sub.add_parser("recover")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "configure":
        result = configure(args.state, args.days)
    elif args.command == "install-download":
        result = install_download(args.state, json.load(sys.stdin))
    elif args.command == "cleanup":
        result = versions.cleanup(args.state, force_check=True)
    elif args.command == "recover":
        versions.recover(args.state); result = {"recovered": True}
    else:
        state = json.loads(args.state.read_text(encoding="utf-8-sig"))
        if args.command == "promote":
            row = next(m for m in state["materials"] if m["id"] == args.new)
            result = versions.promote(args.state, row, args.old)
            Library(args.state).sync(row["course_id"])
        else:
            result = {"policy": state.get("settings", {}).get("material_version_policy"),
                      "current": sum(versions.current(m) for m in state.get("materials", [])),
                      "archived": [{k: m.get(k) for k in ["id", "relative_path", "superseded_by", "archived_at", "version_status"]}
                                   for m in state.get("materials", []) if not versions.current(m)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
