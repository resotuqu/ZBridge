# Архитектура SMS-AI сервера
## GigaChat + Plusofon + Timeweb Cloud App Platform

**Статус:** финальная архитектура для разработки  
**Версия:** 2.0  
**Дата:** 21 августа 2026  
**Принцип:** без внешней и локальной постоянной БД

---

# 1. Цель

Создать персональный SMS-интерфейс к ИИ и интернет-сервисам, которым можно пользоваться с обычного телефона без мобильного интернета.

Основной поток:

```text
Телефон
  |
  | SMS
  v
Plusofon
  |
  | HTTPS webhook
  v
Timeweb Cloud App Platform
  |
  +--> Router команд
  |      |
  |      +--> GigaChat
  |      +--> Новости
  |      +--> Погода
  |      +--> Валюты
  |      +--> Справочные источники
  |      +--> Калькулятор
  |
  +--> SMS formatter
  |
  | Plusofon API
  v
Plusofon
  |
  | SMS
  v
Телефон
```

Сервис рассчитан прежде всего на небольшую группу из 2–3 постоянных пользователей.

Это не система массовых SMS-рассылок.

---

# 2. Главные архитектурные решения

1. Backend — Python + FastAPI.
2. Размещение — Timeweb Cloud App Platform.
3. Деплой первой версии — ручной.
4. SMS transport — Plusofon API.
5. Основной LLM — GigaChat API.
6. Постоянная БД отсутствует.
7. История SMS и SMS-статистика читаются из Plusofon API.
8. Временные настройки живут только в RAM приложения.
9. После перезапуска временные настройки сбрасываются.
10. Постоянные настройки и секреты задаются через environment variables.
11. 2–3 постоянных номера задаются через `ALLOWED_PHONE_NUMBERS`.
12. Новый номер можно временно авторизовать через `AUTH_PIN`.
13. Административные настройки защищены `MASTER_PIN`.
14. `latin on/off` хранится отдельно для каждого номера.
15. Выбранная модель хранится отдельно для каждого номера.
16. По умолчанию ответ максимально короткий.
17. Длинный ответ продолжается командой `+`.
18. Порог 5 SMS в день — только предупреждение.
19. Начиная с 6-го исходящего SMS-сегмента добавляется `[!]`.
20. После `[!]` ответы не блокируются.
21. Автоматических исходящих SMS в первой версии нет.
22. Новости получают актуальные данные из RSS/поиска, а не из памяти модели.
23. Погода, валюты и другие текущие данные получают специализированные API.
24. При окончательном отказе GigaChat пользователь получает `ИИ поломался :(`.

---

# 3. Что принципиально НЕ используется

В production не используются:

- PostgreSQL;
- MySQL;
- SQLite как постоянное хранилище;
- Redis;
- SQLAlchemy;
- Alembic;
- Celery;
- Kafka;
- внешняя очередь;
- отдельный database service.

Приложение проектируется максимально stateless.

Допускаются только:

- RAM текущего процесса;
- история Plusofon;
- данные внешних API;
- environment variables;
- runtime logs платформы.

---

# 4. Технологический стек

## Backend

- Python 3.12+
- FastAPI
- Uvicorn
- httpx
- Pydantic
- pydantic-settings
- tenacity
- python-dateutil / zoneinfo

## Тестирование

- pytest
- pytest-asyncio
- respx

## Внешние сервисы

- Plusofon API v1
- GigaChat REST API
- RSS/Atom feeds
- SearchProvider для интернет-поиска новостей
- WeatherProvider
- CurrencyProvider
- EncyclopediaProvider

---

# 5. Развёртывание в Timeweb Cloud Apps

## 5.1. Build

```bash
pip install --upgrade -r requirements.txt
```

## 5.2. Start

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Приложение обязательно слушает `0.0.0.0`.

## 5.3. Деплой

Первая версия разворачивается вручную.

Процесс:

```text
локальная разработка
-> тесты
-> git commit / push
-> ручной deploy/redeploy в Timeweb Apps
-> smoke test
-> реальная тестовая SMS
```

Автоматический redeploy по push в первой версии не нужен.

## 5.4. HTTPS endpoints

```text
GET  /health
GET  /ready

POST /webhooks/plusofon/incoming/<WEBHOOK_TOKEN>
```

Позже при необходимости:

```text
POST /webhooks/plusofon/dlr/<WEBHOOK_TOKEN>
```

---

# 6. Структура проекта

```text
sms-ai/
├── app/
│   ├── __init__.py
│   ├── main.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py
│   │   └── plusofon_webhook.py
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── logging.py
│   │   ├── security.py
│   │   └── runtime_state.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── plusofon.py
│   │   ├── gigachat.py
│   │   ├── message_router.py
│   │   ├── sms_formatter.py
│   │   ├── conversation.py
│   │   ├── usage.py
│   │   ├── news.py
│   │   ├── weather.py
│   │   ├── currency.py
│   │   ├── encyclopedia.py
│   │   └── retry.py
│   │
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── ai.py
│   │   ├── help.py
│   │   ├── continue_.py
│   │   ├── stats.py
│   │   ├── clear.py
│   │   ├── news.py
│   │   ├── weather.py
│   │   ├── calc.py
│   │   ├── translate.py
│   │   ├── wiki.py
│   │   ├── currency.py
│   │   ├── models.py
│   │   ├── model.py
│   │   ├── latin.py
│   │   └── auth.py
│   │
│   └── schemas/
│       ├── __init__.py
│       ├── plusofon.py
│       └── messages.py
│
├── tests/
│   ├── test_auth.py
│   ├── test_router.py
│   ├── test_sms_formatter.py
│   ├── test_usage.py
│   ├── test_plusofon.py
│   ├── test_gigachat.py
│   └── test_webhook.py
│
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── architecture.md
```

