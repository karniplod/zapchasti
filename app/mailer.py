"""Отправка писем.

Своего почтового сервера нет: письма уходят через SMTP почтового ящика
(Яндекс, Mail.ru, корпоративная почта) — настройки SMTP_* в .env.
SMTP не задан — на рабочем сайте письма не уходят вовсе (enabled()
отвечает False, и всё, что от писем зависит, работает без них). На
машине разработчика (DEBUG=true) письмо кладётся файлом .eml в
data/outbox — его открывает любой почтовый клиент, а ссылку из него
можно пройти руками.
"""

import asyncio
import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import BASE_DIR, settings

log = logging.getLogger("razbor.mail")
OUTBOX = BASE_DIR / "data" / "outbox"


def smtp_ready() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def enabled() -> bool:
    """Можно ли рассчитывать на письмо: SMTP задан или это стенд с папкой."""
    return smtp_ready() or settings.debug


def _sender() -> str:
    return settings.mail_from or settings.smtp_user or "noreply@localhost"


def _build(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.app_name, _sender()))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=_sender().split("@")[-1])
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _send_sync(msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, 465, context=ctx, timeout=20) as s:
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    else:
        # 587 и прочие — обычное соединение с переходом на TLS
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)


async def send(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """True — письмо ушло (или легло в папку на стенде)."""
    msg = _build(to, subject, text, html)
    if not smtp_ready():
        if not settings.debug:
            log.warning("SMTP не настроен, письмо «%s» для %s не отправлено", subject, to)
            return False
        OUTBOX.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".eml"
        (OUTBOX / name).write_bytes(bytes(msg))
        log.info("Письмо для %s сохранено: %s", to, OUTBOX / name)
        return True
    try:
        # smtplib блокирующий — в отдельном потоке, чтобы не держать сервер
        await asyncio.to_thread(_send_sync, msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.warning("Письмо для %s не ушло: %s", to, e)
        return False
