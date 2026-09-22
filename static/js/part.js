// Скрипт шаблона templates/part.html.

const $ = id => document.getElementById(id);

document.querySelectorAll('.strip button').forEach(b => b.onclick = () => {
  $('bigImg').src = b.dataset.src;
  document.querySelectorAll('.strip button').forEach(x => x.classList.toggle('on', x === b));
});

// Просмотр с увеличением. Открывается тем снимком, который показан
// сейчас, — иначе после переключения миниатюры увеличивалось бы не то
(function(){
  const viewer = $('viewer'), big = $('bigImg'), img = $('viewerImg');
  if (!viewer || !big || !img) return;

  const MIN = 1, MAX = 6;
  let scale = 1, tx = 0, ty = 0;

  // Снимок центрирован через top/left 50%, поэтому свой сдвиг на половину
  // размера входит в ту же трансформацию — иначе центр «уезжает»
  const apply = () => {
    img.style.transform =
      `translate(-50%, -50%) translate(${tx}px, ${ty}px) scale(${scale})`;
    $('zoomLvl').textContent = Math.round(scale * 100) + '%';
    viewer.classList.toggle('zoomed', scale > 1);
  };

  const reset = () => { scale = 1; tx = ty = 0; apply(); };

  // Масштабируем вокруг точки под курсором: она должна остаться на месте
  const zoomAt = (factor, cx, cy) => {
    const next = Math.min(MAX, Math.max(MIN, scale * factor));
    if (next === scale) return;
    const r = viewer.getBoundingClientRect();
    const dx = cx - (r.left + r.width / 2) - tx;
    const dy = cy - (r.top + r.height / 2) - ty;
    const k = next / scale;
    tx -= dx * (k - 1);
    ty -= dy * (k - 1);
    scale = next;
    if (scale === 1) { tx = ty = 0; }
    apply();
  };

  const open = () => {
    img.src = big.src;
    reset();
    viewer.classList.add('open');
    document.body.classList.add('no-scroll');   // фон не должен прокручиваться
  };
  const close = () => {
    viewer.classList.remove('open');
    document.body.classList.remove('no-scroll');
  };

  big.onclick = open;
  $('zoomBtn').onclick = e => { e.stopPropagation(); open(); };
  $('viewerClose').onclick = close;
  $('zoomIn').onclick = () => zoomAt(1.5, innerWidth / 2, innerHeight / 2);
  $('zoomOut').onclick = () => zoomAt(1 / 1.5, innerWidth / 2, innerHeight / 2);

  // После перетаскивания браузер всё равно шлёт click. Без этой отметки
  // сдвиг снимка заканчивался бы сбросом масштаба
  let dragged = false;

  // Клик мимо снимка закрывает; по самому снимку — приближает,
  // а если уже приближено, возвращает к исходному
  viewer.onclick = e => {
    if (dragged) { dragged = false; return; }
    if (e.target === img) { scale > 1 ? reset() : zoomAt(2.5, e.clientX, e.clientY); }
    else if (!e.target.closest('.zoom-ctl') && !e.target.closest('.close')) close();
  };

  viewer.addEventListener('wheel', e => {
    e.preventDefault();
    zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY);
  }, {passive: false});

  // Перетаскивание и щипок — общий обработчик на указателях,
  // поэтому мышь и палец работают одинаково
  const pts = new Map();
  let base = null;

  viewer.addEventListener('pointerdown', e => {
    if (e.target.closest('.zoom-ctl') || e.target.closest('.close')) return;
    pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
    viewer.setPointerCapture(e.pointerId);
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      base = {d: Math.hypot(a.x - b.x, a.y - b.y), scale};
    }
    viewer.classList.add('dragging');
  });

  viewer.addEventListener('pointermove', e => {
    const prev = pts.get(e.pointerId);
    if (!prev) return;
    const cur = {x: e.clientX, y: e.clientY};

    if (pts.size === 2 && base) {
      pts.set(e.pointerId, cur);
      const [a, b] = [...pts.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      const next = Math.min(MAX, Math.max(MIN, base.scale * (d / base.d)));
      zoomAt(next / scale, (a.x + b.x) / 2, (a.y + b.y) / 2);
      return;
    }

    if (scale > 1) {           // без увеличения таскать нечего
      const dx = cur.x - prev.x, dy = cur.y - prev.y;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) dragged = true;
      tx += dx;
      ty += dy;
      apply();
    }
    pts.set(e.pointerId, cur);
  });

  const release = e => {
    pts.delete(e.pointerId);
    if (pts.size < 2) base = null;
    if (!pts.size) viewer.classList.remove('dragging');
  };
  viewer.addEventListener('pointerup', release);
  viewer.addEventListener('pointercancel', release);

  document.addEventListener('keydown', e => {
    if (!viewer.classList.contains('open')) return;
    if (e.key === 'Escape') close();
    if (e.key === '+' || e.key === '=') zoomAt(1.5, innerWidth / 2, innerHeight / 2);
    if (e.key === '-') zoomAt(1 / 1.5, innerWidth / 2, innerHeight / 2);
  });
})();

// Каталожный номер копируют чаще всего — уходят сверять в другом каталоге
document.querySelectorAll('.copy').forEach(b => b.onclick = async () => {
  try { await navigator.clipboard.writeText(b.dataset.copy); toast('Номер скопирован'); }
  catch { toast('Не удалось скопировать — выделите вручную'); }
});

// Корзина. Деталь штучная, поэтому «добавить» — разовое действие:
// после него кнопка ведёт в корзину, а не добавляет второй раз
$('buy').onclick = async () => {
  if ($('buy').dataset.added){ location.href = '/cart'; return; }
  $('buy').disabled = true;
  try {
    const r = await fetch('/api/cart', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sku: $('buy').dataset.sku})});
    const d = await r.json().catch(() => ({}));
    if (r.ok){
      $('buy').dataset.added = '1';
      $('buy').textContent = 'В корзине — перейти';
      $('buy').disabled = false;
      const n = document.getElementById('cartN');
      if (n){ n.textContent = d.count; n.hidden = false; }
      toast('Деталь в корзине');
      return;
    }
    toast(d.detail || 'Не получилось добавить');
  } catch { toast('Нет связи с сервером'); }
  $('buy').disabled = false;
};

let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 2400); }