В проекте НЕТ:

```text
db/
repositories/
migrations/
alembic.ini
```

---

# 7. Environment variables

Пример `.env.example`:

```dotenv
APP_ENV=production
LOG_LEVEL=INFO

TIMEZONE=Asia/Yakutsk

PLUSOFON_TOKEN=
PLUSOFON_CLIENT_ID=10553
PLUSOFON_NUMBER_ID=
PLUSOFON_NUMBER=
PLUSOFON_WEBHOOK_TOKEN=

GIGACHAT_CREDENTIALS=
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-3-Ultra

ALLOWED_PHONE_NUMBERS=79XXXXXXXXX,79YYYYYYYYY
MASTER_PIN=
AUTH_PIN=

DAILY_WARNING_THRESHOLD=5
MAX_CONTEXT_MESSAGES=8
DEFAULT_LATIN_MODE=off

SMS_PRICE_RUB=2.00

HTTP_TIMEOUT_SECONDS=15
```

Дополнительно по мере подключения сервисов:

```dotenv
WEATHER_API_KEY=
SEARCH_API_KEY=
CURRENCY_API_KEY=
```

Все тарифы, имена моделей и пороги должны быть конфигурируемыми.

---

# 8. Runtime state

Постоянного хранилища нет.

В RAM допускается хранить:

```python
temporary_authorized_numbers: set[str]

latin_mode_by_phone: dict[str, bool]

selected_model_by_phone: dict[str, str]

clear_context_after_by_phone: dict[str, datetime]

gigachat_requests_total: int

gigachat_requests_by_model: dict[str, int]

oauth_token_cache: OAuthToken | None

recent_webhook_keys: TTLCache

usage_cache: TTLCache

per_phone_locks: dict[str, asyncio.Lock]
```

После перезапуска Timeweb App:

- временно авторизованные номера исчезают;
- `latin` возвращается к default;
- модель возвращается к `GIGACHAT_MODEL`;
- `clear`-границы исчезают;
- AI-счётчики начинаются с нуля;
- OAuth token получается заново;
- TTL-кэши очищаются.

SMS-история при этом не теряется, потому что она находится у Plusofon.

---

# 9. Авторизация номеров

## 9.1. Постоянный whitelist

В env:

```dotenv
ALLOWED_PHONE_NUMBERS=79XXXXXXXXX,79YYYYYYYYY,79ZZZZZZZZZ
```

Это основные 2–3 номера.

Номера нормализуются до единого формата.

Например:

```text
+7 999 123-45-67
89991234567
79991234567
```

преобразуются к:

```text
79991234567
```

## 9.2. Неизвестный номер

Неизвестный номер может выполнить только:

```text
<AUTH_PIN> auth
```

Например:

```text
3957 auth
```

При успехе:

```text
Доступ разрешён до перезапуска.
```

Номер добавляется в `temporary_authorized_numbers`.

При неправильном PIN рекомендуется молча игнорировать SMS.

## 9.3. AUTH_PIN

`AUTH_PIN`:

- предназначен только для допуска нового номера;
- не разрешает административные настройки;
- не выводится в ответах;
- не попадает в логи;
- хранится только в env.

## 9.4. MASTER_PIN

`MASTER_PIN` применяется к административным изменениям.

Например:

```text
8241 model GigaChat-2-Pro
```

`MASTER_PIN`:

- хранится только в env;
- не логируется;
- не выводится;
- должен отличаться от `AUTH_PIN`.

---

# 10. Plusofon: входящие SMS

Endpoint:

```http
POST /webhooks/plusofon/incoming/<WEBHOOK_TOKEN>
```

Логически входящее событие содержит:

```json
{
  "src_number": "79991234567",
  "dst_number": "79997654321",
  "content": "что такое vlan",
  "date": "2026-08-21 22:00:00"
}
```

Фактическая схема должна соответствовать текущей документации Plusofon.

Алгоритм:

```text
1. Получить webhook.
2. Проверить WEBHOOK_TOKEN.
3. Провалидировать payload.
4. Нормализовать номера.
5. Проверить SMS-loop.
6. Проверить временный anti-duplicate TTL cache.
7. Определить авторизацию номера.
8. Если номер неизвестен:
   - разрешить только AUTH_PIN auth;
   - всё остальное игнорировать.
9. Быстро вернуть HTTP 200.
10. Сериализовать обработку по номеру.
11. Выполнить команду/AI-запрос.
12. Получить актуальную SMS-статистику через Plusofon.
13. Сформировать SMS.
14. Добавить [!] при необходимости.
15. Отправить через Plusofon.
```

---

# 11. Защита от повторных webhook без БД

Строгой вечной идемпотентности без постоянного хранилища нет.

Для персонального сервиса используется TTL-кэш в RAM.

Ключ:

```text
SHA256(
    src_number
    + dst_number
    + normalized_date
    + exact_content
)
```

Рекомендуемый TTL:

```text
10 минут
```

Повтор в пределах TTL:

```text
HTTP 200
без повторного GigaChat
без повторной SMS
```

После полного перезапуска приложения TTL-кэш исчезает.

Этот риск принимается сознательно ради полного отказа от БД.

