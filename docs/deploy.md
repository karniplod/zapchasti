# Развёртывание

## requirements.txt

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
alembic==1.14.0
pydantic==2.10.4
pydantic-settings==2.7.0
jinja2==3.1.5
python-multipart==0.0.20
passlib[bcrypt]==1.7.4
itsdangerous==2.2.0
segno==1.6.1
pillow==11.1.0
```

`python-multipart` обязателен — без него формы с фото молча не работают.
`pillow` понадобится для ресайза изображений.

---

## .env

```ini
DEBUG=false
BASE_URL=https://razbor.example.ru
DATABASE_URL=postgresql://razbor:пароль@localhost:5432/razbor
SECRET_KEY=сюда_вывод_openssl_rand_hex_32
SESSION_TTL_HOURS=12
SMTP_HOST=smtp.yandex.ru
SMTP_USER=orders@example.ru
SMTP_PASSWORD=пароль_приложения
ORDER_NOTIFY_TO=manager@example.ru
```

Права на файл: `chmod 600 .env` — там пароль от базы.

---

## Установка

```bash
sudo -u postgres createuser razbor -P
sudo -u postgres createdb razbor -O razbor

cd /opt/razbor
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Схема
psql -U razbor -d razbor -f sql/schema.sql
psql -U razbor -d razbor -f sql/vin_patterns.sql
psql -U razbor -d razbor -f sql/vin_queries.sql
psql -U razbor -d razbor -c "CREATE SEQUENCE donor_code_seq START 1;"
psql -U razbor -d razbor -c "ALTER TABLE donors ADD COLUMN part_counter int NOT NULL DEFAULT 0;"
psql -U razbor -d razbor -c "ALTER TABLE parts ADD COLUMN label_printed_at timestamptz;"
psql -U razbor -d razbor -c "ALTER TABLE users ADD COLUMN last_login_at timestamptz;"

# Alembic поверх готовой схемы
./venv/bin/alembic revision --autogenerate -m "baseline"
./venv/bin/alembic stamp head

# Первый администратор
./venv/bin/python -m app.scripts.create_admin
```

---

## /etc/systemd/system/razbor.service

```ini
[Unit]
Description=Razbor — каталог автодонора
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=razbor
Group=razbor
WorkingDirectory=/opt/razbor
Environment="PATH=/opt/razbor/venv/bin"
ExecStart=/opt/razbor/venv/bin/uvicorn app.main:app \
          --host 127.0.0.1 --port 8100 --workers 2 --proxy-headers
Restart=always
RestartSec=5

# Изоляция
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/razbor/media

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now razbor
sudo journalctl -u razbor -f
```

---

## Caddyfile

```caddy
razbor.example.ru {
    encode zstd gzip

    # Статику и фото отдаёт Caddy напрямую — Python на этом
    # только тратил бы воркеры
    handle /media/* {
        root * /opt/razbor
        header Cache-Control "public, max-age=2592000, immutable"
        file_server
    }

    handle /static/* {
        root * /opt/razbor
        header Cache-Control "public, max-age=604800"
        file_server
    }

    handle {
        reverse_proxy 127.0.0.1:8100
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }

    # Загрузка фото с телефона: несколько снимков разом
    request_body {
        max_size 60MB
    }

    log {
        output file /var/log/caddy/razbor.log {
            roll_size 20mb
            roll_keep 10
        }
    }
}
```

---

## Резервные копии

```bash
# /etc/cron.d/razbor-backup
0 3 * * * razbor pg_dump razbor | gzip > /opt/backups/razbor-$(date +\%F).sql.gz
30 3 * * 0 razbor tar czf /opt/backups/media-$(date +\%F).tar.gz /opt/razbor/media
0 4 * * * razbor find /opt/backups -name '*.gz' -mtime +30 -delete
```

Фото — единственное, что нельзя восстановить: машина уже разобрана и продана.
Дамп базы без медиа даёт каталог с пустыми карточками.
