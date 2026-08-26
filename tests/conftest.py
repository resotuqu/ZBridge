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


for name, value in _TEST_ENV.items():
    # setdefault, not unconditional assignment: an operator who
    # has already exported real Plusofon credentials in the shell
    # (to run the opt-in, real read-only history check gated by
    # PLUSOFON_E2E_READONLY) must keep them -- the fake test
    # values are only a fallback for the normal case where those
    # variables aren't set at all.
    os.environ.setdefault(name, value)