---

# 12. SMS-loop protection

До любой AI-обработки:

```text
src_number == PLUSOFON_NUMBER -> reject

dst_number != PLUSOFON_NUMBER -> reject
```

Если номер не постоянный и не временно авторизованный:

```text
разрешить только AUTH_PIN auth
```

Сервис никогда не отвечает сам себе.

---

# 13. Plusofon: отправка SMS

Базовый API:

```text
https://restapi.plusofon.ru
```

Endpoint:

```http
POST /api/v1/sms
```

Логическая структура:

```json
{
  "text": "текст",
  "number_id": 123,
  "to": 79991234567,
  "reject_long": true,
  "count_pdu": true
}
```

Точные поля сверяются с текущей документацией перед production.

## 13.1. Почему приложение само контролирует длину

Желательно использовать:

```text
reject_long=true
```

и разбивать SMS самостоятельно.

Плюсы:

- заранее известно число сегментов;
- корректно считается расход;
- `[!]` добавляется предсказуемо;
- можно использовать служебные префиксы;
- сервер не отправит неожиданно длинное сообщение.

---

# 14. История SMS через Plusofon API

Источником истины является Plusofon.

Для истории используется:

```http
GET /api/v1/sms
```

Для диалога с конкретным номером, если endpoint доступен в текущей версии API:

```http
GET /api/v1/sms/dialog/{number}
```

Сервис должен изолировать эти запросы внутри `PlusofonClient`.

Если конкретный dialog endpoint изменится, меняется только клиент, а не логика приложения.

---

# 15. Подсчёт SMS без БД

Для команды `stat` и предупреждения `[!]` сервер получает исходящие сообщения за нужный период через Plusofon API.

Алгоритм дня:

```text
1. Определить начало текущего дня в TIMEZONE.
2. Определить конец текущего дня.
3. Запросить исходящие SMS к текущему номеру.
4. Пройти все страницы ответа.
5. Суммировать pdu.
6. Получить sent_today.
```

Псевдокод:

```python
sent_today = sum(
    sms.pdu
    for sms in outgoing_sms_today
)
```

Если `pdu` отсутствует в конкретном ответе API, клиент должен использовать документированный эквивалент или корректный расчёт сегментов.

Нельзя молча считать каждую запись одной SMS.

---

# 16. `[!]` — мягкое предупреждение

Env:

```text
DAILY_WARNING_THRESHOLD=5
```

Это не лимит.

Логика:

```text
sent_today < 5:
    обычный ответ

sent_today >= 5:
    ответ начинается с [!]
```

То есть:

- первые 5 физических исходящих SMS-сегментов за локальный день — без предупреждения;
- 6-й и последующие — с `[!]`.

Пример:

```text
[!] DHCP автоматически выдаёт IP, маску, шлюз и DNS.
```

Если ответ физически разбит на несколько частей:

```text
[!] [1/3] ...
[2/3] ...
[3/3] ...
```

`[!]` ставится только в первый сегмент ответа.

Ответы после порога никогда не блокируются.

---

# 17. GigaChat

## 17.1. Авторизация

Для физлица:

```text
scope=GIGACHAT_API_PERS
```

OAuth token:

- получается по credentials;
- кэшируется в RAM;
- переиспользуется до истечения;
- обновляется автоматически.

Не получать новый token перед каждым вопросом.

## 17.2. Chat API

Основной логический endpoint:

```text
POST /v1/chat/completions
```

Точный base URL сверяется с официальной документацией.

## 17.3. Модель по умолчанию

```dotenv
GIGACHAT_MODEL=GigaChat-3-Ultra
```

Имя модели не должно быть зашито в код.

## 17.4. Персональная модель

Для каждого номера:

```python
effective_model = (
    selected_model_by_phone.get(phone)
    or settings.gigachat_model
)
```

Команда `model` меняет модель только до перезапуска.

---

# 18. `models`

Команда:

```text
models
```

Сервис по возможности получает список моделей через официальный GigaChat API.

Пример ответа:

```text
GigaChat-2
GigaChat-2-Pro
GigaChat-2-Max
GigaChat-3-Ultra
Текущая: GigaChat-3-Ultra
```

Правила:

- не хранить доступные модели как единственный жёсткий список;
- использовать API, когда endpoint доступен;
- если API моделей временно недоступен — вернуть короткую ошибку;
- не придумывать лимиты.

Если GigaChat предоставляет текущему аккаунту endpoint баланса/лимитов:

```text
models
```

может дополнительно показать доступный остаток.

Если endpoint не разрешён тарифом/аккаунтом — выводится только список моделей.

---

# 19. `model [название]`

Административная команда.

Формат:

```text
<MASTER_PIN> model <название>
```

Пример:

```text
8241 model GigaChat-2-Pro
```

Ответ:

```text
Модель: GigaChat-2-Pro
```

Перед переключением:

1. получить/проверить доступные модели;
2. убедиться, что имя существует;
3. сохранить выбор в RAM для данного номера.

После рестарта:

```text
GIGACHAT_MODEL
```

снова становится моделью по умолчанию.

---

# 20. Системная инструкция GigaChat

Базовый смысл:

```text
Ты отвечаешь пользователю через обычные SMS.

Правила:
- отвечай прямо;
- не повторяй вопрос;
- не используй Markdown и таблицы;
- пиши информационно плотно;
- по умолчанию давай короткий ответ;
- учитывай, что пользователь может запросить продолжение символом "+";
- не выдумывай текущие новости, погоду, курсы, цены и расписания;
- для актуальных данных используй только данные, переданные сервером.
```

