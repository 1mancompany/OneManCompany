"""Unit tests for agent stall detection."""

import pytest

from onemancompany.core.vessel import detect_unfulfilled_promises


class TestDetectUnfulfilledPromises:
    """Detect when an agent says it will do something but didn't use tools."""

    def test_chinese_future_action(self):
        assert detect_unfulfilled_promises("我将正式开始执行前端重构任务") is True

    def test_chinese_next_step(self):
        assert detect_unfulfilled_promises("接下来我会优化数据库查询") is True

    def test_chinese_about_to(self):
        assert detect_unfulfilled_promises("下一步，我将部署新版本") is True

    def test_chinese_now_starting(self):
        assert detect_unfulfilled_promises("现在开始处理这个需求") is True

    def test_english_i_will(self):
        assert detect_unfulfilled_promises("I will now start implementing the feature") is True

    def test_english_let_me(self):
        assert detect_unfulfilled_promises("Let me start working on the refactoring") is True

    def test_english_next(self):
        assert detect_unfulfilled_promises("Next, I'll dispatch a task to the engineer") is True

    def test_completed_report_no_stall(self):
        """Agent reporting what it DID is not a stall."""
        assert detect_unfulfilled_promises("已完成OKR更新，所有指标已同步") is False

    def test_short_ack_no_stall(self):
        assert detect_unfulfilled_promises("收到，已更新") is False

    def test_empty_string(self):
        assert detect_unfulfilled_promises("") is False

    def test_none_returns_false(self):
        assert detect_unfulfilled_promises(None) is False

    def test_past_tense_no_stall(self):
        assert detect_unfulfilled_promises("I have completed the task and updated the OKR") is False

    def test_mixed_completed_and_promise(self):
        """If output has BOTH completed work AND future promises, it's a stall."""
        text = "已完成OKR更新。接下来我将开始执行前端重构任务。"
        assert detect_unfulfilled_promises(text) is True

    def test_asking_question_no_stall(self):
        """Agent asking a question should not be flagged."""
        assert detect_unfulfilled_promises("需要确认一下，这个功能要支持哪些浏览器？") is False
