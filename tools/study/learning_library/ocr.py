"""Local OCR sidecars for blank PDF/image units; never authoritative evidence.

Source text caches and learning state are read-only. Successful OCR records are
content-addressed and retained when a later render, batch or individual row fails.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

import library as storage

OCR_VERSION = "1.0.0"
SCHEMA = 1
ENGINE = "Windows.Media.Ocr"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SCRIPT = Path(__file__).with_name("ocr_windows.ps1")
_POWERSHELL = shutil.which("powershell.exe") or str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
_FLAGS = ["ocr_candidate_only", "needs_visual_review",
          "formula_and_handwriting_unverified", "reading_order_unverified",
          "blank_result_does_not_prove_blank_page"]


def _derived(base, *parts):
    base = Path(base).absolute()
    declared = base.joinpath(*parts)
    resolved = declared.resolve()
    if base.resolve() != base or resolved != declared or not resolved.is_relative_to(base):
        raise ValueError("OCR derived paths must not escape the library or use links/junctions")
    return declared


def _folder(lib):
    value = getattr(lib, "folder", None) or getattr(lib, "cache_dir", None)
    if value is None:
        raise ValueError("Library has no derived-data directory")
    return _derived(value)


def _unit(unit):
    value = unit.get("page") if isinstance(unit, dict) else unit
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("OCR unit must be a positive integer")
    return value


def _identity(row, unit, language):
    sha = row.get("sha256")
    if not isinstance(sha, str) or not _HASH.fullmatch(sha):
        raise ValueError("Missing valid source hash")
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language):
        raise ValueError("Invalid OCR language tag")
    script_sha = storage.digest(_SCRIPT)
    text = "\0".join([OCR_VERSION, script_sha, sha, str(_unit(unit)), language.casefold()])
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), script_sha


def _index(folder):
    path = _derived(folder, "ocr", "index.json")
    if not path.exists():
        return {"schema": SCHEMA, "records": {}}
    value = storage.read_json(path)
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or not isinstance(value.get("records"), dict):
        raise ValueError("Invalid OCR index")
    return value


def _source(lib, row):
    source = storage.safe_source(lib.study, row["relative_path"])
    if not source.is_file() or storage.digest(source) != row["sha256"]:
        raise ValueError("Source changed; rebuild its text index before OCR")
    return source


def _image(lib, row, path, source):
    declared = Path(path).absolute()
    if row.get("unit_kind") == "image":
        if declared.resolve() != source or declared.resolve() != declared:
            raise ValueError("OCR image must be the indexed original image")
    else:
        folder = _folder(lib)
        if not declared.is_relative_to(folder / "renders"):
            raise ValueError("PDF OCR image must be inside the library render directory")
        _derived(folder, declared.relative_to(folder))
    if not declared.is_file():
        raise ValueError("OCR source image is missing")
    return declared


def load_ocr(lib, row, unit, language="en-US"):
    """Return an intact, source-current, unverified candidate, otherwise None.

    Does not render, execute OCR, create directories, or change any state/cache.
    A checksum validates byte integrity, never the correctness of OCR text.
    """
    try:
        if row.get("unit_kind") not in {"pdf_page", "image"}:
            return None
        number = _unit(unit)
        key, script_sha = _identity(row, number, language)
        folder = _folder(lib)
        entry = _index(folder)["records"].get(key)
        if not isinstance(entry, dict):
            return None
        filename = entry.get("file", "")
        if not re.fullmatch(re.escape(key[:32]) + r"-[0-9a-f]{32}\.json", filename):
            return None
        path = _derived(folder, "ocr", "records", filename)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            return None
        record = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(record, dict) or any([
            record.get("schema") != SCHEMA, record.get("ocr_key") != key, record.get("ocr_version") != OCR_VERSION,
            record.get("engine") != ENGINE, record.get("engine_script_sha256") != script_sha,
            record.get("source_sha256") != row["sha256"], record.get("unit") != number,
            record.get("requested_language", "").casefold() != language.casefold(),
            record.get("verified") is not False, record.get("errors") != [],
            not isinstance(record.get("text"), str),
            not isinstance(record.get("lines"), list),
        ]):
            return None
        if not all(isinstance(line, str) for line in record["lines"]):
            return None
        source = _source(lib, row)
        # Identical original images can share an OCR key despite different paths.
        image = source if row.get("unit_kind") == "image" else _image(lib, row, record["source_image"], source)
        if storage.digest(image) != record.get("image_sha256"):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _error(exc):
    return {"type": type(exc).__name__, "message": str(exc)[:500]}


def _run_batch(manifest, language, count):
    timeout = max(120, min(1800, 60 + 15 * count))
    process = subprocess.run([
        _POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(_SCRIPT), "-ManifestPath", str(manifest), "-LanguageTag", language,
    ], capture_output=True, timeout=timeout)
    if process.returncode:
        stderr = process.stderr.decode("utf-8-sig", errors="replace") if isinstance(process.stderr, bytes) else process.stderr
        raise ValueError("Windows OCR batch failed: " + (stderr or "no error text")[-500:])
    output = process.stdout.decode("utf-8-sig") if isinstance(process.stdout, bytes) else process.stdout
    data = json.loads(output)
    if (not isinstance(data, dict) or data.get("engine") != ENGINE or
            data.get("verified") is not False or data.get("requested") != count or
            not isinstance(data.get("results"), list)):
        raise ValueError("Invalid Windows OCR batch output")
    return data["results"]


def _validated_result(lib, pending, result, language):
    row, number = pending["row"], pending["unit"]
    if not isinstance(result, dict) or result.get("id") != pending["key"]:
        raise ValueError("Missing or mismatched OCR result ID")
    if result.get("errors"):
        raise ValueError("OCR image failed: " + json.dumps(result["errors"], ensure_ascii=False)[:500])
    if result.get("engine") != ENGINE or result.get("verified") is not False or result.get("errors") != []:
        raise ValueError("Invalid OCR result provenance")
    if not isinstance(result.get("text"), str) or not isinstance(result.get("lines"), list) or not all(isinstance(x, str) for x in result["lines"]):
        raise ValueError("Invalid OCR text/lines")
    if not isinstance(result.get("language"), str) or result["language"].casefold() != language.casefold():
        raise ValueError("OCR language does not match request")
    source = _source(lib, row)
    image = _image(lib, row, pending["image"], source)
    if Path(result.get("source_image", "")).resolve() != image:
        raise ValueError("OCR output image path does not match rendered input")
    image_sha = storage.digest(image)
    if image_sha != pending["image_sha256"] or result.get("source_sha256") != image_sha:
        raise ValueError("OCR image changed or its reported checksum differs")
    if storage.digest(_SCRIPT) != pending["script_sha256"]:
        raise ValueError("OCR script changed during batch")
    return {
        "schema": SCHEMA, "ocr_key": pending["key"], "ocr_version": OCR_VERSION, "engine": ENGINE,
        "engine_script_sha256": pending["script_sha256"], "source_sha256": row["sha256"],
        "unit": number, "page": number, "unit_kind": row["unit_kind"],
        "source_image": str(image), "image_sha256": image_sha,
        "requested_language": language, "language": result["language"],
        "text": result["text"], "lines": result["lines"],
        "width": result.get("width"), "height": result.get("height"),
        "text_angle": result.get("text_angle"), "elapsed_ms": result.get("elapsed_ms"),
        "confidence": None, "verified": False, "errors": [],
        "flags": list(_FLAGS), "quality_flags": list(_FLAGS), "created_at": storage.now(),
    }


def build_ocr(lib, course=None, force=False, language="en-US"):
    """OCR only no-text PDF/image units in one locally locked batch."""
    start = time.perf_counter()
    folder = _folder(lib)
    _derived(folder, ".build.lock")
    ocr_dir = _derived(folder, "ocr")
    records_dir = _derived(folder, "ocr", "records")
    summary = {"schema": SCHEMA, "ocr_version": OCR_VERSION, "engine": ENGINE,
               "course": course, "language": language, "eligible_units": 0,
               "reused": 0, "queued": 0, "written": 0, "failed": 0,
               "blank_results": 0, "deduplicated": 0, "skipped_structural_units": 0,
               "failures": [], "unavailable": [], "records": [], "verified": False,
               "state_written": False, "original_cache_written": False,
               "source_written": False}
    with storage.build_lock(folder):
        records_dir.mkdir(parents=True, exist_ok=True)
        state, rows, issues = lib.fresh_rows(course)
        if course and course not in {c["id"] for c in state.get("courses", [])}:
            raise ValueError("Unknown course: " + course)
        summary.update(state_revision=state.get("revision"), unavailable=list(issues))
        try:
            index = _index(folder)
        except (OSError, ValueError, TypeError):
            index = {"schema": SCHEMA, "records": {}}
            summary["unavailable"].append({"reason": "ocr_index_invalid_rebuild_required"})
        pending = {}

        def failure(row, number, exc):
            summary["failed"] += 1
            summary["failures"].append({"id": row.get("id"), "unit": number,
                "error": _error(exc), "previous_success_preserved": load_ocr(lib, row, number, language) is not None})

        for row in rows:
            try:
                data = lib.data(row)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                summary["unavailable"].append({"id": row.get("id"), "reason": _error(exc)})
                continue
            if row.get("unit_kind") not in {"pdf_page", "image"}:
                summary["skipped_structural_units"] += sum(not p.get("text", "").strip() for p in data["pages"])
                continue
            for page in data["pages"]:
                if page.get("text", "").strip():
                    continue
                summary["eligible_units"] += 1
                number = page.get("page")
                try:
                    number = _unit(number)
                    key, script_sha = _identity(row, number, language)
                    if not force and load_ocr(lib, row, number, language) is not None:
                        summary["reused"] += 1
                        continue
                    if key in pending:
                        summary["deduplicated"] += 1
                        continue
                    rendered = lib.render(row["id"], number, dpi=130, include_solutions=True)
                    if rendered.get("source_sha256") != row["sha256"]:
                        raise ValueError("Rendered page has a different source hash")
                    source = _source(lib, row)
                    image = _image(lib, row, rendered["path"], source)
                    pending[key] = {"key": key, "row": row, "unit": number,
                                    "image": image, "image_sha256": storage.digest(image),
                                    "script_sha256": script_sha}
                except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
                    failure(row, number, exc)
        summary["queued"] = len(pending)
        manifest = None
        output = []
        batch_failed = False
        if pending:
            try:
                fd, name = tempfile.mkstemp(prefix=".ocr-manifest-", suffix=".json", dir=ocr_dir)
                os.close(fd)
                manifest = _derived(folder, Path(name).relative_to(folder))
                storage.atomic_json(manifest, [{"id": key, "path": str(item["image"]), "language": language}
                                               for key, item in pending.items()])
                output = _run_batch(manifest, language, len(pending))
            except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
                batch_failed = True
                for item in pending.values():
                    failure(item["row"], item["unit"], exc)
            finally:
                if manifest is not None and manifest.exists():
                    _derived(folder, manifest.relative_to(folder)).unlink()
            if output:
                counts = Counter(result.get("id") for result in output
                                 if isinstance(result, dict) and isinstance(result.get("id"), str))
                results = {result.get("id"): result for result in output
                           if isinstance(result, dict) and isinstance(result.get("id"), str)}
                for key, item in pending.items():
                    try:
                        if counts[key] != 1:
                            raise ValueError("Missing or duplicate OCR result")
                        record = _validated_result(lib, item, results[key], language)
                        content_sha = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                        # Short names avoid Windows MAX_PATH in nested study folders;
                        # full identity and integrity hashes remain in record/index.
                        filename = key[:32] + "-" + content_sha[:32] + ".json"
                        target = _derived(folder, "ocr", "records", filename)
                        storage.atomic_json(target, record)
                        entry = {"file": filename, "sha256": storage.digest(target),
                                 "source_sha256": item["row"]["sha256"], "unit": item["unit"],
                                 "language": language, "verified": False}
                        index["records"][key] = entry
                        summary["records"].append({"key": key, **entry})
                        summary["written"] += 1
                        summary["blank_results"] += not bool(record["text"].strip())
                    except (OSError, ValueError, TypeError, KeyError) as exc:
                        failure(item["row"], item["unit"], exc)
            elif not batch_failed:
                # An empty but well-formed batch cannot silently mark queued work complete.
                for item in pending.values():
                    failure(item["row"], item["unit"], ValueError("OCR batch returned no results"))
        if summary["written"]:
            index["updated_at"] = storage.now()
            storage.atomic_json(_derived(folder, "ocr", "index.json"), index)
        summary.update(elapsed_seconds=round(time.perf_counter() - start, 3), built_at=storage.now())
        storage.atomic_json(_derived(folder, "ocr", "last-build.json"), summary)
    return summary