При `latin on` добавляется:

```text
Пиши русский текст транслитом латиницей.
Не переводи ответ на английский, если пользователь не просил перевод.
```

---

# 21. Контекст диалога без БД

Env:

```text
MAX_CONTEXT_MESSAGES=8
```

Перед запросом к GigaChat сервис получает последние SMS диалога через Plusofon API.

Из них строится контекст:

```text
user
assistant
user
assistant
...
```

Используется максимум `MAX_CONTEXT_MESSAGES`.

## 21.1. Фильтрация

В контекст не должны попадать:

- PIN-команды;
- `auth`;
- служебные `stat`;
- `models`;
- системные ошибки;
- нерелевантные технические уведомления.

## 21.2. `clear`

Команда:

```text
clear
```

создаёт персональную временную границу контекста в RAM.

Ответ:

```text
Новый диалог.
```

После `clear` более старые SMS не подмешиваются в новые AI-запросы, пока работает текущий процесс.

После перезапуска эта граница исчезает.

---

# 22. Продолжение `+` без БД

Команда:

```text
+
```

не использует сохранённый в БД хвост.

Алгоритм:

```text
1. Получить последние сообщения диалога из Plusofon.
2. Найти последний пользовательский AI-вопрос.
3. Найти последний AI-ответ.
4. Отправить их GigaChat.
5. Добавить инструкцию:
   "Продолжи предыдущий ответ. Не повторяй уже отправленный текст."
6. Получить новый короткий фрагмент.
7. Проверить дневной расход.
8. Добавить [!] при необходимости.
9. Отправить SMS.
```

Плюс:

- переживает redeploy;
- не требует БД.

Минус:

- продолжение генерируется заново;
- это не дословно сохранённый хвост первоначальной генерации.

Это принимается сознательно.

---

# 23. SMS formatter

`SMSFormatter` обязан:

1. удалить Markdown;
2. убрать лишние переносы;
3. нормализовать пробелы;
4. определить GSM-7 / UCS-2;
5. посчитать физические сегменты;
6. учитывать служебный `[!]`;
7. учитывать `[1/N]`, если используется;
8. разбивать по границам слов;
9. не ломать URL/числа без необходимости;
10. не отправлять пустые сегменты.

Ориентир:

```text
UCS-2:
  одиночная ~70 символов
  составная ~67 на сегмент

GSM-7:
  одиночная ~160
  составная ~153 на сегмент
```

Финальную реализацию необходимо проверить реальными тестами Plusofon/PDU.

---

# 24. `latin on/off`

Команда персональная.

```text
latin on
```

Ответ:

```text
Latin: ON
```

```text
latin off
```

Ответ:

```text
Latin: OFF
```

Настройка хранится:

```python
latin_mode_by_phone[phone]
```

Она:

- отдельная для каждого номера;
- не требует MASTER_PIN;
- сбрасывается после перезапуска.

Цель `latin on` — использовать GSM-7 и вместить больше текста в одну SMS.

Режим не означает автоматический перевод на английский.

---

# 25. Router команд

Приоритет:

```text
1. Авторизация неизвестного номера.
2. Административная команда с MASTER_PIN.
3. Пользовательские системные команды.
4. Инструментальные команды.
5. Продолжение.
6. Обычный AI-вопрос.
```

Псевдокод:

```python
if not is_authorized(phone):
    if matches_auth(text):
        return auth_command(phone, text)
    return ignore()

if matches_master_admin(text):
    return admin_command(phone, text)

if text == "+":
    return continue_command(phone)

if text in {"help", "помощь"}:
    return help_command(phone)

if text in {"stat", "stats", "стат"}:
    return stat_command(phone)

if text in {"clear", "сброс"}:
    return clear_command(phone)

if text == "models":
    return models_command(phone)

if text.startswith("latin "):
    return latin_command(phone, text)

if text.startswith("news"):
    return news_command(phone, text)

if text.startswith("weather"):
    return weather_command(phone, text)

if text.startswith("calc"):
    return calc_command(phone, text)

if text.startswith("translate"):
    return translate_command(phone, text)

if text.startswith("wiki"):
    return wiki_command(phone, text)

if text.startswith("currency"):
    return currency_command(phone, text)

return ai_command(phone, text)
```

---

# 26. Полный набор команд первой версии

```text
<обычный вопрос>
+
help
stat
clear

news [любая тема]
weather [город]
calc [выражение]
translate [текст]
wiki [тема]
currency [валюты]

models
latin on
latin off

<MASTER_PIN> model [название]

<AUTH_PIN> auth
```

---

# 27. `help`

Ответ должен быть максимально коротким.

Пример:

```text
Вопрос=ИИ; +=ещё; news [тема]; weather [город]; calc; translate; wiki; currency; models; latin on/off; stat; clear.
```

Если не помещается разумно — разделить на 2 SMS только при необходимости.

---

# 28. `stat`

SMS-статистика берётся из Plusofon API.

AI-статистика — из RAM.

Пример:

```text
SMS: 4 сегодня / 63 месяц. AI: 7 с запуска. Модель: Ultra.
```

Можно добавить оценку:

```text
~126 ₽
```

Расчёт:

```text
monthly_pdu * SMS_PRICE_RUB
```

Цена конфигурируется через env.

Важно:

