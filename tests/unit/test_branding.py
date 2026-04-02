from legion.branding import APP_NAME, API_TITLE, GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL


def test_app_name():
    assert APP_NAME == "Legion"


def test_api_title():
    assert API_TITLE == "Legion API"


def test_git_author():
    assert GIT_AUTHOR_NAME == "Legion"
    assert "@" in GIT_AUTHOR_EMAIL
