// Скрипт шаблона templates/admin/_nav.html.
// Склеены 3 блока, стоявшие подряд, — порядок прежний.

(function(){
  // Точное совпадение адреса, иначе /parts подсветил бы и /admin
  const path = location.pathname;
  let best = null;
  document.querySelectorAll('.nav a[data-p]').forEach(a => {
    const p = a.dataset.p;
    if (path === p || path.startsWith(p + '/')) {
      if (!best || p.length > best.dataset.p.length) best = a;
    }
  });
  if (best) best.classList.add('on');
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

(function(){
  const btn = document.getElementById('acctBtn');
  const menu = document.getElementById('acctMenu');
  if (!btn || !menu) return;

  const setOpen = open => {
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
  };
  setOpen(false);

  btn.onclick = e => { e.stopPropagation(); setOpen(menu.hidden); };
  // Закрывается по клику мимо и по Escape — иначе висит поверх работы
  document.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
  menu.addEventListener('click', e => e.stopPropagation());
})();

// Логотип необязателен: файла нет — убираем картинку, остаётся надпись.
// Раньше это делал onerror="this.remove()" прямо в разметке. Картинка
// могла сломаться ещё до этого скрипта — тогда её ловит проверка
// complete/naturalWidth, иначе — обработчик error
document.querySelectorAll('.nav .logo img').forEach(img => {
  if (img.complete && !img.naturalWidth) img.remove();
  else img.addEventListener('error', () => img.remove());
});