- SMS-статистика переживает restart;
- AI-счётчик НЕ переживает restart;
- это должно быть понятно из текста `с запуска`.

---

# 29. `news [любая тема]`

Примеры:

```text
news
news ИИ
news космос
news Якутия
news Apple
news Minecraft
news квантовые компьютеры
```

Тема свободная.

Архитектура:

```text
topic
 |
 v
NewsProvider
 |
 +--> RSS/Atom
 |
 +--> SearchProvider
 |
 v
свежие результаты
 |
 v
дедупликация
 |
 v
отбор
 |
 v
GigaChat summary
 |
 v
SMS
```

## 29.1. Источники

Приоритет:

1. RSS/Atom;
2. интернет-поиск по свежим источникам;
3. GigaChat только для объединения/сжатия.

## 29.2. Запрещено

Нельзя использовать внутреннюю память GigaChat как единственный источник актуальных новостей.

## 29.3. Ссылки

Ссылки пользователю по умолчанию не отправляются.

Внутренне NewsItem содержит:

```text
title
source
url
published_at
snippet/content
```

## 29.4. Результат

Ответ должен быть коротким.

Пример:

```text
1) ... 2) ... 3) ...
```

Если тема широкая — максимум несколько действительно важных новостей.

---

# 30. SearchProvider

Поиск должен быть отдельной абстракцией:

```python
class SearchProvider(Protocol):
    async def search_news(
        self,
        query: str,
        limit: int,
    ) -> list[SearchResult]:
        ...
```

Это позволяет позднее менять поставщика поиска без изменения `news`.

Если у GigaChat появится официальный режим веб-поиска, его можно реализовать как ещё один SearchProvider.

---

# 31. `weather [город]`

Город всегда указывается явно.

Примеры:

```text
weather Якутск
weather Москва
weather London
```

Нет города по умолчанию.

WeatherProvider получает актуальные данные.

GigaChat не обязателен, если данные уже можно компактно сформатировать кодом.

Пример:

```text
Якутск: -18°, облачно; ветер 3 м/с; ночью -23°.
```

---

# 32. `currency`

Примеры:

```text
currency USD RUB
currency EUR RUB
currency CNY RUB
```

Курс всегда приходит из актуального CurrencyProvider.

GigaChat не используется как источник курса.

Ответ:

```text
1 USD ≈ 00.00 RUB
```

Формат зависит от данных провайдера.

---

# 33. `wiki`

Пример:

```text
wiki DHCP
```

Источник:

- Wikipedia/API;
- другой справочный API;
- затем GigaChat может кратко сжать найденный текст.

Если внешний источник недоступен, допускается обычный GigaChat-ответ, но он не должен притворяться актуальным внешним поиском.

---

# 34. `translate`

Примеры:

```text
translate Hello, how are you?
translate en Привет
translate ru Hello
```

Если язык явно указан — соблюдать его.

Если нет — определить автоматически.

Для перевода используется GigaChat.

---

# 35. `calc`

Пример:

```text
calc 1250*1.2
```

Расчёт выполняется локально.

Запрещено:

```python
eval(user_input)
```

Использовать безопасный parser выражений.

Поддержать минимум:

- `+`
- `-`
- `*`
- `/`
- скобки
- проценты при однозначной семантике

---

# 36. Автоматические SMS

В первой версии полностью исключены.

Нет:

```text
daily on
daily off
автоматических новостей
автоматической погоды
автоматических напоминаний
фоновых рассылок
```

Сервис отвечает только на входящие сообщения пользователя.

---

# 37. Часовой пояс

Env:

```text
TIMEZONE=Asia/Yakutsk
```

Все расчёты `сегодня` выполняются в этом часовом поясе.

Внутренние datetime предпочтительно держать timezone-aware.

Порог `[!]` автоматически начинает новый отсчёт при наступлении нового локального дня, потому что статистика заново запрашивается у Plusofon за новый диапазон дат.

---

# 38. GigaChat usage

В RAM:

```python
gigachat_requests_total += 1
gigachat_requests_by_model[model] += 1
```

Увеличивать счётчик после фактической отправки запроса модели.

Если GigaChat response содержит token usage, его можно дополнительно агрегировать в RAM:

```python
prompt_tokens_total
completion_tokens_total
total_tokens
```

Но после restart эти значения обнуляются.

Если GigaChat предоставляет доступный API баланса — его можно запросить отдельно.

---

# 39. Ошибки пользователю

## GigaChat окончательно недоступен

После допустимых retries:

```text
ИИ поломался :(
```

Именно этот текст является стандартным.

## Продолжение невозможно

```text
Продолжения нет.
```

## Неверная явная команда

```text
Неизвестная команда. help — список.
```

## Неверный AUTH_PIN

Предпочтительно:

```text
молчаливое игнорирование
```

## Внешний сервис недоступен

Примеры:

```text
Новости сейчас недоступны :(
Погода сейчас недоступна :(
```

Ответы должны быть короткими.

---

# 40. Retry policy

## GigaChat

При timeout / transient 5xx:

```text
1–2 повтора
```

При auth error:

```text
1. сбросить cached OAuth token
2. получить новый
3. повторить запрос один раз
```

## Plusofon send

Пример:

```text
0 сек
2 сек
10 сек
30 сек
```

Не делать бесконечные retries.

Особенно важно не создать дубли SMS.

---

# 41. HTTP clients

Использовать переиспользуемые `httpx.AsyncClient`, а не создавать новый client на каждый запрос.

Все внешние вызовы имеют:

