# SMS-AI

Персональный SMS-шлюз к GigaChat через Plusofon.

Пользователь отправляет SMS на номер Plusofon. Plusofon вызывает webhook
FastAPI-приложения, приложение получает ответ GigaChat и отправляет его
пользователю через Plusofon API.

Постоянная база данных, Redis и автоматические рассылки не используются.

## Требования

- Python 3.12 или новее;
- номер Plusofon с поддержкой SMS;
- API-токен Plusofon;
- credentials GigaChat API для физического лица.

## Локальная установка в Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Заполните секреты и номера в `.env`. Файл `.env` нельзя добавлять в Git.

## Обязательные переменные окружения

```dotenv
APP_ENV=development
LOG_LEVEL=INFO
TIMEZONE=Asia/Yakutsk

PLUSOFON_TOKEN=
PLUSOFON_CLIENT_ID=10553
PLUSOFON_NUMBER_ID=
PLUSOFON_NUMBER=
PLUSOFON_WEBHOOK_TOKEN=
PLUSOFON_API_BASE_URL=https://restapi.plusofon.ru/api/v1

GIGACHAT_CREDENTIALS=
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-3-Ultra
GIGACHAT_API_BASE_URL=https://api.giga.chat/v1
GIGACHAT_OAUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_CA_BUNDLE=

ALLOWED_PHONE_NUMBERS=79991234567
```

Номера записываются в международном формате без символа `+`.

Несколько разрешённых номеров разделяются запятыми без пробелов:

```dotenv
ALLOWED_PHONE_NUMBERS=79991234567,79997654321
```

`GIGACHAT_CA_BUNDLE` обычно оставляется пустым. Если среда не доверяет
сертификату GigaChat, укажите путь к PEM-файлу доверенного CA bundle.

## Дополнительные настройки

```dotenv
MASTER_PIN=
AUTH_PIN=
DAILY_WARNING_THRESHOLD=5
MAX_CONTEXT_MESSAGES=8
DEFAULT_LATIN_MODE=off
SMS_PRICE_RUB=2.00
HTTP_TIMEOUT_SECONDS=15
```

Команды, использующие PIN, будут подключены на следующих этапах. PIN-коды
должны отличаться друг от друга.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Тесты используют mock-серверы и не обращаются к реальным API. Они не расходуют
токены GigaChat и не отправляют SMS.

## Проверка импорта

```powershell
.\.venv\Scripts\python.exe -c "from app.main import app; print(app.title, app.version)"
```

Ожидаемый результат:

```text
SMS-AI 0.1.0
```

## Локальный запуск

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

Проверка из второго окна PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

Ожидаемые ответы:

```text
status
------
ok

status
------
ready
```

Локальный адрес недоступен Plusofon из интернета. Не указывайте его как
webhook в личном кабинете.

## HTTP endpoints

- `GET /health` — процесс приложения работает;
- `GET /ready` — конфигурация и внутренние клиенты созданы;
- `POST /webhooks/plusofon/incoming/<token>` — входящие SMS Plusofon.

Проверки `/health` и `/ready` не обращаются к Plusofon или GigaChat.

## Timeweb Cloud App Platform

Команда сборки:

```bash
pip install --upgrade -r requirements.txt
```

Команда запуска:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log
```

Путь проверки состояния:

```text
/ready
```

Все значения из `.env` задаются в панели Timeweb как переменные окружения.
Сам файл `.env` в репозиторий не загружается.

Для GigaChat в настройках приложения могут понадобиться доверенные российские
корневые сертификаты. Production-деплой выполняется вручную только после
успешных локальных тестов.

## Безопасность

- не публикуйте `.env`;
- не передавайте API-токены в URL или query-параметрах;
- используйте длинный случайный `PLUSOFON_WEBHOOK_TOKEN`;
- не включайте стандартный access-log: webhook-токен является частью URL;
- разрешайте только личные номера через `ALLOWED_PHONE_NUMBERS`;
- не запускайте несколько экземпляров приложения: порядок SMS хранится в RAM.