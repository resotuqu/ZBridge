# Реальная SMS end-to-end проверка

Проверка намеренно выполняется вручную. Она не отправляет автоматические SMS
и запускается только после деплоя в Timeweb точного проверяемого commit SHA.

## Предварительные условия

- в Timeweb запущен проверяемый commit;
- `/health` и `/ready` отвечают HTTP 200;
- webhook Plusofon указывает на это приложение Timeweb;
- физический тестовый телефон входит в `ALLOWED_PHONE_NUMBERS`;
- локальный `.env` содержит те же настройки Plusofon, что и деплой;
- credentials GigaChat и Plusofon действительны.

## Smoke-проверки провайдеров

Проверить GigaChat без отправки SMS:

```bash
python -m scripts.smoke_gigachat
```

Показать одну платную исходящую SMS без отправки:

```bash
python -m scripts.smoke_plusofon --to 79XXXXXXXXX
```

Отправить её только после проверки маскированного получателя и текста:

```bash
python -m scripts.smoke_plusofon \
  --to 79XXXXXXXXX \
  --confirm-send
```

## Полный цикл через физический телефон

Запустить защищённую проверку:

```bash
python -m scripts.smoke_e2e --phone 79XXXXXXXXX
```

Скрипт напечатает уникальное короткое сообщение. Пока он ждёт, отправьте этот
текст без изменений с физического телефона на номер Plusofon. Успешный
результат должен содержать:

```text
E2E smoke test: ROUND TRIP OK
```

Затем подтвердите, что ответ действительно пришёл на физический телефон. Так
проверяется реальный маршрут:

```text
phone -> Plusofon -> Timeweb webhook -> GigaChat -> Plusofon -> phone
```

Запишите в pull request проверенный commit SHA, время UTC, маскированный номер
и результат. Никогда не вставляйте токены, PIN-коды, полные номера телефонов
или содержимое SMS в GitHub logs и комментарии.
