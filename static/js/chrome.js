// Скрипт шаблона templates/_base.html.

// Ряд с поиском возвращается сверху, когда страница его прокрутила:
// пока он на своём месте — обычная часть страницы, ушёл за верхний
// край — становится плавающим и проявляется (вид и анимация —
// chrome.css). Следим за гнездом, а не за самой прокруткой:
// обработчик scroll срабатывал бы десятки раз в секунду, наблюдатель —
// только на границе. Без наблюдателя ряд просто уезжает со страницей:
// поиск и корзина остаются доступны в начале страницы.
(function(){
  const slot = document.querySelector('.head-slot');
  const head = slot && slot.querySelector('.site-head');
  if (!head || !('IntersectionObserver' in window)) return;
  new IntersectionObserver(([e]) => {
    head.classList.toggle('stuck', !e.isIntersecting && e.boundingClientRect.top < 0);
  }).observe(slot);
})();

(function(){
  // Одно поведение на витрине и в бэкенде: кнопка переключает список
  const burger = document.querySelector('.burger');
  const menu = document.querySelector('.site-nav') || document.querySelector('.nav ul');
  if (!burger || !menu) return;

  const mobile = () => window.matchMedia('(max-width:760px)').matches;
  const setOpen = open => {
    menu.hidden = mobile() ? !open : false;
    burger.setAttribute('aria-expanded', String(open));
  };

  setOpen(false);
  burger.onclick = () => setOpen(menu.hidden);

  // Меню закрывается по выбору пункта и при возврате к широкому экрану
  menu.addEventListener('click', e => { if (e.target.tagName === 'A') setOpen(false); });
  window.addEventListener('resize', () => { if (!mobile()) setOpen(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
})();

// Шапка досчитывает себя сама: страница отдаётся одинаковой всем,
// поэтому её можно кешировать, а личное подставляется после загрузки
fetch('/api/me').then(r => r.json()).then(d => {
  if (d.cart){ const n = document.getElementById('cartN');
               n.textContent = d.cart; n.hidden = false; }
  if (d.authorized){ const a = document.getElementById('acctLink');
                     a.href = '/account'; a.textContent = d.name || 'Кабинет';
                     acctPop(a); }
}).catch(() => {});

// Окно под именем покупателя: баллы, последний вход — когда и с какого
// устройства, «Перейти в профиль». Данные — при каждом открытии
// (/api/account/brief). Ctrl/⌘+клик открывает кабинет как ссылку
function acctPop(link){
  const box = document.getElementById('acctPop');
  if (!box) return;
  link.setAttribute('aria-haspopup', 'dialog');
  link.setAttribute('aria-expanded', 'false');
  link.setAttribute('aria-controls', 'acctPop');
  const esc = s => String(s ?? '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  // Время — в часовом поясе покупателя: сервер отдаёт его с поясом
  const when = iso => {
    const d = new Date(iso), now = new Date();
    const day = d.toDateString() === now.toDateString() ? 'сегодня'
      : d.toDateString() === new Date(now - 864e5).toDateString() ? 'вчера'
      : d.toLocaleDateString('ru', {day: 'numeric', month: 'long', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric'});
    return `${day} в ${d.toLocaleTimeString('ru', {hour: '2-digit', minute: '2-digit'})}`;
  };
  const close = () => { box.hidden = true; link.setAttribute('aria-expanded', 'false'); };
  const draw = d => {
    const initial = (d.name || d.contact || '?').trim()[0].toUpperCase();
    const last = d.previous || d.current;
    box.innerHTML = `
      <div class="ap-head"><span class="ap-ava" aria-hidden="true">${esc(initial)}</span>
        <span class="ap-who"><b>${esc(d.name || 'Покупатель')}</b>${d.contact ? `<small>${esc(d.contact)}</small>` : ''}</span>
        <button type="button" class="mc-x" aria-label="Закрыть">×</button></div>
      <a class="ap-bonus" href="/account/bonus"><span>Баллы</span><b>${(+d.balance).toLocaleString('ru')}</b></a>
      <dl class="ap-facts">
        ${last ? `<dt>${d.previous ? 'Последний вход' : 'Вход'}</dt>
          <dd>${esc(when(last.at))}<small>${esc(last.device)}</small></dd>` : ''}
        ${d.previous && d.current ? `<dt>Сейчас</dt>
          <dd>${esc(d.current.device)}<small>вход ${esc(when(d.current.at))}</small></dd>` : ''}
      </dl>
      ${d.previous ? '<p class="ap-note">Не узнаёте вход? Смените пароль в профиле.</p>' : ''}
      <a class="btn btn-primary" href="/account/profile">Перейти в профиль</a>
      <div class="ap-links"><a href="/account">Мои заказы</a><a href="/account/logout">Выйти</a></div>`;
  };
  link.addEventListener('click', async e => {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    if (!box.hidden){ close(); return; }
    box.hidden = false;
    link.setAttribute('aria-expanded', 'true');
    box.style.top = innerWidth <= 640 ? Math.round(link.getBoundingClientRect().bottom + 6) + 'px' : '';
    box.innerHTML = '<p class="mc-empty">Загружаем…</p>';
    try {
      const r = await fetch('/api/account/brief', {cache: 'no-store'});
      if (r.status === 401){ location.href = '/account/login'; return; }
      draw(await r.json());
    } catch { box.innerHTML = '<p class="mc-empty">Нет связи с сервером</p>'; }
  });
  box.addEventListener('click', e => { if (e.target.closest('.mc-x')){ close(); link.focus(); } });
  document.addEventListener('click', e => { if (!box.hidden && !e.target.closest('.acct-wrap')) close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !box.hidden){ close(); link.focus(); } });
}

// Краткая корзина под значком: что лежит, сколько стоит, «Оформить заказ».
// Состав — с сервера при каждом открытии (/api/cart?full=1): корзина
// меняется и на других вкладках. Ctrl/⌘+клик и средняя кнопка открывают
// /cart как обычная ссылка; на самой странице корзины значок — ссылка
(function(){
  const link = document.querySelector('.cart-link');
  const box = document.getElementById('miniCart');
  if (!link || !box || location.pathname === '/cart') return;
  const SHOW = 4;
  const rub = v => Math.round(+v).toLocaleString('ru') + ' ₽';
  const esc = s => String(s ?? '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const plural = (n, a, b, c) => {
    const x = n % 10, y = n % 100;
    return x === 1 && y !== 11 ? a : x >= 2 && x <= 4 && (y < 12 || y > 14) ? b : c;
  };
  const badge = n => {
    const el = document.getElementById('cartN');
    el.textContent = n; el.hidden = !n;
  };

  const open = () => !box.hidden;
  function close(){
    box.hidden = true;
    link.setAttribute('aria-expanded', 'false');
  }

  async function load(){
    box.innerHTML = '<p class="mc-empty">Загружаем корзину…</p>';
    try {
      const d = await (await fetch('/api/cart?full=1')).json();
      draw(d);
    } catch {
      box.innerHTML = '<p class="mc-empty">Нет связи с сервером</p>';
    }
  }

  function draw(d){
    badge(d.count);
    const items = d.items || [];
    if (!items.length){
      box.innerHTML = `<div class="mc-empty">
          <b>В корзине пока пусто</b>
          <span>Найдите деталь по названию, номеру или VIN</span>
          <a class="btn btn-ghost btn-sm" href="/catalog">Перейти в каталог</a>
        </div>`;
      return;
    }
    const gone = items.filter(i => !i.available).length;
    box.innerHTML = `
      <div class="mc-head"><b>Корзина</b>
        <span>${d.count} ${plural(d.count, 'деталь', 'детали', 'деталей')}</span>
        <button type="button" class="mc-x" aria-label="Закрыть">×</button></div>
      <ul class="mc-list">${items.slice(0, SHOW).map(i => `
        <li class="${i.available ? '' : 'is-gone'}">
          <a class="mc-pic" href="/p/${encodeURIComponent(i.sku)}">${i.photo ? `<img src="${esc(i.photo)}" alt="" loading="lazy">` : ''}</a>
          <span class="mc-nm"><a href="/p/${encodeURIComponent(i.sku)}">${esc(i.name)}</a>
            <small>${i.available
              ? `${i.qty > 1 ? i.qty + ' шт × ' : ''}${i.price ? rub(i.price) : 'цена по запросу'}${i.short ? ' · осталось меньше' : ''}`
              : 'нет в наличии'}</small></span>
          <b class="mc-sum">${i.available && i.price ? rub(i.price * i.qty) : ''}</b>
          <button type="button" class="mc-drop" data-id="${i.part_id}" aria-label="Убрать ${esc(i.name)}">×</button>
        </li>`).join('')}</ul>
      ${items.length > SHOW ? `<a class="mc-more" href="/cart">и ещё ${items.length - SHOW} ${plural(items.length - SHOW, 'деталь', 'детали', 'деталей')} →</a>` : ''}
      ${gone ? `<p class="mc-note">${gone} ${plural(gone, 'деталь', 'детали', 'деталей')} уже нет в наличии — уберите из корзины</p>` : ''}
      <div class="mc-total"><span>Товары</span><b>${rub(d.total)}</b></div>
      <p class="mc-hint">Доставку посчитаем при оформлении</p>
      <div class="mc-btns">
        <a class="btn btn-primary" href="/cart">Оформить заказ</a>
        <button type="button" class="btn btn-ghost mc-close">Продолжить покупки</button>
      </div>`;
  }

  link.addEventListener('click', e => {
    // Ctrl/⌘/Shift+клик — обычная ссылка: открыть корзину в новой вкладке
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    if (open()){ close(); return; }
    box.hidden = false;
    // На телефоне панель — во всю ширину сразу под шапкой: шапка то
    // прилипает к верху, то нет, поэтому место считаем при открытии
    box.style.top = innerWidth <= 640 ? Math.round(link.getBoundingClientRect().bottom + 10) + 'px' : '';
    link.setAttribute('aria-expanded', 'true');
    load();
  });

  box.addEventListener('click', async e => {
    if (e.target.closest('.mc-x, .mc-close')){ close(); link.focus(); return; }
    const drop = e.target.closest('.mc-drop');
    if (!drop) return;
    drop.disabled = true;
    try {
      const r = await fetch('/api/cart/' + drop.dataset.id, {method: 'DELETE'});
      if (r.ok){ load(); return; }
    } catch {}
    drop.disabled = false;
  });

  document.addEventListener('click', e => {
    if (open() && !e.target.closest('.cart-wrap')) close();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && open()){ close(); link.focus(); }
  });
})();

// Логотип необязателен: файла нет — убираем картинку, остаётся надпись.
// Раньше это делал onerror="this.remove()" прямо в разметке. Картинка
// могла сломаться ещё до этого скрипта — тогда её ловит проверка
// complete/naturalWidth, иначе — обработчик error
document.querySelectorAll('.brand img').forEach(img => {
  if (img.complete && !img.naturalWidth) img.remove();
  else img.addEventListener('error', () => img.remove());
});

// Поиск в шапке. Строка, похожая на VIN (17 знаков без I, O, Q), ведёт
// в подбор по VIN — там каталог сам определит машину. Всё остальное —
// обычный поиск деталей. Без скрипта форма уходит в каталог GET-ом с ?q=.
(function(){
  const form = document.getElementById('headSearch');
  const input = document.getElementById('headQ');
  if (!form || !input) return;

  // В каталоге строка показывает то, что сейчас ищут
  const params = new URLSearchParams(location.search);
  if (location.pathname === '/catalog') input.value = params.get('q') || params.get('vin') || '';

  form.addEventListener('submit', e => {
    const raw = input.value.trim();
    if (!raw){ e.preventDefault(); input.focus(); return; }
    const vin = raw.replace(/[\s-]/g, '').toUpperCase();
    if (/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)){
      e.preventDefault();
      location.href = '/catalog?vin=' + encodeURIComponent(vin);
    }
  });
})();

// Меню «Все категории»: узлы запчастей из /api/catalog/nodes — те же,
// что в фильтре «Узел» каталога. Данные грузятся один раз, уже при
// наведении на кнопку: к клику меню обычно готово. Без скрипта кнопка
// остаётся ссылкой в каталог.
(function(){
  const btn = document.getElementById('catBtn');
  const menu = document.getElementById('catMenu');
  if (!btn || !menu) return;

  // Под узлом — две самые заполненные категории (сервер отдаёт их по
  // убыванию числа деталей), остальные раскрывает кнопка «ещё N»
  const SHOW = 2;
  let data = null, loading = null;

  // Названия приходят из базы — в разметку только экранированными
  const esc = s => String(s).replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const link = (id, name, cnt, cls) =>
    `<a${cls ? ` class="${cls}"` : ''} href="/catalog?category=${id}">` +
    `${esc(name)}<span>${cnt}</span></a>`;
  const plural = n => {
    const a = n % 10, b = n % 100;
    if (a === 1 && b !== 11) return 'деталь';
    if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return 'детали';
    return 'деталей';
  };

  function load(){
    if (!loading){
      loading = fetch('/api/catalog/nodes')
        .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(d => (data = d))
        // Сбой не запоминаем: следующее открытие попробует снова
        .catch(e => { loading = null; throw e; });
    }
    return loading;
  }

  function render(){
    if (!data.nodes.length){
      menu.innerHTML = '<p class="cm-state">Каталог пока пуст — детали появятся ' +
                       'после разбора первых машин.</p>';
      return;
    }
    menu.innerHTML = '<div class="cm-grid">' + data.nodes.map(n => {
      // Узел автомобиля без деталей есть в меню всегда — это карта того,
      // что бывает на разборе, — но без ссылки: в пустую выдачу не ведём
      if (!n.cnt) return '<section class="cm-node cm-empty">' +
        `<span class="cm-title">${esc(n.name)}</span>` +
        '<span class="cm-none">пока нет в наличии</span></section>';
      const items = n.items.slice(0, SHOW).map(i => link(i.id, i.name, i.cnt)).join('');
      const rest = n.items.slice(SHOW);
      const more = rest.length
        ? `<div class="cm-rest" id="cmRest${n.id}" hidden>` +
            rest.map(i => link(i.id, i.name, i.cnt)).join('') + '</div>' +
          `<button type="button" class="cm-more" aria-expanded="false" ` +
            `aria-controls="cmRest${n.id}" data-n="${rest.length}">ещё ${rest.length}</button>`
        : '';
      return `<section class="cm-node">${link(n.id, n.name, n.cnt, 'cm-title')}${items}${more}</section>`;
    }).join('') + '</div>' +
      `<div class="cm-foot"><span>В наличии ${data.total} ${plural(data.total)}</span>` +
      '<a href="/catalog">Весь каталог</a></div>';
  }

  const setOpen = open => {
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
  };

  async function open(){
    setOpen(true);
    if (data){ render(); return; }
    menu.innerHTML = '<p class="cm-state">Загружаю категории…</p>';
    try {
      await load();
      if (!menu.hidden) render();
    } catch {
      menu.innerHTML = '<p class="cm-state">Не удалось загрузить категории. ' +
                       '<a href="/catalog">Открыть каталог</a></p>';
    }
  }

  // «Ещё N» раскрывает остальные категории узла прямо в меню — и сворачивает
  menu.addEventListener('click', e => {
    const more = e.target.closest('.cm-more');
    if (!more) return;
    const rest = document.getElementById(more.getAttribute('aria-controls'));
    const open = rest.hidden;
    rest.hidden = !open;
    more.setAttribute('aria-expanded', String(open));
    more.textContent = open ? 'свернуть' : `ещё ${more.dataset.n}`;
  });

  btn.addEventListener('pointerenter', () => load().catch(() => {}), {once: true});
  btn.addEventListener('click', e => {
    e.preventDefault();
    if (menu.hidden) open(); else setOpen(false);
  });
  // Закрывается кликом мимо и по Escape — фокус возвращается на кнопку
  document.addEventListener('click', e => {
    if (!menu.hidden && !menu.contains(e.target) && !btn.contains(e.target)) setOpen(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !menu.hidden){ setOpen(false); btn.focus(); }
  });
})();

// Кнопка «в корзину» на плитке детали. Обработчик один на страницу и
// слушает документ: плитки есть на главной, в каталоге и на странице
// машины, а в каталоге они ещё и перерисовываются при каждом фильтре —
// вешать обработчик на каждую пришлось бы заново после каждой выдачи.
// Деталь штучная: добавили — кнопка ведёт в корзину, а не добавляет
// второй раз. Счётчик в шапке обновляется тем же ответом.
document.addEventListener('click', async e => {
  const b = e.target.closest('.buy-card');
  if (!b) return;
  if (b.dataset.added){ location.href = '/cart'; return; }
  b.disabled = true;
  try {
    const r = await fetch('/api/cart', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sku: b.dataset.sku})});
    const d = await r.json().catch(() => ({}));
    if (r.ok){
      b.dataset.added = '1';
      b.textContent = 'В корзине — перейти';
      b.disabled = false;
      const n = document.getElementById('cartN');
      if (n){ n.textContent = d.count; n.hidden = false; }
      return;
    }
    // 409 — деталь уже забрали, 404 — сняли с продажи: кнопка говорит
    // об этом на месте, уводить человека со страницы незачем
    b.textContent = d.detail || 'Не получилось';
    return;
  } catch { b.textContent = 'Нет связи'; }
  b.disabled = false;
});

