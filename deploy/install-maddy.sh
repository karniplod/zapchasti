#!/usr/bin/env bash
# Установка maddy — почтового сервера на отправку (подтверждение email).
#
#   sudo bash /opt/razbor/deploy/install-maddy.sh
#
# Повторный запуск безопасен: бинарник и конфиг обновятся, учётка
# отправителя и её пароль в .env останутся прежними. В конце печатает
# записи DNS, без которых письма уйдут в спам.
set -euo pipefail

VERSION=0.9.5
DOMAIN=avt.vpn-x.fun
SENDER=noreply@$DOMAIN
APP_DIR=/opt/razbor
ENV=$APP_DIR/.env

command -v zstd >/dev/null || apt-get install -y -qq zstd >/dev/null

# ── Бинарник и служба ───────────────────────────────────────────────
if ! command -v maddy >/dev/null || ! maddy version 2>/dev/null | grep -q "$VERSION"; then
  tmp=$(mktemp -d)
  curl -fsSL "https://github.com/foxcpp/maddy/releases/download/v$VERSION/maddy-$VERSION-x86_64-linux-musl.tar.zst" \
    -o "$tmp/maddy.tar.zst"
  tar --zstd -xf "$tmp/maddy.tar.zst" -C "$tmp"
  install -m 755 "$tmp/maddy-$VERSION-x86_64-linux-musl/maddy" /usr/local/bin/maddy
  install -m 644 "$tmp/maddy-$VERSION-x86_64-linux-musl/systemd/maddy.service" \
    /etc/systemd/system/maddy.service
  rm -rf "$tmp"
fi
id maddy >/dev/null 2>&1 || useradd -r -U -s /usr/sbin/nologin -d /var/lib/maddy maddy
install -d -o maddy -g maddy -m 750 /var/lib/maddy
install -d -m 755 /etc/maddy
install -m 644 "$APP_DIR/deploy/maddy.conf" /etc/maddy/maddy.conf
systemctl daemon-reload
systemctl enable --quiet maddy
systemctl restart maddy
sleep 2
systemctl is-active --quiet maddy || { journalctl -u maddy -n 30 --no-pager; exit 1; }

# ── Учётка отправителя и настройки сайта ────────────────────────────
mcli(){ sudo -u maddy maddy -config /etc/maddy/maddy.conf "$@"; }
if ! mcli creds list 2>/dev/null | grep -qx "$SENDER"; then
  PASS=$(openssl rand -hex 24)
  mcli creds create --password "$PASS" "$SENDER" >/dev/null
  # Настройки почты в .env сайта: старые строки убираем, новые дописываем
  sed -i -E '/^(SMTP_HOST|SMTP_PORT|SMTP_USER|SMTP_PASSWORD|SMTP_SECURITY|MAIL_FROM)=/d' "$ENV"
  cat >> "$ENV" <<CONF
SMTP_HOST=127.0.0.1
SMTP_PORT=587
SMTP_USER=$SENDER
SMTP_PASSWORD=$PASS
SMTP_SECURITY=none
MAIL_FROM=$SENDER
CONF
  chown razbor:razbor "$ENV"; chmod 600 "$ENV"
  echo "== учётка $SENDER создана, настройки записаны в $ENV"
  systemctl restart razbor
else
  echo "== учётка $SENDER уже есть — .env не трогаю"
fi
# Ящик postmaster — для отчётов о недоставке
mcli imap-acct list 2>/dev/null | grep -qx "postmaster@$DOMAIN" \
  || mcli imap-acct create "postmaster@$DOMAIN" >/dev/null

# ── Что прописать в DNS ─────────────────────────────────────────────
KEY=/var/lib/maddy/dkim_keys/${DOMAIN}_default.dns
IP=$(hostname -I | awk '{print $1}')
echo
echo "== Записи DNS для $DOMAIN (панель регистратора):"
echo "   TXT  $DOMAIN                  v=spf1 ip4:$IP include:beget.com ~all"
[ -f "$KEY" ] && echo "   TXT  default._domainkey.$DOMAIN  $(cat "$KEY")" \
              || echo "   DKIM-ключ появится после первого письма: $KEY"
echo "   TXT  _dmarc.$DOMAIN           v=DMARC1; p=none; rua=mailto:postmaster@$DOMAIN"
echo "== У хостера: обратная запись PTR для $IP → $DOMAIN"
