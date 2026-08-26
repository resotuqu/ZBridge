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
    os.environ[name] = value