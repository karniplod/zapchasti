// Скрипт шаблона templates/_base.html.
// Склеены 3 блока, стоявшие подряд, — порядок прежний.

(function(){
  // Шапка «вплавлена» в страницу, пока та в самом верху, и отделяется
  // при прокрутке. Вид обоих состояний — в chrome.css, здесь только
  // переключение.
  const head = document.querySelector('.site-head');
  if (!head) return;

  // Без наблюдателя следить нечем — шапка остаётся отделённой, как была
  if (!('IntersectionObserver' in window)) return;

  // Скрипт стоит сразу после шапки и выполняется до первой отрисовки:
  // верное состояние видно с первого кадра, без мигания линии
  head.classList.toggle('at-top', window.scrollY < 1);

  // Следим не за scroll, а за пиксельной меткой в самом верху документа:
  // обработчик прокрутки срабатывал бы десятки раз в секунду, а
  // наблюдатель — только когда метка уходит с экрана или возвращается
  {
    const mark = document.createElement('div');
    mark.setAttribute('aria-hidden', 'true');
    mark.className = 'head-mark';
    document.body.prepend(mark);
    new IntersectionObserver(([e]) => head.classList.toggle('at-top', e.isIntersecting))
      .observe(mark);
  }

  // Переходы — только после того, как начальное состояние нарисовано
  requestAnimationFrame(() => requestAnimationFrame(() => head.classList.add('ready')));
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
                     a.href = '/account'; a.textContent = d.name || 'Кабинет'; }
}).catch(() => {});

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

  const SHOW = 5;   // деталей под узлом; остальное — ссылкой «ещё N» на узел
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
      const items = n.items.slice(0, SHOW).map(i => link(i.id, i.name, i.cnt)).join('');
      const more = n.items.length > SHOW
        ? `<a class="cm-more" href="/catalog?category=${n.id}">ещё ${n.items.length - SHOW}</a>`
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
