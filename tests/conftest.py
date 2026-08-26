import os


_TEST_ENV = {
    "APP_ENV": "test",
    "PLUSOFON_TOKEN": "test-plusofon-token",
    "PLUSOFON_CLIENT_ID": "10553",
    "PLUSOFON_NUMBER_ID": "1",
    "PLUSOFON_NUMBER": "70000000000",
    "PLUSOFON_WEBHOOK_TOKEN": "test-webhook-token",
    "GIGACHAT_CREDENTIALS": (
        "test-gigachat-credentials"
    ),
    "ALLOWED_PHONE_NUMBERS": "71111111111",
    "AUTH_PIN": "9999",
    "MASTER_PIN": "8241",
}


if os.environ.get("PLUSOFON_E2E_READONLY") == "1":
    # Only for the separate opt-in E2E check
    # (tests/test_plusofon_history_e2e.py): don't clobber the
    # operator's real credentials, already exported in the shell
    # before invoking pytest with this flag set.
    for name, value in _TEST_ENV.items():
        os.environ.setdefault(name, value)
else:
    # Normal pytest and CI: always use deterministic test-*
    # values, regardless of whatever happens to already be set
    # in the environment.
    os.environ.update(_TEST_ENV)