- timeout;
- понятную обработку статусов;
- ограниченные retries;
- безопасные логи без credentials.

---

# 42. Конкурентная обработка

Если один пользователь быстро отправил две SMS:

```text
A
B
```

ответы должны по возможности прийти в правильном порядке.

В одном экземпляре приложения:

```python
per_phone_locks[phone] = asyncio.Lock()
```

Обработка по конкретному номеру сериализуется.

Для проекта не включать горизонтальное масштабирование без необходимости.

Без внешней координации несколько параллельных экземпляров могут нарушить порядок.

---

# 43. Anti-abuse

Мягкий `[!]` не является security rate limit.

В RAM можно иметь аварийные ограничения:

```text
MAX_INBOUND_PER_MINUTE=5
MAX_INBOUND_PER_HOUR=60
```

Они нужны против:

- loop;
- бага webhook;
- компрометации номера;
- аномального повторного трафика.

Обычное превышение пяти SMS в день не блокируется.

---

# 44. Безопасность

## 44.1. Secrets

Никогда не хранить в Git:

- Plusofon token;
- GigaChat credentials;
- MASTER_PIN;
- AUTH_PIN;
- webhook token;
- API keys внешних сервисов.

## 44.2. Логи

Не логировать:

- PIN;
- Authorization headers;
- OAuth credentials;
- API tokens;
- полный `.env`.

Номер маскировать:

```text
*******4567
```

## 44.3. SMS-admin

Не реализовывать через SMS:

- shell;
- exec;
- произвольные команды ОС;
- просмотр env;
- чтение секретов;
- изменение файлов;
- удалённый Python eval.

`MASTER_PIN` предназначен только для заранее определённых безопасных настроек.

---

# 45. Structured logging

Пример:

```json
{
  "event": "sms_processed",
  "request_id": "uuid",
  "phone": "*******4567",
  "route": "ai",
  "model": "GigaChat-3-Ultra",
  "segments_sent": 1,
  "duration_ms": 1270
}
```

События:

```text
webhook_received
webhook_duplicate
auth_success
auth_rejected
command_routed
gigachat_request
gigachat_error
plusofon_send
plusofon_error
sms_processed
```

---

# 46. Health endpoints

## `/health`

```http
GET /health
```

Ответ:

```json
{"status": "ok"}
```

Не обращается к внешним API.

## `/ready`

Проверяет:

- конфигурация загружена;
- обязательные env присутствуют;
- приложение готово принимать webhook.

Не нужно на каждом `/ready` вызывать Plusofon/GigaChat.

---

# 47. Интерфейсы провайдеров

## SMSProvider

```python
class SMSProvider(Protocol):
    async def send(self, to: str, text: str) -> SendResult:
        ...

    async def list_messages(self, query: SMSQuery) -> list[SMSMessage]:
        ...

    async def get_dialog(self, phone: str, limit: int) -> list[SMSMessage]:
        ...
```

Первая реализация:

```text
PlusofonSMSProvider
```

## LLMProvider

```python
class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[Message],
        model: str,
    ) -> LLMResponse:
        ...

    async def list_models(self) -> list[ModelInfo]:
        ...
```

Первая реализация:

```text
GigaChatProvider
```

## NewsProvider

```python
class NewsProvider(Protocol):
    async def get_news(
        self,
        topic: str | None,
        limit: int,
    ) -> list[NewsItem]:
        ...
```

## WeatherProvider

```python
class WeatherProvider(Protocol):
    async def get_weather(self, city: str) -> WeatherInfo:
        ...
```

## CurrencyProvider

```python
class CurrencyProvider(Protocol):
    async def convert(
        self,
        base: str,
        quote: str,
    ) -> CurrencyRate:
        ...
```

---

# 48. Пример обычного диалога

Пользователь:

```text
Что такое DHCP?
```

Сервис:

```text
DHCP автоматически выдаёт устройствам IP, маску, шлюз и DNS.
```

Пользователь:

```text
А NAT?
```

Сервис получает предыдущий контекст из истории Plusofon и отвечает:

```text
NAT преобразует IP-адреса между сетями; дома обычно позволяет устройствам выходить через один внешний IP.
```

---

# 49. Пример превышения рекомендуемого порога

История Plusofon показывает:

```text
pdu: 1 + 1 + 1 + 2 = 5
```

Следующий ответ:

```text
[!] VLAN логически разделяет одну физическую сеть на несколько изолированных сетей.
```

Сервис продолжает работать без ограничений.

---

# 50. Пример `latin`

Номер A:

```text
latin on
```

Ответ:

```text
Latin: ON
```

Номер B не меняется.

Дальше для номера A:

```text
DHCP avtomaticheski vydaet IP, masku, gateway i DNS...
```

После restart:

```text
DEFAULT_LATIN_MODE
```

снова применяется ко всем номерам.

---

# 51. Пример временной авторизации

Неизвестный номер:

```text
3957 auth
```

При правильном `AUTH_PIN`:

```text
Доступ разрешён до перезапуска.
```

После restart этот номер снова неизвестен.

---

# 52. Пример выбора модели

Пользователь:

```text
models
```

Ответ:

```text
...список доступных моделей...
Текущая: GigaChat-3-Ultra
```

Административная команда:

```text
8241 model GigaChat-2-Pro
```

Ответ:

```text
Модель: GigaChat-2-Pro
```

Выбор относится только к отправившему номеру.

---

# 53. Failure scenarios

## Timeweb перезапустил приложение

