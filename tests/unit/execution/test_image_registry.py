import pytest

from embry0.execution.image_registry import (
    ImagePinError,
    clear_image_pins,
    install_image_pins,
    is_registry_qualified,
    is_valid_digest,
    load_image_pins,
    parse_image_pins,
    pinned_digest,
    pushed_digest,
    qualify_image,
    strip_digest,
)


def test_empty_registry_passthrough():
    assert qualify_image("embry0-sandbox:latest", "") == "embry0-sandbox:latest"


def test_empty_image_passthrough():
    assert qualify_image("", "registry:5000") == ""


def test_prefixes_unqualified_image():
    assert qualify_image("embry0-proxy:latest", "registry:5000") == "registry:5000/embry0-proxy:latest"


def test_prefixes_unqualified_image_with_path_separator():
    # "library/postgres" has a slash but the first segment is not a host.
    assert qualify_image("library/postgres", "registry:5000") == "registry:5000/library/postgres"


def test_does_not_double_qualify_with_dot():
    assert qualify_image("ghcr.io/foo/bar:1.0", "registry:5000") == "ghcr.io/foo/bar:1.0"


def test_does_not_double_qualify_with_port():
    assert qualify_image("registry:5000/embry0-proxy:latest", "registry:5000") == "registry:5000/embry0-proxy:latest"


def test_does_not_double_qualify_localhost():
    assert qualify_image("localhost/foo:bar", "registry:5000") == "localhost/foo:bar"


def test_strips_trailing_slash_from_registry():
    assert qualify_image("embry0-sandbox:latest", "registry:5000/") == "registry:5000/embry0-sandbox:latest"


def test_handles_image_without_tag():
    assert qualify_image("embry0-proxy", "registry:5000") == "registry:5000/embry0-proxy"


# --- is_registry_qualified: what can be pulled (#334 review) ----------------


@pytest.mark.parametrize(
    "image",
    ["registry:5000/embry0-sandbox-qa:latest", "ghcr.io/org/img:1", "localhost/img", "localhost:5000/img:tag"],
)
def test_registry_qualified_names(image):
    assert is_registry_qualified(image) is True


@pytest.mark.parametrize("image", ["embry0-sandbox-qa:latest", "embry0-qa-org_repo:2026-09-20T04-59", "org/img:1", ""])
def test_unqualified_names(image):
    assert is_registry_qualified(image) is False


def test_qualify_image_uses_the_same_rule():
    assert qualify_image("embry0-sandbox-qa:latest", "registry:5000") == "registry:5000/embry0-sandbox-qa:latest"
    assert qualify_image("registry:5000/img:1", "other:5000") == "registry:5000/img:1"


# --- digest pins (#523) ------------------------------------------------------

PROXY_PIN = "sha256:" + "a" * 64
QA_PIN = "sha256:" + "b" * 64


@pytest.fixture
def pins():
    install_image_pins("registry:5000", {"embry0-proxy:latest": PROXY_PIN, "embry0-sandbox-qa:latest": QA_PIN})
    yield
    clear_image_pins()


def test_a_pinned_image_is_named_by_its_digest(pins):
    """The reference a launch uses must resolve by digest, so a re-push of the
    tag cannot change what runs."""
    assert qualify_image("embry0-proxy:latest", "registry:5000") == f"registry:5000/embry0-proxy:latest@{PROXY_PIN}", (
        "a pinned image must be launched as <registry>/<name>:<tag>@<digest>"
    )


def test_a_pinned_image_already_qualified_with_the_registry_gets_its_digest(pins):
    assert (
        qualify_image("registry:5000/embry0-sandbox-qa:latest", "registry:5000")
        == f"registry:5000/embry0-sandbox-qa:latest@{QA_PIN}"
    ), "a profile that stores the qualified name must not bypass the pin"


def test_a_missing_tag_means_latest_for_the_pin(pins):
    assert qualify_image("embry0-proxy", "registry:5000") == f"registry:5000/embry0-proxy@{PROXY_PIN}"


def test_an_unpinned_image_keeps_its_tag(pins):
    assert qualify_image("embry0-sandbox:latest", "registry:5000") == "registry:5000/embry0-sandbox:latest"
    assert qualify_image("embry0-proxy:v2", "registry:5000") == "registry:5000/embry0-proxy:v2"


def test_pins_apply_only_to_the_registry_they_were_recorded_in(pins):
    assert qualify_image("embry0-proxy:latest", "other:5000") == "other:5000/embry0-proxy:latest"
    assert qualify_image("ghcr.io/acme/embry0-proxy:latest", "registry:5000") == "ghcr.io/acme/embry0-proxy:latest"


def test_a_name_that_already_carries_a_digest_is_left_alone(pins):
    """A pre-built QA image is recorded with its own digest."""
    ref = "embry0-qa-acme_widgets:2026-10-08T20-00@" + "sha256:" + "c" * 64
    assert qualify_image(ref, "registry:5000") == f"registry:5000/{ref}"
    assert qualify_image(f"registry:5000/{ref}", "registry:5000") == f"registry:5000/{ref}"


