from athanor.branding import API_TITLE, APP_NAME, GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME


def test_app_name():
    assert APP_NAME == "Athanor"


def test_api_title():
    assert API_TITLE == "Athanor API"


def test_git_author():
    assert GIT_AUTHOR_NAME == "Athanor"
    assert "@" in GIT_AUTHOR_EMAIL
