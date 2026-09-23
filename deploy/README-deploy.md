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
- **Почта** — `SMTP_*` и `ORDER_NOTIFY_TO` в `/opt/razbor/.env`: пока пусто,
  письма о заказах не уходят, заказы видно только в бэкенде.
- **Геолокация города** — положить базу MaxMind GeoLite2-City в
  `/opt/razbor/data/GeoLite2-City.mmdb`; без файла определение просто выключено.
- **Подсказка каталожных номеров** — `PARSERS_ENABLED=true` и ключ Brave
  в `SEARCH_API_KEY`. По умолчанию выключено: сервер не должен сам ходить
  на чужие сайты.
- **Тестовые данные** (11 машин, 57 деталей) на бою не нужны. Если их
  залили для показа, убрать:
  `sudo -u razbor venv/bin/python -m app.scripts.seed_test_data --remove --apply`

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
