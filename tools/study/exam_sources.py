"""Read-only Moodle exam evidence intake; changed evidence requires human review.

This module never decides that a student's post is an official exam requirement.
The assistant reviews candidates and registers structured profiles with locators.
"""
from copy import deepcopy
import hashlib
import html
import json
import re
from urllib.parse import urlsplit, urlunsplit

import review
import study_time


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _site_url(snapshot):
    """Use the collector's configured site, or URLs actually present in evidence."""
    possibilities = [snapshot.get("moodle_url")]
    for page in snapshot.get("pages") or []:
        for section in page.get("sections") or []:
            possibilities.extend(module.get("url") for module in section.get("modules") or [])
    possibilities.extend(thread.get("forum_url") for thread in snapshot.get("threads") or [])
    for value in possibilities:
        if not isinstance(value, str):
            continue
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            continue
        path = parsed.path
        for marker in ("/mod/", "/course/"):
            path = path.split(marker, 1)[0]
        return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))
    return None


def evidence_index(snapshot, state=None):
    records = {}
    site = _site_url(snapshot)
    course_map = {str(identifier): course["id"] for course in (state or {}).get("courses", [])
                  for identifier in course.get("moodle_course_ids", [])}
    def identity(entry):
        value = entry.get("course_id")
        return value if value is not None else course_map.get(str(entry.get("courseid")), str(entry.get("courseid")))
    def link(path):
        return site + path if site else None
    for page in snapshot.get("pages") or []:
        course = identity(page)
        for section in page.get("sections") or []:
            locator = f"section:{page.get('courseid', page.get('course_id'))}:{section['id']}"
            records[locator] = {"course_id": course, "location": locator,
                "url": link(f"/course/section.php?id={section['id']}"),
                "text": clean(section.get("name", "") + " " + section.get("summary", "")),
                "authority": "official_course"}
            for module in section.get("modules") or []:
                locator = f"module:{module['id']}"
                records[locator] = {"course_id": course, "location": locator,
                    "url": module.get("url") or link(f"/course/section.php?id={section['id']}"),
                    "text": clean(module.get("name", "") + " " + module.get("description", "")),
                    "content_metadata": module.get("contentsinfo"),
                    "authority": "official_course"}
    for thread in snapshot.get("threads") or []:
        discussion = (thread.get("discussion") or {}).get("discussion")
        for post in thread.get("posts") or []:
            locator = f"post:{post['id']}"
            records[locator] = {"course_id": identity(thread), "location": locator,
                "url": link(f"/mod/forum/discuss.php?d={discussion}#p{post['id']}") if discussion is not None else None,
                "author": post.get("author", {}).get("fullname"),
                "author_id": post.get("author", {}).get("id"),
                "text": clean(post.get("subject", "") + " " + post.get("message", "")),
                "authority": "unreviewed_forum_author"}
    for record in records.values():
        record["content_hash"] = digest({k: v for k, v in record.items() if k != "url"})
    return records


def candidates(snapshot, state=None):
    pattern = re.compile(r"exam|mid.?term|test\s*[12]|multiple.choice|short.answer|long.answer|true.false|assessment|question|考试|题型|选择题|简答题|判断题|填空题", re.I)
    return [x for x in evidence_index(snapshot, state).values() if pattern.search(x["text"])]


def candidate_signature(snapshot, course_id, state=None):
    return digest(sorted((x["location"], x["content_hash"]) for x in candidates(snapshot, state)
                         if x["course_id"] == course_id))


@study_time.state_timezone
def sync_profiles(state, snapshot, now):
    """A collection is not semantic review. Never silently accept edited requirements."""
    at = now.astimezone(study_time.local_zone()).isoformat()
    complete = snapshot.get("collection_complete") is True
    index = evidence_index(snapshot, state) if complete else {}
    changed = []
    for profile in state.get("exam_profiles", []):
        problems = []
        if not complete:
            problems.append("本轮 Moodle 采集未完整，保留上次已核读要求")
        else:
            if profile.get("reviewed_candidates_hash") != candidate_signature(snapshot, profile["course_id"], state):
                problems.append("新增或修订的考试相关说明需核读，不能直接沿用旧题型")
            for source in profile.get("sources", []):
                location = source.get("location", "")
                if location.startswith(("post:", "module:", "section:")):
                    current = index.get(location)
                    if current is None or current["content_hash"] != source.get("content_hash"):
                        problems.append(location + " 内容变化或本轮不可见，需重新核读")
                elif location.startswith("attachment:"):
                    module = index.get("module:" + str(source.get("module_id")))
                    if module is None or module["content_hash"] != source.get("module_content_hash"):
                        problems.append(location + " 附件入口变化或不可见，需重新内存核读考试说明")
        profile["latest_source_check"] = {"at": at, "collection_at": snapshot.get("finished_at"),
            "collection_complete": complete, "issues": problems}
        if problems:
            profile["verification_status"] = "needs_review"
            profile["pending_source_issues"] = list(dict.fromkeys(profile.get("pending_source_issues", []) + problems))
        if profile.get("verification_status") == "needs_review":
            changed.append(profile["id"])
        # An unchanged later collection cannot clear a review previously required.
    return {"profiles_needing_review": changed, "collection_complete": complete}


