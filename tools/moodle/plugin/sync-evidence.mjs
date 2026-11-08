// Evidence that the first school sync actually finished for the current
// account, course selection and course-level material authorization.
//
// A directory that merely exists proves nothing: setup completion must be
// derived from the real collection snapshot and material-library report, with
// the account and authorization they were produced under.
import { existsSync, readFileSync } from 'node:fs'
import { isAbsolute, join, relative, resolve } from 'node:path'

import { SCOPE_VERSION } from '../course-library.mjs'
import { normalizeSchoolUrl, readState } from './settings.mjs'

const COLLECTION_CANDIDATES = ['refresh-latest.json', 'refresh-last-complete.json']
const REPORT_RELATIVE = 'Study/_System/LibrarySync/latest.json'

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''))
  } catch {
    return null
  }
}

function parseTime(value) {
  const at = Date.parse(String(value ?? ''))
  return Number.isFinite(at) ? at : null
}

function siteKey(value) {
  try {
    return normalizeSchoolUrl(value)
  } catch {
    return null
  }
}

function accountOfScope(scope) {
  if (!scope || typeof scope !== 'object') return null
  const site = siteKey(scope.site)
  const userId = Number(scope.user_id)
  if (!site || !Number.isInteger(userId) || userId <= 0) return null
  return { site, user_id: userId }
}

function sameAccount(first, second) {
  if (!first || !second) return false
  return first.site === second.site && Number(first.user_id) === Number(second.user_id)
}

function describeAccount(account) {
  return account ? { site: account.site, user_id: account.user_id } : null
}

export function loadLatestSnapshot(dataDir) {
  const folder = join(dataDir, 'Study', '_System')
  for (const name of COLLECTION_CANDIDATES) {
    const path = join(folder, name)
    const snapshot = readJson(path)
    if (!snapshot || !Array.isArray(snapshot.pages)) continue
    return {
      path,
      snapshot,
      account: accountOfScope(snapshot.account_scope),
      collectionComplete: snapshot.collection_complete === true,
      finishedAt: snapshot.finished_at ?? snapshot.started_at ?? null,
      finishedMs: parseTime(snapshot.finished_at ?? snapshot.started_at),
      courses: [...new Set(snapshot.pages.map((page) => page.course_id).filter(Boolean))],
      moodleCourseIds: new Set(snapshot.pages.map((page) => Number(page.courseid)).filter(Number.isInteger)),
    }
  }
  return null
}

export function loadLibraryReport(dataDir) {
  const path = join(dataDir, REPORT_RELATIVE)
  const report = readJson(path)
  if (!report || typeof report !== 'object') return null
  let snapshotAccount = null
  let snapshotComplete = null
  const snapshotPath = report.text_source?.snapshot?.path
  if (typeof snapshotPath === 'string' && snapshotPath) {
    const absolute = resolve(dataDir, snapshotPath)
    const rel = relative(dataDir, absolute)
    if (rel && !rel.startsWith('..') && !isAbsolute(rel)) {
      const snapshot = readJson(absolute)
      snapshotAccount = accountOfScope(snapshot?.account_scope)
      snapshotComplete = snapshot?.collection_complete === true
    }
  }
  const totals = report.totals ?? {}
  const errors = Array.isArray(report.errors) ? report.errors : []
  const complete = (totals.failed ?? 0) === 0 && (totals.blocked_courses ?? 0) === 0 && errors.length === 0
  return {
    path,
    report,
    account: snapshotAccount,
    snapshotComplete,
    finishedAt: report.finished_at ?? null,
    finishedMs: parseTime(report.finished_at),
    totals,
    errors,
    complete,
    courses: Array.isArray(report.courses) ? report.courses : [],
    index: report.index?.courses ?? {},
    authorization: report.authorization ?? null,
  }
}

export function selectedCoursesFromState(state) {
  const courses = Array.isArray(state?.courses) ? state.courses : []
  const grants = state?.settings?.course_library_authorization?.courses
  const grantMap = grants && typeof grants === 'object' && !Array.isArray(grants) ? grants : {}
  return courses
    .filter((course) => (course.moodle_course_ids ?? []).length > 0)
    .map((course) => {
      const grant = grantMap[course.id] ?? null
      return {
        id: course.id,
        name: course.name ?? course.shortname ?? course.id,
        moodleCourseIds: (course.moodle_course_ids ?? []).map(Number),
        grant,
        mode: grant?.mode ?? null,
        authorizedAt: grant?.authorized_at ?? null,
        authorizedMs: parseTime(grant?.authorized_at),
        account: accountOfScope(grant?.account_scope),
      }
    })
}

function indexCounts(report, courseId) {
  const entry = report?.index?.[courseId]
  if (entry && typeof entry === 'object') {
    return {
      materials: Number(entry.materials ?? 0),
      units: Number(entry.units ?? 0),
      textUnits: Number(entry.text_units ?? 0),
      statusCounts: entry.status_counts ?? null,
    }
  }
  return null
}

function courseIssue(code, hint) {
  return { code, hint }
}