Ничего критичного.

Сбрасывается только RAM.

История SMS заново читается из Plusofon.

## GigaChat token потерян

Получить новый по credentials.

## GigaChat недоступен

После retries:

```text
ИИ поломался :(
```

## Plusofon history временно недоступна

Для `stat` вернуть короткую ошибку.

Перед обычным ответом нельзя бесконечно ждать статистику.

Допускается безопасная fallback-политика:

- отправить ответ;
- не ставить ложный `[!]`;
- записать warning в лог.

Либо, если важнее строгий контроль расходов:

- вернуть короткую ошибку без AI-ответа.

Для первой версии рекомендуется **не блокировать полезный ответ только из-за недоступной статистики**, но логировать событие.

## Повтор webhook

TTL-кэш предотвращает обычный мгновенный дубль.

## Restart между дублями

Редкий дубль теоретически возможен — сознательное ограничение архитектуры без БД.

---

# 54. Тесты

## 54.1. Auth

- постоянный номер разрешён;
- неизвестный номер игнорируется;
- правильный AUTH_PIN временно авторизует;
- неправильный AUTH_PIN не авторизует;
- временный номер работает;
- MASTER_PIN не равен AUTH_PIN;
- PIN не попадает в logs.

## 54.2. Router

- обычный текст -> AI;
- `+`;
- `help`;
- `stat`;
- `clear`;
- `news любая тема`;
- `weather город`;
- `calc`;
- `translate`;
- `wiki`;
- `currency`;
- `models`;
- `latin on/off`;
- master `model`.

## 54.3. Formatter

- UCS-2 short;
- UCS-2 multipart;
- GSM-7 short;
- GSM-7 multipart;
- `[!]`;
- `[1/N]`;
- emoji;
- длинное слово;
- пустой ответ;
- служебный префикс не ломает лимит.

## 54.4. Usage

- 0–4 сегмента -> без `[!]`;
- ровно 5 уже отправлено -> следующий с `[!]`;
- новый локальный день;
- пагинация Plusofon;
- суммирование `pdu`;
- статистика месяца.

## 54.5. Webhook

- валидный payload;
- invalid payload;
- loop;
- duplicate TTL;
- неизвестный номер;
- auth flow;
- быстрый HTTP 200.

## 54.6. GigaChat

- OAuth cache;
- refresh after auth error;
- timeout;
- 5xx;
- models list;
- выбранная модель;
- точный fallback `ИИ поломался :(`.

---

# 55. requirements.txt

Ориентир:

```text
fastapi
uvicorn[standard]
httpx
pydantic
pydantic-settings
tenacity
python-dateutil

pytest
pytest-asyncio
respx
```

Не добавлять database libraries.

---

# 56. Этапы реализации

## Этап 1 — минимальный end-to-end

- FastAPI;
- config;
- `/health`;
- webhook Plusofon;
- whitelist;
- Plusofon send;
- GigaChat OAuth;
- GigaChat chat;
- обычный вопрос -> ответ SMS;
- timeout/retry;
- tests.

Критерий:

```text
реальная SMS -> GigaChat -> реальная SMS
```

## Этап 2 — stateless Plusofon history

- `GET /api/v1/sms`;
- dialog history;
- пагинация;
- `pdu`;
- `stat`;
- контекст.

## Этап 3 — SMS formatter

- GSM-7/UCS-2;
- сегменты;
- `[!]`;
- compact answers;
- `+`.

## Этап 4 — авторизация

- 2–3 постоянных номера;
- AUTH_PIN;
- временный whitelist;
- MASTER_PIN;
- secure logging.

## Этап 5 — runtime settings

- `models`;
- `model`;
- `latin on/off`;
- per-phone settings;
- `clear`.

## Этап 6 — инструменты

- `news [любая тема]`;
- RSS;
- SearchProvider;
- `weather [город]`;
- `currency`;
- `wiki`;
- `translate`;
- `calc`.

## Этап 7 — эксплуатационная надёжность

- DLR при необходимости;
- улучшенные retries;
- structured logs;
- anti-abuse;
- smoke tests;
- security review.

---

# 57. Что не нужно делать

Не добавлять без отдельного решения:

- БД;
- Redis;
- очереди;
- web-admin;
- регистрацию через сайт;
- JWT для SMS;
- микросервисы;
- Kubernetes;
- локальную LLM;
- RAG;
- мобильное приложение;
- автоматические рассылки;
- shell-команды через SMS.

---

# 58. Первая задача для Codex

```text
Реализуй Этап 1 из architecture.md.

Архитектура является единственным источником требований.
Не добавляй PostgreSQL, SQLite, Redis, SQLAlchemy, Alembic или другое постоянное хранилище.

Нужно:
- Python 3.12+;
- FastAPI;
- pydantic-settings;
- структурированный проект app/;
- GET /health;
- POST /webhooks/plusofon/incoming/<token>;
- схема входящего webhook;
- нормализация телефонных номеров;
- постоянный whitelist из ALLOWED_PHONE_NUMBERS;
- защита от SMS-loop;
- in-memory TTL anti-duplicate;
- Plusofon REST client для отправки SMS;
- GigaChat REST client;
- OAuth GIGACHAT_API_PERS;
- OAuth token cache в RAM;
- обычный текст пользователя -> GigaChat -> короткий SMS-ответ;
- точный текст финальной ошибки GigaChat: "ИИ поломался :(";
- httpx AsyncClient;
- timeout;
- ограниченные retries;
- structured logging без секретов;
- unit tests;
- .env.example;
- README с локальным запуском.

Пока не добавляй:
- news;
- weather;
- currency;
- wiki;
- translate;
- models/model;
- latin;
- stat;
- Plusofon history;
- автоматические SMS;
- какую-либо БД.

После реализации:
1. запусти тесты;
2. исправь все ошибки;
3. проверь импорт приложения;
4. перечисли созданные файлы;
5. перечисли env variables, которые нужно заполнить;
6. не выполняй production deploy без отдельной команды.
```

