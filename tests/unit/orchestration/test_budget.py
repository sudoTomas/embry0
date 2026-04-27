from athanor.orchestration.budget import check_budget


def test_within_budget():
    result = check_budget(current_cost=5.0, max_budget=10.0, overrun_mode="soft")
    assert result.allowed is True
    assert result.remaining == 5.0


def test_over_budget_soft_stop():
    result = check_budget(current_cost=12.0, max_budget=10.0, overrun_mode="soft")
    assert result.allowed is True
    assert result.overrun == 2.0


def test_over_budget_hard_stop():
    result = check_budget(current_cost=12.0, max_budget=10.0, overrun_mode="hard")
    assert result.allowed is False
    assert result.overrun == 2.0


def test_zero_budget():
    result = check_budget(current_cost=0.0, max_budget=10.0, overrun_mode="soft")
    assert result.allowed is True
    assert result.remaining == 10.0
