"""
Обработка фотографий.

Что решает этот модуль:

  1. Снимок с телефона — 4-8 МБ и 4000 px по длинной стороне. В каталоге
     нужен максимум 1600 px. Без ресайза диск VPS кончится за пару месяцев,
     а карточка детали не откроется на мобильном интернете.
  2. EXIF-ориентация. Фото, снятое вертикально, без разворота ляжет набок —
     браузер учитывает EXIF не везде.
  3. EXIF вообще. Телефон пишет в снимок GPS-координаты. Публиковать
     координаты своего склада на весь интернет незачем.
  4. iPhone по умолчанию снимает в HEIC — без отдельного декодера
     Pillow такой файл не откроет.
  5. Чужие фото. Разборки регулярно тащат снимки друг у друга на площадки.
     Водяной знак это не остановит, но сделает заметным.

Требуется:
    pip install pillow pillow-heif
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from ..config import settings

log = logging.getLogger(__name__)

# Поддержка HEIC/HEIF с айфонов
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_OK = True
except ImportError:  # pragma: no cover
    HEIC_OK = False
    log.warning("pillow-heif не установлен — фото с iPhone в HEIC приниматься не будут")

ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}

FULL_QUALITY = 82  # выше визуально не отличить, вес растёт вдвое
THUMB_QUALITY = 76
THUMB_SIDE = 400


@dataclass
class SavedImage:
    path: str  # /media/parts/12/ab34cd.webp
    thumb: str  # /media/parts/12/ab34cd_t.webp
    width: int
    height: int
    bytes: int


# ------------------------------------------------------------------
# Проверка
# ------------------------------------------------------------------


async def read_and_validate(upload: UploadFile) -> bytes:
    """Читаем в память целиком: 12 МБ на файл терпимо, зато не остаётся
    мусора на диске, если файл окажется не картинкой."""
    if upload.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, f"{upload.filename}: нужен JPEG, PNG, WebP или HEIC")
    if upload.content_type in ("image/heic", "image/heif") and not HEIC_OK:
        raise HTTPException(
            415,
            f"{upload.filename}: формат HEIC пока не поддерживается. "
            "В настройках камеры iPhone выберите «Наиболее совместимый»",
        )

    data = await upload.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"{upload.filename}: больше {settings.max_upload_mb} МБ")
    if not data:
        raise HTTPException(422, f"{upload.filename}: пустой файл")
    return data


# ------------------------------------------------------------------
# Обработка
# ------------------------------------------------------------------


def process(data: bytes, folder: Path, watermark: str | None = None) -> SavedImage:
    """Синхронная и довольно тяжёлая функция — вызывать через
    BackgroundTasks или run_in_threadpool, не в теле обработчика."""
    try:
        img = _open(data)
    except (UnidentifiedImageError, OSError):
        raise HTTPException(422, "Файл повреждён или это не изображение") from None

    # Разворот по EXIF и одновременное избавление от метаданных:
    # exif_transpose возвращает новое изображение без ориентации,
    # а сохранение без параметра exif выбрасывает и GPS
    img = ImageOps.exif_transpose(img)

    # Прозрачность в webp поддерживается, но фото деталей всегда
    # непрозрачные — переводим в RGB, экономим вес
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")

    img.thumbnail((settings.image_max_side, settings.image_max_side), Image.Resampling.LANCZOS)

    if watermark:
        img = _watermark(img, watermark)

    folder.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    full_path = folder / f"{stem}.webp"
    thumb_path = folder / f"{stem}_t.webp"

    img.save(full_path, "WEBP", quality=FULL_QUALITY, method=5)

    thumb = img.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
    thumb.save(thumb_path, "WEBP", quality=THUMB_QUALITY, method=4)

    rel = f"/media/{folder.relative_to(settings.media_root).as_posix()}"
    return SavedImage(
        path=f"{rel}/{stem}.webp",
        thumb=f"{rel}/{stem}_t.webp",
        width=img.width,
        height=img.height,
        bytes=full_path.stat().st_size,
    )


def _open(data: bytes) -> Image.Image:
    from io import BytesIO

    img = Image.open(BytesIO(data))
    img.load()  # ловим битые файлы здесь, а не при сохранении
    return img


def _watermark(img: Image.Image, text: str) -> Image.Image:
    """Полупрозрачная подпись в правом нижнем углу. Размер привязан
    к ширине изображения, чтобы на миниатюре тоже читалось."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    size = max(14, img.width // 34)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        font = ImageFont.load_default()

    box = draw.textbbox((0, 0), text, font=font)
    pad = size // 2
    x = img.width - (box[2] - box[0]) - pad
    y = img.height - (box[3] - box[1]) - pad

    # Тень для читаемости на светлом фоне
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 90))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 170))

    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


# ------------------------------------------------------------------
# Использование в роутерах
# ------------------------------------------------------------------


async def save_images(
    uploads: list[UploadFile], folder: Path, watermark: str | None = None
) -> list[SavedImage]:
    """Читает и обрабатывает пачку файлов. Обработка идёт в пуле потоков:
    Pillow держит GIL, и без этого один разборщик с восемью фото
    подвесит всё приложение."""
    from starlette.concurrency import run_in_threadpool

    results = []
    for upload in uploads:
        data = await read_and_validate(upload)
        results.append(await run_in_threadpool(process, data, folder, watermark))
    return results


# ------------------------------------------------------------------
# Пережатие того, что уже загружено
# ------------------------------------------------------------------


def reprocess_existing(root: Path | None = None, dry_run: bool = True) -> dict:
    """Разовый прогон по старым файлам, если что-то успели залить
    в оригинале:

        python -c "from app.services.images import reprocess_existing; \\
                   print(reprocess_existing(dry_run=False))"
    """
    root = root or settings.media_root
    stats = {"files": 0, "before": 0, "after": 0, "errors": 0}

    for path in root.rglob("*"):
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        stats["files"] += 1
        stats["before"] += path.stat().st_size
        if dry_run:
            continue
        try:
            saved = process(path.read_bytes(), path.parent)
            stats["after"] += saved.bytes
            path.unlink()
        except Exception:
            log.exception("Не смог пережать %s", path)
            stats["errors"] += 1

    stats["saved_mb"] = round((stats["before"] - stats["after"]) / 1024 / 1024, 1)
    return stats
