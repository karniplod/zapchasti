// Длинный список с поиском: где раньше был обычный <select>, появляется
// поле, в которое можно писать. Подключается на витрине и в бэкенде.
//
// Зачем. В справочнике 418 марок, 219 категорий, четыре сотни машин —
// в системном списке такое ищут прокруткой, а на телефоне это вообще
// колесо на весь экран. Теперь: начал печатать «мер» — остались Mercedes
// и Mercury.
//
// Как устроено. Мы не заменяем <select>, а надстраиваем его: он
// остаётся в разметке и по-прежнему хранит значение, поэтому формы
// уходят как раньше, а чужой код, слушающий change, ничего не замечает.
// Без скрипта страница работает системным списком — так же, как до
// этого.
//
// Кого подхватываем: любой <select> длиннее MIN пунктов. Короткие
// (статус, сортировка, способ доставки) не трогаем — там искать нечего.
// Списки, которые заполняются позже (модель после марки, поколение
// после модели), ловит наблюдатель: он же следит за появлением новых
// списков в перерисованных таблицах бэкенда.
(function(){
  'use strict';

  const MIN = 8;     // со скольких пунктов список получает поиск
  const SHOW = 60;   // сколько строк рисуем сразу: 400 строк тормозят
  let seq = 0;

  // «ё» и регистр не должны мешать поиску: «Ёлка» находится по «елка»
  const norm = s => (s || '').toLowerCase().replace(/ё/g, 'е').trim();

  // Половина марок записана латиницей, и набирать их русскими буквами
  // для нашего покупателя естественно: «мерс» должно находить Mercedes,
  // «шкода» — Skoda. Поэтому у каждого пункта есть латинский ключ, а
  // русский запрос превращается в выражение, где спорные буквы
  // допускают оба написания: с → s или c, ш → sh или s
  const LAT = {а:'a',б:'b',в:'v',г:'g',д:'d',е:'e',ж:'zh',з:'z',и:'i',й:'y',
    к:'k',л:'l',м:'m',н:'n',о:'o',п:'p',р:'r',с:'s',т:'t',у:'u',ф:'f',х:'h',
    ц:'c',ч:'ch',ш:'sh',щ:'sch',ъ:'',ы:'y',ь:'',э:'e',ю:'yu',я:'ya'};
  const ALT = {а:'a',б:'b',в:'[vw]',г:'g',д:'d',е:'e',ж:'(?:zh|j)',з:'z',и:'i',
    й:'[yi]',к:'[kc]',л:'l',м:'m',н:'n',о:'o',п:'p',р:'r',с:'[sc]',т:'t',
    у:'[uy]',ф:'f',х:'[hkx]',ц:'[ctz]',ч:'(?:ch|c)',ш:'(?:sh|s)',
    щ:'(?:sch|sh)',ъ:'',ы:'y',ь:'',э:'e',ю:'(?:yu|u|iu)',я:'(?:ya|a|ia)'};

  const lat = s => [...norm(s)].map(c => c in LAT ? LAT[c] : c).join('');

  // Знаки вроде «(» в запросе не должны ломать выражение
  const esc = c => /[a-z0-9а-я ]/.test(c) ? c : '\\' + c;

  function query(q){
    const n = norm(q);
    if (!n) return null;
    if (!/[а-я]/.test(n)) return {n, rx: null};
    const body = [...n].map(c => c in ALT ? ALT[c] : esc(c)).join('');
    let rx = null;
    try { rx = new RegExp(body, 'i'); } catch {}
    return {n, rx};
  }

  function options(sel){
    return [...sel.options].map((o, i) => ({
      i, value: o.value, text: o.text, disabled: o.disabled,
      key: norm(o.text), lat: lat(o.text),
    }));
  }

  function enhance(sel){
    if (sel.dataset.combo === 'off' || sel.multiple || sel.comboBox) return;
    if (sel.options.length < MIN) return;

    // Имя поля — от имени списка: так его находят подписи и проверки
    const id = sel.id || 'combo' + (++seq);
    const box = document.createElement('div');
    box.className = 'combo';
    const input = document.createElement('input');
    input.type = 'text';
    input.id = id + '-in';
    input.className = sel.className || '';
    input.classList.add('combo-input');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', id + '-list');
    input.setAttribute('aria-autocomplete', 'list');
    input.autocomplete = 'off';
    input.spellcheck = false;
    const list = document.createElement('ul');
    list.className = 'combo-list';
    list.id = id + '-list';
    list.setAttribute('role', 'listbox');
    list.hidden = true;

    sel.parentNode.insertBefore(box, sel);
    box.append(sel, input, list);
    sel.classList.add('combo-native');

    // Подпись вела на <select>; ведём её на поле, иначе нажатие по
    // подписи ставило бы фокус на спрятанный список
    if (sel.id){
      const lab = document.querySelector('label[for="' + CSS.escape(sel.id) + '"]');
      if (lab) lab.setAttribute('for', input.id);
    }

    let items = options(sel);
    let shown = [];
    let active = -1;
    let open = false;

    const chosen = () => sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;

    function label(){
      const o = chosen();
      // Пустое первое значение («— выберите —») в поле не показываем:
      // оно и так подсказано серым placeholder
      return o && o.value ? o.text : '';
    }

    function placeholder(){
      const first = sel.options[0];
      return sel.dataset.comboPlaceholder
          || (first && !first.value ? first.text : 'Начните вводить');
    }

    function paint(q){
      const f = query(q);
      const n = f ? f.n : '';
      const fits = o => o.value && (o.key.includes(n) || o.lat.includes(n)
                                    || (f.rx && f.rx.test(o.lat)));
      const hit = f
        ? items.filter(fits)
               // совпадение с начала — выше: «ваз» должен найти ВАЗ,
               // а не «Нива ВАЗ» первой строкой
               .sort((a, b) => (b.key.startsWith(n) - a.key.startsWith(n))
                            || (b.lat.startsWith(n) - a.lat.startsWith(n))
                            || a.i - b.i)
        : items.filter(o => o.value);
      shown = hit.slice(0, SHOW);
      list.innerHTML = '';
      if (!shown.length){
        const li = document.createElement('li');
        li.className = 'combo-none';
        li.textContent = 'Ничего не нашлось';
        list.append(li);
        return;
      }
      shown.forEach((o, k) => {
        const li = document.createElement('li');
        li.className = 'combo-opt';
        li.id = id + '-o' + k;
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', String(o.value === sel.value));
        li.dataset.k = String(k);
        li.textContent = o.text;
        list.append(li);
      });
      if (hit.length > shown.length){
        const li = document.createElement('li');
        li.className = 'combo-more';
        li.textContent = 'ещё ' + (hit.length - shown.length) + ' — уточните запрос';
        list.append(li);
      }
      mark(shown.findIndex(o => o.value === sel.value));
    }

    function mark(k){
      active = k;
      [...list.querySelectorAll('.combo-opt')].forEach((li, i) => {
        const on = i === k;
        li.classList.toggle('on', on);
        if (on){
          input.setAttribute('aria-activedescendant', li.id);
          li.scrollIntoView({block: 'nearest'});
        }
      });
      if (k < 0) input.removeAttribute('aria-activedescendant');
    }

    function show(q){
      paint(q === undefined ? '' : q);
      list.hidden = false;
      open = true;
      input.setAttribute('aria-expanded', 'true');
    }

    function hide(restore){
      list.hidden = true;
      open = false;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      if (restore !== false) input.value = label();
    }

    function pick(k){
      const o = shown[k];
      if (!o) return;
      sel.selectedIndex = o.i;
      input.value = o.text;
      hide(false);
      // Чужой код слушает change у списка — шлём его сами
      sel.dispatchEvent(new Event('change', {bubbles: true}));
    }

    input.addEventListener('focus', () => { input.select(); show(''); });
    input.addEventListener('input', () => show(input.value));
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
        e.preventDefault();
        if (!open){ show(''); return; }
        const step = e.key === 'ArrowDown' ? 1 : -1;
        const n = list.querySelectorAll('.combo-opt').length;
        if (n) mark((active + step + n) % n);
      } else if (e.key === 'Enter'){
        if (open){ e.preventDefault(); pick(active < 0 ? 0 : active); }
      } else if (e.key === 'Escape'){
        if (open){ e.stopPropagation(); hide(); }
      } else if (e.key === 'Tab'){
        hide();
      }
    });
    list.addEventListener('pointerdown', e => {
      // pointerdown, а не click: click приходит после blur, а тот уже
      // успевает закрыть список. И не mousedown: на телефоне его
      // подделка приходит слишком поздно, выбор не срабатывал
      const li = e.target.closest('.combo-opt');
      if (!li) return;
      e.preventDefault();
      pick(Number(li.dataset.k));
    });
    input.addEventListener('blur', () => setTimeout(() => { if (open) hide(); }, 0));

    // Снаружи список могли переписать (модель после выбора марки) или
    // выключить — держим поле в том же состоянии
    function sync(){
      items = options(sel);
      input.value = label();
      input.placeholder = placeholder();
      input.disabled = sel.disabled;
      box.classList.toggle('is-disabled', sel.disabled);
      if (open) paint(input.value === label() ? '' : input.value);
      if (items.length < MIN && !sel.dataset.comboKeep) unhance();
    }

    function unhance(){
      // Список стал коротким — возвращаем системный
      hide(false);
      sel.classList.remove('combo-native');
      box.parentNode.insertBefore(sel, box);
      box.remove();
      delete sel.comboBox;
      obs.disconnect();
      sel.removeEventListener('change', sync);
    }

    sel.comboBox = {sync, input, list};
    sel.addEventListener('change', sync);
    const obs = new MutationObserver(sync);
    obs.observe(sel, {childList: true, attributes: true,
                      attributeFilter: ['disabled']});
    sync();
  }

  function scan(root){
    const r = root || document;
    const list = r.querySelectorAll ? r.querySelectorAll('select') : [];
    list.forEach(sel => { try { enhance(sel); } catch {} });
  }

  // Списки бэкенда создаются скриптами при каждой перерисовке таблицы,
  // поэтому следим за появлением новых
  let t;
  new MutationObserver(m => {
    const любопытно = m.some(x =>
      // список наполнили пунктами (модель после марки) — сам список
      // при этом не появлялся, менялось его содержимое
      x.target.tagName === 'SELECT' ||
      [...x.addedNodes].some(n => n.nodeType === 1 &&
        (n.tagName === 'SELECT' || n.tagName === 'OPTION' ||
         (n.querySelector && n.querySelector('select')))));
    if (!любопытно) return;
    clearTimeout(t);
    t = setTimeout(() => scan(document), 60);
  }).observe(document.documentElement, {childList: true, subtree: true});

  window.combo = {scan, enhance};
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', () => scan(document));
  else scan(document);
})();
