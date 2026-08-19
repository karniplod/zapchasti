cd /opt/razbor        # подставьте свой путь к проекту

# 1. Разложить новые файлы (замените ~/Downloads на свой путь)
cp ~/Downloads/admin.py            app/routers/admin.py
cp ~/Downloads/dashboard.html      templates/admin/dashboard.html
cp ~/Downloads/seed_categories.py  app/scripts/seed_categories.py

# 2. Подключить роутер в main.py
python3 - <<'PY'
from pathlib import Path
p = Path("app/main.py"); s = p.read_text()

if "admin" in s.split("from .routers import")[1].split("\n")[0]:
    print("уже подключён, ничего не меняю")
else:
    s = s.replace(
        "from .routers import catalog, dismantle, intake, reference",
        "from .routers import admin, catalog, dismantle, intake, reference")
    s = s.replace(
        "app.include_router(reference.router)",
        "app.include_router(reference.router)\napp.include_router(admin.router)")
    p.write_text(s)
    print("подключено")
PY

# 3. Проверить, что получилось
grep -n "routers import\|include_router" app/main.py
./venv/bin/python -m py_compile app/routers/admin.py app/main.py && echo "синтаксис ок"
