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
