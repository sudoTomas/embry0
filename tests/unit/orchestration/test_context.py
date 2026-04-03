from legion.orchestration.context import merge_context


def test_merge_context_all_sources():
    result = merge_context(
        global_context="Global rules.",
        repo_context="Repo rules.",
        additional_context="Job-specific rules.",
    )
    assert "Global rules." in result
    assert "Repo rules." in result
    assert "Job-specific rules." in result


def test_merge_context_empty():
    result = merge_context()
    assert result == ""


def test_merge_context_partial():
    result = merge_context(global_context="Only global.")
    assert "Only global." in result
    assert result.strip() == "Only global."
