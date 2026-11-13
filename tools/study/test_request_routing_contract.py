"""Contract checks for request-type routing and exam-information timing.

The teaching behaviour is produced by the model from these rules, so ordinary unit
tests cannot assert an answer.  These checks keep the rule changes from silently
disappearing: the current request must decide the task, exam information is
background unless the request needs it, and chapter explanations keep their own
organisation instead of an exam-scope preamble.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class RequestRoutingContractTests(unittest.TestCase):
    def test_workflow_distinguishes_request_types(self):
        text = read("docs/WORKFLOW.md")
        self.assertIn("## 当前请求决定任务", text)
        for label in ("讲解某章", "复习已学", "考前复习", "查询考试范围", "学习计划"):
            self.assertIn(label, text, label)
        self.assertIn("不默认插入考试日期", text)
        self.assertIn("Not required for the exam", text)
        self.assertIn("不调用 `continue`", text)
        self.assertIn("chapter-check", text)

    def test_skill_has_chapter_explanation_organisation(self):
        text = read("skills/self-study-tutor/SKILL.md")
        self.assertIn("## 章节讲解：内容组织与深度", text)
        self.assertIn("章节讲解回复组织模板", text)
        self.assertIn("应避免的回复", text)
        self.assertIn("## 考试信息的使用时机", text)
        self.assertIn("不默认插入考试日期", text)
        self.assertIn("Not required for the exam", text)
        self.assertIn("不把“完整理解一章”缩减为“只讲会考的部分”", text)
        # The anti-pattern is listed so the model does not copy it.
        self.assertIn("先明确考试范围", text)
        # The skill must stay on-demand rather than becoming a persistent mode.
        self.assertIn("按需调用", text)
        self.assertIn("自动退出", text)

    def test_tutoring_rules_and_detection_reference(self):
        tutoring = read("docs/TUTORING.md")
        self.assertIn("## 任务类型与考试信息的适用时机", tutoring)
        self.assertIn("## 章节讲解", tutoring)
        self.assertIn("考试表现是后续目标之一", tutoring)
        self.assertIn("不因项目或记录中存有考试信息就把讲解章节当作考前复习", tutoring)
        reference = read("skills/self-study-tutor/references/assessment-and-review.md")
        self.assertIn("使用门槛", reference)
        self.assertIn("普通章节讲解不必加载本文件", reference)

    def test_agent_rules_and_readme_agree_on_collection_trigger(self):
        self.assertIn("当前请求决定任务", read("AGENTS.md"))
        self.assertIn("讲解章节按本章理解主线组织，不按考纲筛选", read("AGENTS.md"))
        self.assertIn("The current request decides the task", read("SKILL.md"))
        zh = read("README.zh-CN.md")
        en = read("README.md")
        self.assertIn("只在当前请求需要校务信息", zh)
        self.assertIn("Moodle is collected only when the current request needs school information", en)
        self.assertNotIn("每个聊天首次进入课程学习、复习或学习计划时采集", zh)
        self.assertNotIn("Moodle is collected when a chat first enters course learning", en)
        self.assertNotIn("Refresh Moodle on the first course-learning", read("SKILL.md"))

    def test_exam_reference_is_gated_for_ordinary_teaching(self):
        exam = read("docs/EXAM_PREP.md")
        self.assertIn("**使用门槛**", exam)
        self.assertIn("普通章节讲解、复习已学内容或学习计划不读本文件", exam)
        session = read("docs/SESSION.md")
        self.assertIn("普通章节讲解且本地已有该章资料时不用它", session)
        self.assertIn("request_policy", read("docs/LIBRARY.md"))

    def test_teaching_flow_keeps_practice_and_feynman_defaults(self):
        skill = read("skills/self-study-tutor/SKILL.md")
        self.assertIn("### 意图与教学阶段判断", skill)
        self.assertIn("不自动跳过练习", skill)
        self.assertIn("只影响当前题", skill)
        self.assertIn("整章先讲完", skill)
        self.assertIn("推进前检查偏离", skill)
        self.assertIn("默认请用户在不看资料的情况下向零基础学生讲解", skill)
        self.assertIn("不要求每个小知识点、每轮对话都做", skill)
        self.assertIn("已展示答案", skill)
        agents = read("AGENTS.md")
        self.assertNotIn("“继续/跳过”直接推进", agents)
        self.assertIn("不自动跳过练习", agents)
        self.assertIn("只影响当前题", agents)
        tutoring = read("docs/TUTORING.md")
        self.assertIn("按教学阶段推进", tutoring)
        self.assertIn("已展示答案", tutoring)
        self.assertIn("跳过或未答", tutoring)
        self.assertNotIn("“继续／跳过”可直接推进", tutoring)
        context = read("tools/study/learning_library/context.py")
        self.assertIn("不自动跳过练习", context)
        self.assertIn("脱稿向零基础学生解释", context)
        self.assertNotIn("继续/跳过可直接推进", context)
        self.assertNotIn("Continue or skip requests advance", read("SKILL.md"))
        self.assertIn("continuing does not automatically skip practice", read("SKILL.md"))

    def test_unified_progress_reader_and_verified_save_contract(self):
        agents = read("AGENTS.md")
        self.assertIn("teaching-save", agents)
        self.assertIn("teaching_progress", agents)
        skill = read("skills/self-study-tutor/SKILL.md")
        self.assertIn("teaching-save", skill)
        self.assertIn("teaching_progress", skill)
        memory = read("docs/MEMORY.md")
        self.assertIn("## 教学接续保存与漏记检查", memory)
        self.assertIn("teaching-save", memory)
        for phrase in ("occurred_at", "expected_revision", "readback_verified", "answer_shown", "source"):
            self.assertIn(phrase, memory)
        session = read("docs/SESSION.md")
        self.assertIn("teaching-save", session)
        tools = read("docs/TOOLS.md")
        self.assertIn("## 保存教学进度（teaching-save）", tools)
        context = read("tools/study/learning_library/context.py")
        self.assertIn("def teaching_progress", context)
        progress = read("tools/study/teaching_progress.py")
        self.assertIn("def save_teaching", progress)
        self.assertIn("readback_verified", progress)
        self.assertIn("append-only", progress)

    def test_onboarding_tips_do_not_force_first_collection(self):
        entry = read("tools/onboarding/entry.py")
        self.assertIn("只教或复习某一章且本地已有资料时，先读取本地记录并只核对本章文件", entry)
        self.assertNotIn("新聊天首次进入学习时先读取记录并检查一次 Moodle", entry)


if __name__ == "__main__":
    unittest.main()