---

# 59. Definition of Done полной первой версии

- [ ] FastAPI запускается.
- [ ] `/health` работает.
- [ ] ручной deploy в Timeweb Apps работает.
- [ ] Plusofon webhook принимает SMS.
- [ ] SMS-loop исключён.
- [ ] 2–3 постоянных номера читаются из env.
- [ ] неизвестный номер игнорируется.
- [ ] `AUTH_PIN auth` временно допускает новый номер.
- [ ] временный номер исчезает после restart.
- [ ] `MASTER_PIN` защищает административные изменения.
- [ ] PIN-коды не попадают в логи.
- [ ] GigaChat отвечает.
- [ ] финальная ошибка GigaChat = `ИИ поломался :(`.
- [ ] OAuth token кэшируется.
- [ ] `models` получает доступные модели.
- [ ] `model` меняет модель персонально до restart.
- [ ] `latin on/off` работает отдельно для каждого номера.
- [ ] контекст строится из Plusofon history.
- [ ] `clear` работает как временная RAM-граница.
- [ ] `+` продолжает ответ через историю Plusofon.
- [ ] `stat` получает SMS-данные через Plusofon.
- [ ] AI-запросы считаются в RAM с запуска.
- [ ] pdu корректно суммируется.
- [ ] первые 5 сегментов без `[!]`.
- [ ] 6-й и следующие с `[!]`.
- [ ] `[!]` ничего не блокирует.
- [ ] новый локальный день учитывается через `Asia/Yakutsk`.
- [ ] длинные ответы корректно форматируются.
- [ ] `news [любая тема]` работает.
- [ ] новости используют внешние свежие источники.
- [ ] ссылки по умолчанию не отправляются.
- [ ] `weather [город]` требует явный город.
- [ ] `currency` использует актуальный API.
- [ ] `wiki` работает.
- [ ] `translate` работает.
- [ ] `calc` не использует unsafe eval.
- [ ] автоматических исходящих SMS нет.
- [ ] постоянной БД нет.
- [ ] SQL/Redis-зависимостей нет.
- [ ] секреты отсутствуют в Git.
- [ ] все внешние HTTP-запросы имеют timeout.
- [ ] retries ограничены.
- [ ] основные сценарии покрыты тестами.
- [ ] реальная тестовая SMS успешно прошла end-to-end.

---

# 60. Ограничения stateless-подхода

Сознательно принимаются:

1. Временная авторизация исчезает после restart.
2. Персональный `latin` исчезает после restart.
3. Персональная модель исчезает после restart.
4. `clear`-граница исчезает после restart.
5. AI usage counters исчезают после restart.
6. Anti-duplicate TTL cache исчезает после restart.
7. Редкий повтор webhook сразу после restart теоретически может создать дубль.
8. `+` генерирует продолжение заново, а не выдаёт сохранённый дословный хвост.

Для персонального сервиса с несколькими SMS в день эти ограничения принимаются как разумный компромисс ради отсутствия БД.

---

# 61. Официальные интеграции, которые нужно перепроверить перед production

Перед финальным развёртыванием Codex/разработчик должен сверить актуальную документацию:

- Plusofon API v1;
- фактическую схему incoming SMS webhook;
- `POST /api/v1/sms`;
- историю `GET /api/v1/sms`;
- endpoint истории диалога, если он используется;
- поля `pdu` и пагинации;
- GigaChat OAuth;
- GigaChat chat completions;
- GigaChat models endpoint;
- GigaChat balance/limits endpoint, если он доступен тарифу;
- Timeweb Cloud Apps FastAPI deployment;
- актуальные тарифы SMS.

Документация внешних API важнее примеров payload в этом файле, если поставщик изменил контракт.

При этом функциональные требования этой архитектуры остаются обязательными.

---

# 62. Итоговая схема

```text
                         +-------------------+
                         |    GigaChat API   |
                         +---------^---------+
                                   |
                                   | HTTPS
                                   |
+---------+      SMS       +-------+--------+
| Телефон | <------------> |    Plusofon    |
+---------+                 +---+---------+--+
                                |         ^
                       webhook  |         | send/history
                                v         |
                      +---------+---------+------+
                      | Timeweb Cloud Apps      |
                      |                         |
                      | FastAPI                 |
                      | - auth                  |
                      | - router                |
                      | - Plusofon client       |
                      | - GigaChat client       |
                      | - context from SMS      |
                      | - SMS formatter         |
                      | - [!] warning           |
                      | - per-phone RAM state   |
                      | - tools/providers       |
                      +-------------------------+

Постоянная БД: НЕТ
Redis: НЕТ
Авторассылки: НЕТ
```

---

# 63. Ключевая инструкция для Codex

> Не пытайся "улучшить" архитектуру добавлением БД. Отсутствие БД — сознательное продуктовое требование. Долговременные SMS-данные берутся у Plusofon, а пользовательские временные настройки должны быть потеряны после перезапуска приложения по дизайну.
