// Страница «Машины». Выбор марки списком: со скриптом страница
// уходит сразу по выбору, поэтому кнопка «Показать» лишняя. Без
// скрипта она остаётся и форма работает обычной отправкой.
(function(){
  const sel = document.getElementById('brandPick');
  const go = document.getElementById('brandGo');
  if (!sel || !go) return;
  go.hidden = true;
  sel.addEventListener('change', () => sel.form.submit());
})();

// «Показать ещё»: подтягиваем следующие 20 плиток и вставляем в конец
// списка. Без скрипта эта же кнопка — обычная ссылка с n=… , поэтому
// страница работает и так; здесь мы только избавляем от перезагрузки
// и от прыжка к началу списка.
(function(){
  const btn = document.getElementById('moreCars');
  const grid = document.getElementById('carGrid');
  if (!btn || !grid) return;
  const left = document.querySelector('.more-left');

  btn.addEventListener('click', async e => {
    e.preventDefault();
    if (btn.dataset.busy) return;
    btn.dataset.busy = '1';
    const label = btn.textContent;
    btn.textContent = 'Загружаем…';
    const p = new URLSearchParams(location.search);
    p.set('offset', btn.dataset.offset);
    try {
      const r = await fetch('/cars/more?' + p.toString());
      if (!r.ok) throw new Error(r.status);
      const html = (await r.text()).trim();
      grid.insertAdjacentHTML('beforeend', html);
      const shown = grid.children.length;
      btn.dataset.offset = shown;
      // Сколько осталось, знает подпись под кнопкой: пересчитываем по ней
      const rest = Number((left.textContent.match(/\d+/) || [0])[0]) -
                   (shown - Number(p.get('offset')));
      if (rest > 0){
        left.textContent = 'осталось ' + rest;
        btn.textContent = 'Показать ещё ' + Math.min(rest, 20);
      } else {
        btn.closest('.more-wrap').hidden = true;
      }
    } catch {
      // Не вышло — возвращаем кнопку-ссылку: перезагрузка сработает
      btn.textContent = label;
      location.href = btn.href;
    }
    delete btn.dataset.busy;
  });
})();
