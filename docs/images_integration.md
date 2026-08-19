# Подключение обработки фото

## 1. Схема

```sql
-- Миниатюра и размеры. Размеры нужны, чтобы каталог не «прыгал»
-- при загрузке картинок: браузер резервирует место заранее.
ALTER TABLE part_photos
    ADD COLUMN thumb  text,
    ADD COLUMN width  int,
    ADD COLUMN height int;

ALTER TABLE donor_photos
    ADD COLUMN thumb  text,
    ADD COLUMN width  int,
    ADD COLUMN height int;
```

## 2. Настройки

```ini
# .env
IMAGE_MAX_SIDE=1600
THUMB_SIDE=400
MAX_UPLOAD_MB=12
WATERMARK=razbor.example.ru      # пусто = без водяного знака
```

Добавить в `config.py`:

```python
watermark: str = ""
```

## 3. Правка `dismantle.py`

Было:

```python
folder = MEDIA_ROOT / str(part_id)
saved = []
for order, upload in enumerate(files):
    fname = save_upload(upload, folder)
    rel = f"/media/parts/{part_id}/{fname}"
    await session.execute(text("""
        INSERT INTO part_photos (part_id, path, sort_order) VALUES (:p, :path, :o)
    """), {"p": part_id, "path": rel, "o": order})
    saved.append(rel)
```

Стало:

```python
from ..services.images import save_images

folder = MEDIA_ROOT / str(part_id)
images = await save_images(files, folder, settings.watermark or None)
saved = []
for order, img in enumerate(images):
    await session.execute(text("""
        INSERT INTO part_photos (part_id, path, thumb, width, height, sort_order)
        VALUES (:p, :path, :thumb, :w, :h, :o)
    """), {"p": part_id, "path": img.path, "thumb": img.thumb,
           "w": img.width, "h": img.height, "o": order})
    saved.append(img.thumb)
```

Функцию `save_upload` из `dismantle.py` удалить — она больше не нужна,
как и константы `ALLOWED_IMAGE_TYPES`, `EXT_BY_TYPE`, `MAX_PHOTO_BYTES`.

## 4. Правка `intake.py`

Так же, но **без водяного знака**: фото приёмки внутренние, на сайт
не попадают, а знак мешает разглядеть повреждения кузова.

```python
images = await save_images(files, folder)
```

## 5. Каталог отдаёт миниатюры

В `catalog.py` в выдаче списка заменить `path` на `thumb`:

```sql
(SELECT COALESCE(thumb, path) FROM part_photos ph
  WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo
```

`COALESCE` — на случай старых записей без миниатюры.

Карточка детали (`part.html`) показывает полный размер, а в ленте
превью — миниатюры:

```python
photos = [dict(r._mapping) for r in await session.execute(text("""
    SELECT path, thumb, width, height FROM part_photos
     WHERE part_id = :id ORDER BY sort_order
"""), {"id": part.id})]
```

## 6. Порядок фото не случаен

Первое фото становится обложкой в каталоге и уходит в фид на площадки.
Разборщику стоит объяснить одно правило: **первым снимается общий вид
детали, а не дефект.** Иначе в выдаче Авито ваш товар выглядит как хлам.

## 7. Разовое пережатие

Если что-то уже успели залить в оригинале:

```bash
# Сначала посмотреть объём
python -c "from app.services.images import reprocess_existing; print(reprocess_existing())"

# Потом пережать
python -c "from app.services.images import reprocess_existing; print(reprocess_existing(dry_run=False))"
```

Скрипт удаляет исходники после успешной конвертации, поэтому
**перед запуском сделайте копию `media/`**.

## 8. Кеш Caddy

Файлы теперь имеют случайные имена и никогда не меняются —
кеш можно ставить агрессивный, он уже прописан в Caddyfile:

```caddy
header Cache-Control "public, max-age=2592000, immutable"
```

## Ожидаемый результат

| | Было | Стало |
|---|---|---|
| Фото детали | 4–8 МБ, 4000 px | 150–300 КБ, 1600 px |
| Миниатюра | нет | 15–30 КБ |
| Машина на 40 деталей, 3 фото | ~700 МБ | ~30 МБ |
| GPS склада в метаданных | есть | нет |
```
