// Скрипт шаблона templates/_city.html — город покупателя в шапке.
//
// Город — куда везти заказ. Угадываем по IP (/api/catalog/geo) и
// спрашиваем «Ваш город — …?», или покупатель выбирает сам: поиском по
// справочнику городов СДЭК или из быстрых кнопок. Хранится в куках city
// (название) и city_code (код СДЭК — по нему считаются тарифы).
//
// Оформление заказа берёт город отсюда (static/js/shop/cart.js) и,
// если покупатель сменил город там, сообщает сюда — siteCity.set().
// Остальные страницы узнают о смене событием «citychange» на document.

(function(){
  const box = document.getElementById('cityBox');
  const btn = document.getElementById('cityBtn');
  const pop = document.getElementById('cityPop');
  const nameEl = document.getElementById('cityName');
  const ask = document.getElementById('cityAsk');
  const search = document.getElementById('citySearch');
  const found = document.getElementById('cityFound');
  const YEAR = 31536000;
  const BIG = ['Москва', 'Санкт-Петербург', 'Екатеринбург', 'Новосибирск', 'Казань',
               'Нижний Новгород', 'Краснодар', 'Челябинск', 'Уфа', 'Ростов-на-Дону'];
  const esc = s => String(s ?? '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const cookie = n => {
    const m = document.cookie.match(new RegExp(`(?:^|; )${n}=([^;]*)`));
    return m ? decodeURIComponent(m[1]) : '';
  };
  const suggest = async q => {
    try { return await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(q))).json(); }
    catch { return []; }
  };

  // Единственное место, где город меняется: куки, подпись и событие
  // для страниц — вместе, рассинхрону взяться неоткуда
  function set(city, code){
    document.cookie = `city=${encodeURIComponent(city || '')};path=/;max-age=${YEAR};samesite=lax`;
    document.cookie = `city_code=${code || ''};path=/;max-age=${YEAR};samesite=lax`;
    nameEl.textContent = city || 'не выбран';
    ask.hidden = true;
    document.dispatchEvent(new CustomEvent('citychange', {detail: {city, code: code || null}}));
  }
  // Город без кода (кнопка, догадка) — код находим по названию
  async function pick(city, code){
    if (!code){
      const hit = (await suggest(city)).find(c => c.name === city);
      code = hit ? hit.cdek_code : null;
    }
    set(city, code);
  }
  window.siteCity = {
    get: () => ({city: cookie('city'), code: +cookie('city_code') || null}),
    set,
  };

  // Быстрые кнопки: наши города (самовывоз) и крупные
  let ours = null;
  async function drawQuick(){
    if (!ours){
      try { ours = (await (await fetch('/api/catalog/cities')).json()).map(c => c.city); }
      catch { ours = []; }
    }
    const now = cookie('city');
    const btns = list => list.map(c =>
      `<button type="button" data-city="${esc(c)}" ${c === now ? 'aria-current="true"' : ''}>${esc(c)}</button>`).join('');
    document.getElementById('cityOurs').innerHTML = btns(ours);
    document.getElementById('cityBig').innerHTML = btns(BIG.filter(c => !ours.includes(c)));
  }

  function open(state){
    pop.hidden = !state;
    btn.setAttribute('aria-expanded', String(state));
    if (state){
      ask.hidden = true;
      search.value = ''; found.hidden = true;
      drawQuick();
      setTimeout(() => search.focus(), 0);
    }
  }
  btn.onclick = () => open(pop.hidden);

  pop.addEventListener('click', async e => {
    const b = e.target.closest('.cp-quick button, .cp-found button');
    if (!b) return;
    open(false);
    await pick(b.dataset.city, +b.dataset.code || null);
  });

  // Поиск по справочнику СДЭК: «Самар» → Самара, Самарская обл.
  let st, seq = 0;
  search.addEventListener('input', () => {
    clearTimeout(st);
    const q = search.value.trim();
    if (q.length < 2){ found.hidden = true; return; }
    st = setTimeout(async () => {
      const my = ++seq;
      const rows = await suggest(q);
      if (my !== seq) return;
      found.innerHTML = rows.length ? rows.map(c =>
        `<button type="button" role="option" data-city="${esc(c.name)}" data-code="${c.cdek_code}">
           ${esc(c.name)}<small>${esc(c.full_name.split(',').slice(1).join(',').trim())}</small></button>`).join('')
        : '<p>Такого города не нашлось</p>';
      found.hidden = false;
    }, 250);
  });
  // Enter — первый из подсказок
  search.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const first = found.querySelector('button');
    if (first) first.click();
  });

  document.addEventListener('click', e => {
    if (!box.contains(e.target)) open(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && (!pop.hidden || !ask.hidden)){ open(false); ask.hidden = true; btn.focus(); }
  });

  // Первый заход: города нет — угадываем по IP. Ставим сразу (оформление
  // подставит его в доставку), но спрашиваем: догадка должна дождаться
  // ответа. Ручной выбор сюда не попадает — кука уже стоит
  if (!cookie('city')){
    (async () => {
      try {
        const geo = await (await fetch('/api/catalog/geo')).json();
        if (!geo.city) return;           // не определили — человек выберет сам
        set(geo.city, geo.code);
        document.getElementById('askName').textContent = geo.city;
        ask.hidden = false;
        document.getElementById('askYes').onclick = () => { ask.hidden = true; };
        document.getElementById('askNo').onclick = () => open(true);
      } catch { /* без определения шапка просто предлагает выбрать город */ }
    })();
  }
})();