// Evaluate one sync result for the current selection and authorization.
// Returns { ready, mode, courses, blockers, summary, snapshot, report } where
// every course carries its own status so partial work can be described
// precisely instead of being reported as success or failure as a whole.
export function evaluateSyncEvidence({ dataDir, state, connectedAccount = null, profileAccount = null } = {}) {
  const snapshot = loadLatestSnapshot(dataDir)
  const report = loadLibraryReport(dataDir)
  const courses = selectedCoursesFromState(state)
  const savedProfileAccount = accountOfScope(profileAccount) ?? accountOfScope(state?.settings?.profile_account_scope)
  const result = {
    ready: false,
    mode: null,
    courses: [],
    blockers: [],
    snapshot: snapshot
      ? {
          path: snapshot.path,
          finishedAt: snapshot.finishedAt,
          collectionComplete: snapshot.collectionComplete,
          account: describeAccount(snapshot.account),
          courses: snapshot.courses,
        }
      : null,
    report: report
      ? {
          path: report.path,
          finishedAt: report.finishedAt,
          complete: report.complete,
          totals: report.totals,
          errors: report.errors.length,
          account: describeAccount(report.account),
        }
      : null,
    checkedAt: new Date().toISOString(),
    summary: {
      authorizedCourses: courses.length,
      readyCourses: 0,
      partialCourses: 0,
      missingCourses: 0,
      downloadedMaterials: 0,
      textUnits: 0,
    },
  }

  if (!courses.length) {
    result.blockers.push(courseIssue('courses_not_selected', '还没有选择课程与资料范围。'))
    return result
  }

  let mixed = false
  const selectionModes = new Set()
  for (const course of courses) {
    selectionModes.add(course.mode)
    if (!course.grant) {
      result.courses.push({
        ...course,
        status: 'authorization_missing',
        issues: [courseIssue('authorization_missing', '这门课程还没有保存资料范围授权，请重新列出课程并确认。')],
        available: null,
      })
      continue
    }
    if (!['full', 'info_only'].includes(course.mode)) {
      result.courses.push({
        ...course,
        status: 'authorization_invalid',
        issues: [courseIssue('authorization_invalid', '课程资料授权格式异常，请重新确认课程与资料范围。')],
        available: null,
      })
      continue
    }
    if ((course.grant.scope_version ?? SCOPE_VERSION) !== SCOPE_VERSION) {
      result.courses.push({
        ...course,
        status: 'stale',
        issues: [courseIssue('authorization_scope_changed', '课程资料授权范围版本已变化，请重新确认资料范围。')],
        available: null,
      })
      continue
    }
    if (savedProfileAccount && course.account && !sameAccount(savedProfileAccount, course.account)) {
      result.courses.push({
        ...course,
        status: 'account_mismatch',
        issues: [courseIssue('account_mismatch', '课程授权来自另一个学校账号；请恢复原账号或使用独立资料目录。')],
        available: null,
      })
      continue
    }
    if (connectedAccount && course.account && !sameAccount(connectedAccount, course.account)) {
      result.courses.push({
        ...course,
        status: 'account_mismatch',
        issues: [courseIssue('account_switched', '当前登录的学校账号与学习档案不一致；已停止使用旧授权。')],
        available: null,
      })
      continue
    }

    if (course.mode === 'info_only') {
      const evidence = evaluateInfoOnly(course, snapshot)
      result.courses.push({ ...course, ...evidence })
      continue
    }

    const evidence = evaluateFull(course, report)
    result.courses.push({ ...course, ...evidence })
  }

  if (selectionModes.size > 1) mixed = true
  if (selectionModes.size === 1) result.mode = [...selectionModes][0]
  else if (selectionModes.size > 1) result.mode = 'mixed'

  for (const course of result.courses) {
    if (course.status === 'ready') {
      result.summary.readyCourses += 1
      result.summary.downloadedMaterials += course.available?.materials ?? 0
      result.summary.textUnits += course.available?.textUnits ?? 0
    } else if (['partial', 'stale', 'failed', 'account_mismatch'].includes(course.status)) {
      result.summary.partialCourses += 1
      for (const issue of course.issues ?? []) result.blockers.push(courseIssue(issue.code, `${course.name}：${issue.hint}`))
    } else {
      result.summary.missingCourses += 1
      for (const issue of course.issues ?? []) result.blockers.push(courseIssue(issue.code, `${course.name}：${issue.hint}`))
    }
  }

  // Global report problems (API errors while enumerating sources) mean the
  // report cannot be treated as complete even when every course row looks fine.
  if (report && !report.complete && result.courses.some((course) => course.mode === 'full')) {
    result.blockers.push(courseIssue('sync_report_partial', '上次资料同步存在错误或未授权课程，需要重跑同步补齐后再完成设置。'))
  }
  if (!report && result.courses.some((course) => course.mode === 'full')) {
    result.blockers.push(courseIssue('sync_not_started', '还没有完成“完整资料同步”。'))
  }
  if (!snapshot) {
    result.blockers.push(courseIssue('collection_not_started', '还没有完成学校信息采集。'))
  }

  result.ready = result.courses.length > 0 && result.courses.every((course) => course.status === 'ready') && result.blockers.length === 0
  return result
}