def test_qualifying_twice_does_not_stack_digests(pins):
    once = qualify_image("embry0-proxy:latest", "registry:5000")
    assert qualify_image(once, "registry:5000") == once


def test_a_trailing_slash_on_the_registry_still_matches_the_pins(pins):
    assert qualify_image("embry0-proxy:latest", "registry:5000/") == f"registry:5000/embry0-proxy:latest@{PROXY_PIN}"


def test_without_pins_qualification_is_unchanged():
    assert qualify_image("embry0-proxy:latest", "registry:5000") == "registry:5000/embry0-proxy:latest"
    assert pinned_digest("embry0-proxy:latest") is None


def test_pinned_digest_reads_qualified_and_unqualified_names(pins):
    assert pinned_digest("embry0-proxy:latest") == PROXY_PIN
    assert pinned_digest("registry:5000/embry0-proxy:latest") == PROXY_PIN
    assert pinned_digest("registry:5000/embry0-proxy") == PROXY_PIN
    assert pinned_digest(f"registry:5000/embry0-proxy:latest@{PROXY_PIN}") is None
    assert pinned_digest("other:5000/embry0-proxy:latest") is None


def test_parse_pins_reads_the_bootstrap_format():
    text = f"# Written by init-push-images\n\nembry0-proxy:latest {PROXY_PIN}\nembry0-sandbox-qa:latest   {QA_PIN}\n"
    assert parse_image_pins(text) == {"embry0-proxy:latest": PROXY_PIN, "embry0-sandbox-qa:latest": QA_PIN}


@pytest.mark.parametrize(
    "line",
    [
        "embry0-proxy:latest",  # no digest
        f"embry0-proxy:latest {PROXY_PIN} extra",
        "embry0-proxy:latest sha256:abc",  # short digest
        f"embry0-proxy:latest md5:{'a' * 64}",
        f"embry0-proxy {PROXY_PIN}",  # no tag
        f"registry:5000/embry0-proxy:latest {PROXY_PIN}",  # qualified
    ],
)
def test_parse_pins_refuses_a_line_it_cannot_trust(line):
    with pytest.raises(ImagePinError, match="line 1"):
        parse_image_pins(line)


def test_parse_pins_refuses_one_image_pinned_to_two_digests():
    with pytest.raises(ImagePinError, match="pinned twice"):
        parse_image_pins(f"embry0-proxy:latest {PROXY_PIN}\nembry0-proxy:latest {QA_PIN}\n")


def test_load_pins_refuses_a_missing_or_empty_file(tmp_path):
    with pytest.raises(ImagePinError, match="cannot read"):
        load_image_pins(tmp_path / "absent")
    empty = tmp_path / "pins"
    empty.write_text("# nothing pushed\n")
    with pytest.raises(ImagePinError, match="pins no images"):
        load_image_pins(empty)


def test_install_refuses_pins_without_a_registry_or_with_a_bad_digest():
    with pytest.raises(ImagePinError):
        install_image_pins("", {"embry0-proxy:latest": PROXY_PIN})
    with pytest.raises(ImagePinError):
        install_image_pins("registry:5000", {"embry0-proxy:latest": "latest"})


def test_pushed_digest_reads_what_docker_push_reported():
    """Captured from `docker push` on a containerd-store daemon: the INFO block
    names the index the daemon holds, which was not pushed; the digest line
    names what was."""
    out = (
        "The push refers to repository [127.0.0.1:5001/embry0-proxy]\n"
        "25f1d6b1951a: Layer already exists\n"
        f"latest: digest: {QA_PIN} size: 1023\n"
        "\n"
        " Info -> Not all multiplatform-content is present and only the available single-platform image was pushed\n"
        f"         {PROXY_PIN} -> {QA_PIN}\n"
    )
    assert pushed_digest(out) == QA_PIN
    assert pushed_digest("") is None
    assert pushed_digest("Pushed\n") is None


def test_strip_digest():
    assert strip_digest(f"registry:5000/embry0-proxy:latest@{PROXY_PIN}") == "registry:5000/embry0-proxy:latest"
    assert strip_digest("registry:5000/embry0-proxy:latest") == "registry:5000/embry0-proxy:latest"


def test_a_digest_ending_in_a_newline_is_not_a_digest():
    """Python's ``$`` also matches just before a final newline, so ``match`` took ``sha256:…\\n``."""
    digest = "sha256:" + "a" * 64
    assert is_valid_digest(digest), "a sha256 digest is valid"
    assert not is_valid_digest(digest + "\n"), "a digest ending in a newline must not be valid"
    try:
        install_image_pins("registry:5000", {"embry0-proxy:latest": digest + "\n"})
    except ImagePinError:
        pass
    else:
        clear_image_pins()
        raise AssertionError("a pin whose digest ends in a newline must be refused")