@study_time.state_timezone
def register_profile(state, payload, snapshot, now):
    """Register a structured, assistant-reviewed reading of official evidence."""
    if snapshot.get("collection_complete") is not True:
        raise ValueError("Moodle 采集未完整，不能登记为本轮已核读考试要求")
    if not isinstance(payload, dict):
        raise ValueError("考试要求必须为结构化记录")
    profile = deepcopy(payload)
    for key in ("id", "course_id", "title"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError("考试要求缺少 " + key)
    if profile["course_id"] not in {x["id"] for x in state.get("courses", [])}:
        raise ValueError("未知课程")
    for key in ("scope_status", "format_status"):
        if profile.get(key) not in {"confirmed", "partial", "unknown"}:
            raise ValueError("考试要求状态无效")
    reviewed = profile.get("review_evidence")
    if (not isinstance(profile.get("sources"), list) or not profile["sources"] or
            not isinstance(reviewed, (str, dict)) or not reviewed or
            (isinstance(reviewed, str) and not reviewed.strip())):
        raise ValueError("需保留实际核读记录和官方来源")
    index = evidence_index(snapshot, state)
    materials = {m["id"]: m for m in state.get("materials", [])}
    material_ids = {key for key, value in materials.items() if value.get("course_id") == profile["course_id"]}
    for source in profile["sources"]:
        if not isinstance(source, dict):
            raise ValueError("官方来源必须为结构化定位")
        if source.get("authority") not in {"teacher", "official_course"}:
            raise ValueError("学生推测不能登记为官方考试要求")
        location = source.get("location", "")
        if not isinstance(location, str):
            raise ValueError("来源定位必须为文字")
        if location.startswith(("post:", "module:", "section:")):
            record = index.get(location)
            if not record or record["course_id"] != profile["course_id"]:
                raise ValueError("来源不在本轮课程采集中")
            if not isinstance(source.get("excerpt"), str) or not clean(source["excerpt"]) or clean(source["excerpt"]) not in record["text"]:
                raise ValueError("考试要求摘录与本轮原文不匹配")
            role = source.get("role_evidence")
            role_valid = isinstance(role, (str, dict)) and bool(role) and (not isinstance(role, str) or bool(role.strip()))
            if location.startswith("post:") and (source.get("authority") != "teacher" or
                    source.get("author_id") != record.get("author_id") or not role_valid):
                raise ValueError("论坛要求需核对作者及教师/助教角色证据")
            source.update(content_hash=record["content_hash"], url=record["url"])
        elif location.startswith("attachment:"):
            # The caller has actually read only assessment metadata in memory;
            # no teaching file is silently downloaded or written by this module.
            module = index.get("module:" + str(source.get("module_id")))
            pages = source.get("pages")
            checksum = source.get("sha256")
            if (not module or module["course_id"] != profile["course_id"] or
                    location != "attachment:" + str(source.get("module_id")) or
                    not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum) or
                    source.get("verification_method") != "online_in_memory_assessment_preview" or
                    source.get("course_file_saved") is not False or
                    not isinstance(pages, list) or not pages or
                    not all(isinstance(p, int) and not isinstance(p, bool) and p > 0 for p in pages) or
                    not isinstance(source.get("excerpt"), str) or not source["excerpt"].strip() or
                    not isinstance(source.get("preview_evidence"), str) or not source["preview_evidence"].strip()):
                raise ValueError("在线考试附件需实际内存核读证据、原页、哈希及对应课程入口")
            source.update(url=module["url"], content_hash=checksum, module_content_hash=module["content_hash"])
        elif location.startswith("material:"):
            pages = source.get("pages")
            material = materials.get(source.get("material_id"), {})
            if (source.get("material_id") not in material_ids or location != "material:" + source.get("material_id", "") or
                    not isinstance(pages, list) or not pages or
                    not all(isinstance(page, int) and not isinstance(page, bool) and page > 0
                            and (not isinstance(material.get("pages"), int) or page <= material["pages"]) for page in pages) or
                    not isinstance(source.get("excerpt"), str) or not source["excerpt"].strip()):
                raise ValueError("课件考试说明需有本地资料及页码")
        else:
            raise ValueError("来源必须关联本轮 Moodle 原文或本地官方课件")
        source["checked_at"] = now.astimezone(study_time.local_zone()).isoformat()
    topics = profile.get("topics", [])
    if not isinstance(topics, list) or not all(isinstance(t, dict) and isinstance(t.get("id"), str) and t["id"].strip() for t in topics):
        raise ValueError("考点需有有效编号")
    if len({t["id"] for t in topics}) != len(topics):
        raise ValueError("考点编号重复")
    for topic in topics:
        ids = topic.get("material_ids", [])
        if (not isinstance(topic.get("label"), str) or not topic["label"].strip() or not isinstance(ids, list)
                or not all(isinstance(m, str) and m in material_ids for m in ids)):
            raise ValueError("考点或资料映射无效")
    profile["verification_status"] = "verified"
    profile.pop("pending_source_issues", None)
    profile["reviewed_candidates_hash"] = candidate_signature(snapshot, profile["course_id"], state)
    profile["verified_at"] = now.astimezone(study_time.local_zone()).isoformat()
    profiles = deepcopy(state.get("exam_profiles", []))
    existing = next((p for p in profiles if p["id"] == profile["id"]), None)
    if existing:
        profile["previous_requirements"] = existing.get("previous_requirements", [])
        from exam_prep import profile_signature
        if profile_signature(existing) != profile_signature(profile):
            profile["previous_requirements"] = profile["previous_requirements"] + [
                {k: v for k, v in existing.items() if k != "previous_requirements"}]
        profiles[profiles.index(existing)] = profile
    else:
        profiles.append(profile)
    state["exam_profiles"] = profiles
    return {"exam_id": profile["id"], "verified": True}


def render_intake(snapshot):
    lines = ["# 待核读的 Moodle 考试说明", "", "论坛作者角色需核对；搜索命中本身不是官方要求。", ""]
    for entry in candidates(snapshot):
        lines += [f"## {entry['course_id']} · {entry['location']}", "",
                  entry.get("author") or "课程页面", entry["text"], ""]
    return "\n".join(lines)
