# Установка на сервер

Ubuntu 22.04 или 24.04, чистый, с доступом по SSH. Всё ставится одной
командой, повторный запуск безопасен.

## 1. Разложить

```bash
ssh root@СЕРВЕР
apt-get update && apt-get install -y git
git clone https://github.com/karniplod/zapchasti.git /tmp/zapchasti
bash /tmp/zapchasti/deploy/bootstrap.sh --domain ваш-домен.ру
```

Без домена — `--ip`: сайт откроется по `http://адрес-сервера`, **без
шифрования**. Годится, чтобы посмотреть; принимать заказы и пароли
покупателей так нельзя. Домен нужно заранее направить на адрес сервера
(A-запись), иначе Caddy не получит сертификат.

Проекту нужен PostgreSQL 15-й или новее: миграции используют
`NULLS NOT DISTINCT`. В Ubuntu 22.04 штатный — 14-й, поэтому скрипт сам
берёт 17-й из репозитория PGDG. Если на сервере уже стоит кластер старше
15-го, скрипт остановится: переносить чужие данные вслепую нельзя, нужно
`pg_upgradecluster`.

Что делает скрипт: ставит PostgreSQL, Python, Caddy; заводит системного
пользователя `razbor` и каталог `/opt/razbor`; создаёт базу и `.env`
(пароль базы и ключ сессий генерирует на месте, локальные не переносит);
накатывает схему и миграции; заводит дерево категорий; включает автозапуск,
Caddy, ежедневные резервные копии и брандмауэр.

Чего не делает: не заводит администратора и не переносит справочник машин —
это следующие два шага.

## 2. Администратор

```bash
cd /opt/razbor && sudo -u razbor venv/bin/python -m app.scripts.create_admin
```

Логин и пароль спросит. Пароль короче 8 символов не примет.

## 3. Справочник машин

Марки, модели, поколения и модификации (около 34 МБ) на сервере взять
неоткуда: в репозитории их нет. Переносим выгрузкой с рабочей машины —
только справочные таблицы, без машин, деталей и заказов.

На рабочей машине:

```bash
pg_dump -U postgres -d razbor --data-only --no-owner -t brands -t models -t generations -t modifications -t complectations -t part_categories -t wmi | gzip > reference.sql.gz
scp reference.sql.gz root@СЕРВЕР:/tmp/
```

На сервере:

```bash
# Дерево категорий уже создал bootstrap — чтобы не задвоилось, чистим
sudo -u postgres psql -d razbor -c "TRUNCATE part_categories RESTART IDENTITY CASCADE"
zcat /tmp/reference.sql.gz | sudo -u postgres psql -d razbor -v ON_ERROR_STOP=1

# Счётчики id. pg_dump кладёт их в выгрузку сам, это подстраховка на случай,
# если выгрузку делали иначе: без верных счётчиков первая же новая марка
# или категория упадёт на дубле ключа
for t in brands models generations modifications complectations part_categories; do
  sudo -u postgres psql -qd razbor -c "SELECT setval(pg_get_serial_sequence('$t','id'), greatest((SELECT coalesce(max(id),1) FROM $t), 1))"
done

systemctl restart razbor
```

Проверить, что доехало:

```bash
sudo -u postgres psql -d razbor -c \
  "SELECT (SELECT count(*) FROM brands) AS марок,
          (SELECT count(*) FROM models) AS моделей,
          (SELECT count(*) FROM generations) AS поколений,
          (SELECT count(*) FROM part_categories) AS категорий"
```

## 4. Проверка

```bash
systemctl status razbor caddy --no-pager
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/
journalctl -u razbor -n 50 --no-pager
```

Страницы: `/` витрина, `/catalog`, `/cars`, `/login` вход в бэкенд.

Полная проверка страниц и скриптов — с рабочей машины по адресу сервера:

```bash
python tools/check_js.py    # в файле поменять B на адрес сервера
```

## Дальше

- **Филиалы** — в бэкенде: без них у деталей не будет города и самовывоза.
- **Почта** — `SMTP_HOST`, `SMTP_PORT` (465 — SSL, 587 — STARTTLS), `SMTP_USER`,
  `SMTP_PASSWORD`, `MAIL_FROM` в `/opt/razbor/.env`. С ней регистрация по email
  требует подтверждения письмом (ссылка на сутки); без неё кабинет по email
  открывается сразу, как раньше. Сейчас — ящик Beget noreply@avtodonor.fun:
  smtp.beget.com, порт 465 (SSL). Для Яндекса: smtp.yandex.ru, 465, пароль
  приложения из настроек Яндекс ID. `ORDER_NOTIFY_TO` — писем о заказах пока нет.
