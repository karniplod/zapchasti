#!/usr/bin/env bash
# Установка Автодонора на чистый Ubuntu 22.04/24.04. Запускать от root:
#
#   bash deploy/bootstrap.sh --domain razbor.example.ru
#   bash deploy/bootstrap.sh --ip                     # без домена, только HTTP
#
# Повторный запуск безопасен: ничего не перезаписывает вслепую — уже
# созданную базу, .env с паролями и пользователя не трогает. То, чего нет,
# создаёт; что есть — обновляет код и перезапускает службу.
#
# Чего скрипт НЕ делает, потому что это решает человек:
#   • не заводит администратора  — python -m app.scripts.create_admin
#   • не заливает справочник машин — см. deploy/README-deploy.md
#   • не открывает порт базы наружу: PostgreSQL слушает только localhost
set -euo pipefail

DOMAIN=""
REPO="https://github.com/karniplod/zapchasti.git"
BRANCH="main"
APP_DIR="/opt/razbor"
APP_USER="razbor"
DB_NAME="razbor"
DB_USER="razbor"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --ip)     DOMAIN=""; shift ;;
    --repo)   REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    *) echo "Неизвестный ключ: $1"; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Нужен root"; exit 1; }
say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
cd /tmp   # чтобы sudo -u postgres не ругался на чужой /root

say "Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git curl ufw gnupg ca-certificates \
  debian-keyring debian-archive-keyring apt-transport-https >/dev/null

# PostgreSQL. Нужен 15-й или новее: миграции используют NULLS NOT DISTINCT.
# В Ubuntu 22.04 штатный — 14-й, поэтому берём 17-й из репозитория PGDG.
# Ставим именно версию, а не метапакет postgresql: тот тянет самый свежий
# выпуск и заводит рядом второй кластер
PG_MAJOR=17
PG_CUR="$(pg_lsclusters -h 2>/dev/null | awk '{print $1}' | sort -rn | head -1)"
if [[ -n "$PG_CUR" && "$PG_CUR" -lt 15 ]]; then
  echo "  На сервере PostgreSQL $PG_CUR, проекту нужен 15-й или новее."
  echo "  Перенесите данные и запустите снова:"
  echo "    apt-get install -y postgresql-$PG_MAJOR"
  echo "    pg_upgradecluster $PG_CUR main && pg_dropcluster --stop $PG_CUR main"
  exit 1
fi
if [[ -z "$PG_CUR" ]]; then
  if ! apt-cache show "postgresql-$PG_MAJOR" >/dev/null 2>&1; then
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --batch --yes --dearmor -o /usr/share/keyrings/pgdg.gpg
    . /etc/os-release
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list
    apt-get update -qq
  fi
  apt-get install -y -qq "postgresql-$PG_MAJOR" >/dev/null
fi

if ! command -v caddy >/dev/null; then
  # Caddy: HTTPS сам, без возни с сертификатами
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi
systemctl enable --now postgresql >/dev/null

say "Пользователь и каталоги"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" /opt/backups
chown "$APP_USER:$APP_USER" /opt/backups

say "Код"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"
else
  rm -rf "$APP_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
mkdir -p "$APP_DIR/media/parts" "$APP_DIR/media/donors" "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "Окружение Python"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv" 2>/dev/null || true
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

say "База данных"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  if [[ -f "$APP_DIR/.env" ]]; then
    # Настройки есть, а пользователя базы нет — кластер пересоздавали.
    # Пароль берём из .env, иначе приложение в базу не войдёт
    DB_PASS="$(sed -n 's|^DATABASE_URL=postgresql://[^:]*:\([^@]*\)@.*|\1|p' "$APP_DIR/.env")"
    [[ -n "$DB_PASS" ]] || { echo "  В .env не разобрать пароль базы — поправьте DATABASE_URL"; exit 1; }
    echo "  пользователя нет, пароль беру из .env"
  else
    DB_PASS="$(openssl rand -hex 24)"
  fi
  sudo -u postgres psql -qc "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS'"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  sudo -u postgres createdb "$DB_NAME" -O "$DB_USER"