function evaluateInfoOnly(course, snapshot) {
  if (!snapshot) {
    return { status: 'missing', issues: [courseIssue('sync_not_started', '还没有完成学校信息采集。')], available: null }
  }
  if (!snapshot.collectionComplete) {
    return { status: 'partial', issues: [courseIssue('collection_incomplete', '学校信息采集不完整，需要重新同步。')], available: null }
  }
  if (snapshot.account && course.account && !sameAccount(snapshot.account, course.account)) {
    return { status: 'account_mismatch', issues: [courseIssue('account_mismatch', '采集快照属于另一个学校账号。')], available: null }
  }
  if (course.authorizedMs && snapshot.finishedMs && snapshot.finishedMs < course.authorizedMs) {
    return { status: 'stale', issues: [courseIssue('snapshot_stale', '最近一次学校信息采集早于本次课程授权，需要重新同步。')], available: null }
  }
  const covered = course.moodleCourseIds.every((id) => snapshot.moodleCourseIds.has(id))
  if (!covered) {
    return { status: 'missing', issues: [courseIssue('snapshot_missing_course', '最近一次学校信息采集没有覆盖这门课程。')], available: null }
  }
  return {
    status: 'ready',
    issues: [],
    available: { materials: 0, units: 0, textUnits: 0, snapshotAt: snapshot.finishedAt, mode: 'info_only' },
  }
}

function evaluateFull(course, report) {
  if (!report) {
    return { status: 'missing', issues: [courseIssue('sync_not_started', '还没有完成“完整资料同步”。')], available: null }
  }
  if ((report.authorization?.mode ?? 'full') !== 'full') {
    return { status: 'stale', issues: [courseIssue('authorization_changed', '资料同步报告的授权范围不是完整同步。')], available: null }
  }
  if (course.authorizedMs && report.finishedMs && report.finishedMs < course.authorizedMs) {
    return { status: 'stale', issues: [courseIssue('sync_stale', '资料同步早于本次课程授权，需要重新同步。')], available: null }
  }
  if (report.account && course.account && !sameAccount(report.account, course.account)) {
    return { status: 'account_mismatch', issues: [courseIssue('account_mismatch', '资料同步报告属于另一个学校账号。')], available: null }
  }
  const row = report.courses.find((entry) => entry.course_id === course.id)
  if (!row) {
    return { status: 'missing', issues: [courseIssue('sync_missing_course', '资料同步报告没有覆盖这门课程。')], available: null }
  }
  const gaps = Array.isArray(row.gaps) ? row.gaps : []
  const blocked = gaps.some((gap) => gap.status === 'blocked')
  const failedItems = Number(row.failed ?? 0)
  const failures = gaps.filter((gap) => ['failed', 'blocked'].includes(gap.status))
  const counts =
    indexCounts(report, course.id) ?? {
      materials: Number(row.installed ?? 0) + Number(row.identical ?? 0) + Number(row.duplicate ?? 0) + Number(row.already_current ?? 0),
      units: Number(row.captures ?? 0),
      textUnits: Number(row.captures ?? 0),
      statusCounts: null,
    }
  const available = { ...counts, mode: 'full', modulesVisible: Number(row.modules_visible ?? 0) }
  if (blocked) {
    return {
      status: 'failed',
      issues: [
        courseIssue('course_blocked', '这门课程的资料同步未完成或未授权；请重新确认资料范围后重跑同步。'),
      ],
      available,
      failures: failures.slice(0, 5),
    }
  }
  if (failedItems > 0 || report.errors.length) {
    return {
      status: 'partial',
      issues: [
        courseIssue(
          'sync_errors',
          failedItems > 0
            ? `上次资料同步有 ${failedItems} 份内容失败；重跑同步会增量补齐，不会重复下载已成功内容。`
            : `上次资料同步记录了 ${report.errors.length} 个来源错误，需要重跑同步补齐。`,
        ),
      ],
      available,
      failures: failures.slice(0, 5),
    }
  }
  return { status: 'ready', issues: [], available }
}

export function syncSummaryText(evidence) {
  if (!evidence) return '尚未检查同步结果。'
  const { summary } = evidence
  if (evidence.ready) {
    return (
      `同步证据完整：${summary.authorizedCourses} 门课程、${summary.downloadedMaterials} 份资料、` +
      `${summary.textUnits} 个文本单元可用（授权模式 ${evidence.mode ?? '未知'}）。`
    )
  }
  const parts = [`已核对授权课程 ${summary.authorizedCourses} 门`]
  if (summary.readyCourses) parts.push(`${summary.readyCourses} 门已完成`)
  if (summary.partialCourses) parts.push(`${summary.partialCourses} 门部分/过期`)
  if (summary.missingCourses) parts.push(`${summary.missingCourses} 门未同步`)
  const blockers = (evidence.blockers ?? []).slice(0, 4).map((item) => item.hint)
  return `${parts.join('，')}。${blockers.length ? ' ' + blockers.join(' ') : ''}`
}

export { sameAccount, accountOfScope }
