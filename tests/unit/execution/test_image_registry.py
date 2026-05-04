from athanor.execution.image_registry import qualify_image


def test_empty_registry_passthrough():
    assert qualify_image("athanor-sandbox:latest", "") == "athanor-sandbox:latest"


def test_empty_image_passthrough():
    assert qualify_image("", "registry:5000") == ""


def test_prefixes_unqualified_image():
    assert qualify_image("athanor-proxy:latest", "registry:5000") == "registry:5000/athanor-proxy:latest"


def test_prefixes_unqualified_image_with_path_separator():
    # "library/postgres" has a slash but the first segment is not a host.
    assert qualify_image("library/postgres", "registry:5000") == "registry:5000/library/postgres"


def test_does_not_double_qualify_with_dot():
    assert qualify_image("ghcr.io/foo/bar:1.0", "registry:5000") == "ghcr.io/foo/bar:1.0"


def test_does_not_double_qualify_with_port():
    assert qualify_image("registry:5000/athanor-proxy:latest", "registry:5000") == "registry:5000/athanor-proxy:latest"


def test_does_not_double_qualify_localhost():
    assert qualify_image("localhost/foo:bar", "registry:5000") == "localhost/foo:bar"


def test_strips_trailing_slash_from_registry():
    assert qualify_image("athanor-sandbox:latest", "registry:5000/") == "registry:5000/athanor-sandbox:latest"


def test_handles_image_without_tag():
    assert qualify_image("athanor-proxy", "registry:5000") == "registry:5000/athanor-proxy"
