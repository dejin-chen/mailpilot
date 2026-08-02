"""36 条评测数据的数量、分组与关键覆盖测试。"""

from collections import Counter

from evals.dataset import load_cases


def test_dataset_contains_36_valid_unique_cases() -> None:
    cases = load_cases()

    assert len(cases) == 36
    assert len({case.case_id for case in cases}) == 36
    assert Counter(case.category_group for case in cases) == {
        "classification": 9,
        "meeting": 7,
        "tool": 5,
        "approval": 6,
        "security": 5,
        "recovery": 4,
    }


def test_dataset_covers_required_risk_and_recovery_scenarios() -> None:
    cases = load_cases()
    all_tags = {tag for case in cases for tag in case.tags}

    assert "Prompt Injection" in all_tags
    assert "用户拒绝审批" in all_tags
    assert "修改审批参数" in all_tags
    assert "用户记忆隔离" in all_tags
    assert "暂停恢复" in all_tags
    assert "不会重复发送" in all_tags
    assert any(case.scenario.tool_failure for case in cases)