fi

say "Настройки (.env)"
if [[ -f "$APP_DIR/.env" ]]; then
  echo "  .env уже есть — не трогаю (там пароли)"
else
  [[ -n "${DB_PASS:-}" ]] || { echo "  Пользователь базы был заведён раньше, а .env нет."; \
    echo "  Задайте пароль вручную: sudo -u postgres psql -c \"ALTER USER $DB_USER WITH PASSWORD '…'\""; exit 1; }
  BASE_URL="http://$(hostname -I | awk '{print $1}')"
  [[ -n "$DOMAIN" ]] && BASE_URL="https://$DOMAIN"
  sed -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME|" \
      -e "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" \
      -e "s|^BASE_URL=.*|BASE_URL=$BASE_URL|" \
      -e "s|^DEBUG=.*|DEBUG=false|" \
      "$APP_DIR/.env.example" > "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "  создан, пароль базы и ключ сессий сгенерированы на месте"
fi

say "Схема базы"
DB_URL="$(grep -E '^DATABASE_URL=' "$APP_DIR/.env" | cut -d= -f2-)"
TABLES="$(sudo -u postgres psql -tAd "$DB_NAME" -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
if [[ "$TABLES" -eq 0 ]]; then
  for f in schema.sql vin_patterns.sql vin_queries.sql catalog_mapping.sql; do
    echo "  $f"
    sudo -u postgres psql -q -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$APP_DIR/sql/$f"
  done
else
  echo "  таблицы уже есть ($TABLES) — основные файлы пропускаю"
fi
echo "  00_migrations.sql"
sudo -u postgres psql -q -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$APP_DIR/sql/00_migrations.sql"
sudo -u postgres psql -q -d "$DB_NAME" -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO $DB_USER;
  GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;
  GRANT ALL ON SCHEMA public TO $DB_USER"

say "Дерево категорий"
(cd "$APP_DIR" && sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" -m app.scripts.seed_categories)

say "Служба"
cp "$APP_DIR/deploy/razbor.service" /etc/systemd/system/razbor.service
systemctl daemon-reload
systemctl enable --now razbor >/dev/null
systemctl restart razbor

say "Caddy"
if [[ -n "$DOMAIN" ]]; then
  sed "s|razbor.example.ru|$DOMAIN|" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
else
  # Без домена сертификат не получить: отдаём по HTTP, но честно об этом
  # предупреждаем в конце. Заголовок HSTS без HTTPS смысла не имеет
  sed -e "s|razbor.example.ru|:80|" \
      -e "/Strict-Transport-Security/d" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
fi
mkdir -p /var/log/caddy && chown caddy:caddy /var/log/caddy
systemctl reload caddy || systemctl restart caddy

say "Резервные копии"
cp "$APP_DIR/deploy/backup.cron" /etc/cron.d/razbor-backup
chmod 644 /etc/cron.d/razbor-backup

say "Брандмауэр"
ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

say "Проверка"
sleep 3
CODE="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/ || true)"
echo "  приложение отвечает: $CODE"
systemctl is-active razbor caddy postgresql | paste -sd' ' -

cat <<TXT

Осталось сделать руками:
  1. Администратор:
     cd $APP_DIR && sudo -u $APP_USER venv/bin/python -m app.scripts.create_admin
  2. Справочник машин (марки, модели, поколения) — перенести с рабочей
     машины, см. deploy/README-deploy.md
  3. Филиалы и цены — через бэкенд на /admin
TXT
[[ -z "$DOMAIN" ]] && cat <<'TXT'

ВНИМАНИЕ: домен не указан, сайт открыт по HTTP без шифрования.
Пароли покупателей и данные заказов пойдут открытым текстом.
Так можно только смотреть; перед приёмом заказов укажите домен:
  bash deploy/bootstrap.sh --domain ваш-домен.ру
TXT
echo
