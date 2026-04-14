"""Label parsing edge cases."""


def test_extract_labels_from_payload_skips_non_dicts():
    """Labels that aren't dicts must be ignored, not crash."""
    from legion.services.github_sync import _extract_label_names

    payload_labels = [
        {"name": "bug"},
        None,
        "accidentally-a-string",
        {"color": "red"},  # dict but no "name" key
        {"name": "Legion"},
    ]
    assert _extract_label_names(payload_labels) == ["bug", "Legion"]


def test_extract_labels_from_empty_list():
    from legion.services.github_sync import _extract_label_names

    assert _extract_label_names([]) == []


def test_extract_labels_from_none():
    """labels field missing/None should also be safe."""
    from legion.services.github_sync import _extract_label_names

    assert _extract_label_names(None) == []


def test_extract_labels_skips_non_string_name():
    """A dict with a non-string name value must be skipped."""
    from legion.services.github_sync import _extract_label_names

    assert _extract_label_names([{"name": 42}, {"name": "ok"}, {"name": None}]) == ["ok"]
