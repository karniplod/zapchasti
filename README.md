
# Авторазбор — каталог б/у автозапчастей

Приёмка машин, разбор на детали, склад и публичный каталог с подбором по VIN.

Стек: FastAPI + PostgreSQL + Jinja2, Caddy как реверс-прокси.

---

## Структура

```
razbor/
├── app/
│   ├── config.py            настройки из .env
│   ├── database.py          подключение к PostgreSQL
│   ├── auth.py              вход сотрудников, роли
│   ├── main.py              сборка приложения
│   ├── vin_decoder.py       свой VIN-декодер
│   ├── routers/
│   │   ├── intake.py        приёмка авто
│   │   ├── dismantle.py     разбор, этикетки с QR
│   │   ├── catalog.py       каталог, VIN-поиск покупателя
│   │   └── reference.py     справочник, добавление на бегу
│   ├── services/
│   │   └── images.py        ресайз, webp, миниатюры
│   └── scripts/
│       ├── create_admin.py       первый администратор
│       └── import_catalog.py     импорт справочника из файла
├── templates/
│   ├── catalog.html         каталог
│   ├── part.html            карточка детали
│   ├── error.html           404 / 403 / 500
│   └── admin/
│       ├── login.html       вход
│       ├── intake.html      форма приёмки
│       ├── dismantle.html   рабочее место разборщика
│       ├── labels.html      печать этикеток
│       └── _quickadd.html   модалка добавления модели
├── sql/                     схема и миграции
├── deploy/                  systemd, Caddyfile, cron
├── docs/                    развёртывание, карта проекта
├── media/                   фото (в git не попадает)
└── static/
```

---

## Установка

```bash
# 1. Разложить на сервере
sudo mv razbor /opt/razbor
sudo useradd -r -s /bin/false razbor
sudo chown -R razbor:razbor /opt/razbor
cd /opt/razbor

# 2. Окружение
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 3. База
sudo -u postgres createuser razbor -P
sudo -u postgres createdb razbor -O razbor

# Порядок важен: файлы ссылаются друг на друга внешними ключами
psql -U razbor -d razbor -f sql/schema.sql
psql -U razbor -d razbor -f sql/vin_patterns.sql
psql -U razbor -d razbor -f sql/vin_queries.sql
psql -U razbor -d razbor -f sql/catalog_mapping.sql
psql -U razbor -d razbor -f sql/00_migrations.sql

# 4. Настройки
cp .env.example .env
openssl rand -hex 32          # вставить в SECRET_KEY
nano .env
chmod 600 .env

# 5. Администратор
./venv/bin/python -m app.scripts.create_admin

# 6. Автозапуск
sudo cp deploy/razbor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now razbor
sudo journalctl -u razbor -f

# 7. HTTPS
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # поменять домен
sudo systemctl reload caddy

# 8. Резервные копии
sudo mkdir -p /opt/backups && sudo chown razbor /opt/backups
sudo cp deploy/backup.cron /etc/cron.d/razbor-backup
```

Проверка: `curl localhost:8100/healthz` должен вернуть `{"db":true}`.

---

## Первые шаги после установки

**1. Справочник автомобилей.** Пока он пуст, форма приёмки открывается,
но выбирать в ней нечего.

```bash
./venv/bin/python -m app.scripts.import_catalog \
    --file spravochnik.xlsx --source avito --dry-run
```

Сначала всегда с `--dry-run` — покажет, что распозналось, и откатит.
Файл берите официальный: справочник Авито Автозагрузки. Тогда названия
марок совпадут с площадкой и выгрузка пойдёт без ручного сопоставления.

**2. Дерево категорий запчастей.** Заполняется вручную под ваш профиль:
кузовной разборке нужны одни узлы, моторной другие.

**3. Одна машина целиком.** Примите и разберите её полностью до того,
как писать корзину. Живой разбор за час покажет, каких категорий не хватает
и какие поля формы разборщик игнорирует. Переделывать на этом этапе дёшево.

---

## Как это работает

**VIN-декодер учится сам.** Позиции 1–3 (завод), 9 (контрольная цифра)
и 10 (год) читаются по стандарту бесплатно. Позиции 4–8 — модель и двигатель —
не стандартизованы, их не расшифровать без каталогов производителя.
Поэтому первую машину каждой модели приёмщик заводит руками, система
запоминает связку `WMI + VDS → модификация`, и вторая такая же определяется
автоматически. Через 40–50 машин своя база покрывает ваш реальный поток
точнее любого универсального API.

**Применимость висит на OEM-номере**, а не на конкретной детали. Заполнили
один раз для номера — все будущие детали с тем же номером ищутся по всем
подходящим поколениям.

**Артикул = QR.** `D-0042-0137` — номер машины плюс порядковый номер детали.
Печатается на этикетке, сканируется на складе, ведёт на публичную карточку.

**Неопознанные VIN — это заявки на закупку.** Всё, что искали покупатели
и не нашли, копится в `vin_queries`. Представление `unmet_demand` показывает,
какую машину выгодно взять на разбор следующей.

---

## Роли

| Роль | Может |
|---|---|
| `dismantler` | разбор, этикетки, добавление моделей в справочник |
| `manager` | плюс приёмка, цены, заказы, проверка справочника |
| `admin` | всё |

---

## Что не доделано

1. Корзина и оформление заказа с резервом детали
2. Онлайн-оплата
3. Админка: список машин, таблица деталей, заказы
4. Выгрузка на Авито и Дром
5. Главная страница, ЧПУ-разделы, sitemap
6. Alembic-миграции

Подробнее — в `docs/structure.md`.
=======
# zapchasti
crm + site

