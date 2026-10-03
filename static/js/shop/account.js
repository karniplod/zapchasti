// Скрипт разделов кабинета: templates/shop/account.html (заказы),
// account_purchased.html (купленные), account_searches.html (поиск).

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3000); }

// ── История поиска ──────────────────────────────────────────
const clear = $('clearHist');
if (clear) clear.onclick = async () => {
  // Спрашиваем прямо: действие необратимое, а кнопка стоит у самого списка
  // Без переносов внутри литерала: в шаблоне их слишком легко получить
  // настоящими, и тогда весь скрипт перестаёт разбираться
  const question = 'Записи останутся в нашей статистике спроса, но уже '
      + 'без связи с вами, и вернуть их в кабинет будет нельзя.';
  if (!await askConfirm(question, {title: 'Очистить историю поиска?', ok: 'Очистить',
                                   danger: true})) return;

  clear.disabled = true;
  try {
    const r = await fetch('/api/account/searches', {method: 'DELETE'});
    if (r.ok){ location.reload(); return; }
    toast('Не получилось очистить');
  } catch { toast('Нет связи с сервером'); }
  clear.disabled = false;
};

// ── Повторить заказ ─────────────────────────────────────────
// Детали штучные: в корзину ложится то, что ещё продаётся, — и мы
// говорим, чего уже нет, прежде чем отправить в корзину
document.querySelectorAll('.repeat').forEach(b => b.onclick = async () => {
  b.disabled = true;
  try {
    const r = await fetch(`/api/account/orders/${b.dataset.number}/repeat`, {method: 'POST'});
    const d = await r.json().catch(() => ({}));
    if (!r.ok){ toast(d.detail || 'Не получилось'); b.disabled = false; return; }
    if (!d.added){ toast('Из этого заказа сейчас ничего нет в продаже'); b.disabled = false; return; }
    if (d.missing.length && !await askConfirm(
        `Уже проданы: ${d.missing.join(', ')}. Остальное — в корзине.`,
        {title: 'Не всё есть в наличии', ok: 'Перейти в корзину', cancel: 'Остаться'})){
      b.disabled = false; return;
    }
    location.href = '/cart';
  } catch { toast('Нет связи с сервером'); b.disabled = false; }
});

// ── Купленные товары ────────────────────────────────────────
const purQ = $('purQ');
if (purQ) purQ.addEventListener('input', () => {
  const q = purQ.value.trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll('#purList li').forEach(li => {
    const ok = !q || li.dataset.text.includes(q);
    li.hidden = !ok; shown += ok;
  });
  $('purNone').hidden = !!shown;
});
document.querySelectorAll('#purList .buy').forEach(b => b.onclick = async () => {
  if (b.dataset.added){ location.href = '/cart'; return; }
  b.disabled = true;
  try {
    const r = await fetch('/api/cart', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify({sku: b.dataset.sku, qty: 1})});
    const d = await r.json().catch(() => ({}));
    if (r.ok){
      b.dataset.added = '1'; b.textContent = 'В корзине →';
      const n = document.getElementById('cartN');
      if (n){ n.textContent = d.count; n.hidden = false; }
      toast('Деталь в корзине');
    } else toast(d.detail || 'Не получилось добавить');
  } catch { toast('Нет связи с сервером'); }
  b.disabled = false;
});