- **Геолокация города** — положить базу MaxMind GeoLite2-City в
  `/opt/razbor/data/GeoLite2-City.mmdb`; без файла определение просто выключено.
- **Подсказка каталожных номеров** — `PARSERS_ENABLED=true` и ключ Brave
  в `SEARCH_API_KEY`. По умолчанию выключено: сервер не должен сам ходить
  на чужие сайты.
- **Быстрый вход покупателя** — ключи в `/opt/razbor/.env`, кнопка появляется,
  только когда ключи заданы (после правки — `systemctl restart razbor`):
  - Google: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` из Google Cloud Console
    (OAuth client «Web application»), адрес возврата `{BASE_URL}/auth/google/callback`;
  - Яндекс ID: `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET` из oauth.yandex.ru
    (веб-сервисы), Redirect URI `{BASE_URL}/auth/yandex/callback`, доступы —
    почта, логин и имя, номер телефона;
  - VK ID: `VK_CLIENT_ID` из id.vk.com (веб-приложение), доверенный адрес
    `{BASE_URL}/auth/vk/callback`, доступы — email и телефон;
  - Telegram: бот у @BotFather, `TELEGRAM_BOT_NAME` и `TELEGRAM_BOT_TOKEN`,
    там же `/setdomain` — домен сайта;
  - MAX: бот на платформе MAX для партнёров, `MAX_BOT_NAME`, `MAX_BOT_TOKEN`,
    `MAX_WEBHOOK_SECRET` (5–256 символов); подписка на вебхук —
    `POST https://platform-api2.max.ru/subscriptions` с
    `{"url": "{BASE_URL}/auth/max/webhook", "update_types": ["bot_started", "message_callback"], "secret": "…"}`.
- **Онлайн-оплата** — ЮKassa: `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`;
  если в ЮKassa подключены чеки (54-ФЗ) — `YOOKASSA_RECEIPTS=true` и ставка
  `YOOKASSA_VAT_CODE` (1 — без НДС);
  в личном кабинете ЮKassa HTTP-уведомления на `{BASE_URL}/api/payments/yookassa`
  (события `payment.succeeded`, `payment.canceled`). Без ключей заказ
  оплачивается при получении. `PAYMENT_DEMO=true` — учебная оплата без банка,
  только для стенда.
- **Робокасса** — `ROBOKASSA_LOGIN`, `ROBOKASSA_PASSWORD1`, `ROBOKASSA_PASSWORD2`
  (технические настройки магазина, алгоритм MD5), `ROBOKASSA_TEST=true` для
  тестовых платежей. В кабинете Робокассы: Result URL
  `{BASE_URL}/api/payments/robokassa/result` (POST), Success URL
  `{BASE_URL}/pay/robokassa/success`, Fail URL `{BASE_URL}/pay/robokassa/fail` (GET).
  Пока ключей нет, способ виден покупателю (`ROBOKASSA_PREVIEW=true`): заказ
  оформляется, оплатить предлагаем при получении.
- **Тестовые данные** (11 машин, 57 деталей) на бою не нужны. Если их
  залили для показа, убрать:
  `sudo -u razbor venv/bin/python -m app.scripts.seed_test_data --remove --apply`
  Города Владивосток и Самара (по 50 деталей) — `seed_test_data --cities --apply`.

## Обновление

```bash
bash /opt/razbor/deploy/bootstrap.sh --domain ваш-домен.ру
```

Заберёт свежий код из ветки `main`, накатит новые миграции, перезапустит
службу. `.env`, база и медиа остаются на месте.

## Безопасность

- Пароль root после установки сменить, вход оставить по ключу:
  `PasswordAuthentication no` в `/etc/ssh/sshd_config`, затем
  `systemctl restart ssh`.
- `.env` лежит с правами 600 и принадлежит `razbor` — там пароль базы
  и ключ подписи сессий. В репозиторий он не попадает.
- PostgreSQL слушает только localhost; наружу открыты 22, 80 и 443.
- Резервные копии: база ежедневно в 3:00, медиа по воскресеньям,
  хранятся 30 дней в `/opt/backups`. Копии лежат на том же сервере —
  забирать их куда-то ещё нужно отдельно.