// Плитка без дыр: показываем ровно столько карточек, сколько заполняет
// целые ряды, — на 1290 это четыре в ряд, на ноутбуке три, на телефоне
// две. Сколько колонок, берём у самой сетки (её раскладка), а не
// считаем формулой — так совпадёт с тем, что нарисовал браузер.
// Лишние прячутся атрибутом hidden: не ловят фокус, не читаются вслух.
//   fillRows(grid, 2) — не больше двух полных рядов
window.fillRows = function(grid, rows){
  const apply = () => {
    const items = [...grid.children];
    items.forEach(el => { el.hidden = false; });
    // У auto-fit пустые колонки схлопнуты в 0px, но в списке они есть —
    // считаем все: это столько, сколько карточек помещается в ряд
    const cols = getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length || 1;
    const full = Math.min(rows, Math.floor(items.length / cols));
    const show = full ? full * cols : items.length;
    items.forEach((el, i) => { el.hidden = i >= show; });
  };
  apply();
  if (!grid.dataset.fillRows){
    grid.dataset.fillRows = rows;
    let t;
    addEventListener('resize', () => { clearTimeout(t); t = setTimeout(apply, 120); });
  }
  grid.dataset.fillRows = rows;
};

// Выбор количества «− N +» — на карточке детали и в корзине. Число не
// выходит за 1…max; onChange получает новое значение (после ввода руками —
// когда человек ушёл с поля или нажал Enter)
window.qtyStepper = function(box, onChange){
  const input = box.querySelector('.qty-in');
  const max = +box.dataset.max || 999;
  const clamp = v => Math.max(1, Math.min(max, Math.round(+v) || 1));
  const sync = () => {
    box.querySelector('[data-d="-1"]').disabled = +input.value <= 1;
    box.querySelector('[data-d="1"]').disabled = +input.value >= max;
  };
  const set = v => {
    const n = clamp(v), changed = n !== +input.dataset.last;
    input.value = n; input.dataset.last = n; sync();
    if (changed && onChange) onChange(n);
  };
  input.dataset.last = input.value;
  box.querySelectorAll('.qty-btn').forEach(b => b.onclick = () => set(+input.value + +b.dataset.d));
  input.addEventListener('change', () => set(input.value));
  input.addEventListener('keydown', e => { if (e.key === 'Enter'){ e.preventDefault(); set(input.value); } });
  sync();
  return {get: () => clamp(input.value), set};
};
