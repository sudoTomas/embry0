from embry0.branding import API_TITLE, APP_NAME, GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME


def test_app_name():
    assert APP_NAME == "embry0"


def test_api_title():
    assert API_TITLE == "embry0 API"


def test_git_author():
    assert GIT_AUTHOR_NAME == "embry0"
    assert "@" in GIT_AUTHOR_EMAIL
    # Default must be a neutral bot identity, never a personal address.
    assert GIT_AUTHOR_EMAIL == "embry0-bot@users.noreply.github.com"
