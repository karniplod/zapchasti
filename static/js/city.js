// Скрипт шаблона templates/_city.html.

(function(){
  const box = document.getElementById('cityBox');
  const btn = document.getElementById('cityBtn');
  const pop = document.getElementById('cityPop');
  const nameEl = document.getElementById('cityName');
  const ask = document.getElementById('cityAsk');

  const YEAR = 31536000;
  const read = () => {
    const m = document.cookie.match(/(?:^|; )city=([^;]*)/);
    return m ? decodeURIComponent(m[1]) : '';
  };

  let cities = null;               // грузим по первому требованию
  const load = async () => cities
    || (cities = await (await fetch('/api/catalog/cities')).json());

  // Единственное место, где город меняется: и кука, и подпись, и
  // выдача каталога обновляются вместе, рассинхрону взяться неоткуда
  function setCity(city, notify = true){
    document.cookie = `city=${encodeURIComponent(city)};path=/;max-age=${YEAR}`;
    nameEl.textContent = city || 'все города';
    ask.hidden = true;
    if (notify && window.onCityChange) window.onCityChange(city);
  }

  async function draw(){
    const rows = await load();
    const now = read();
    pop.innerHTML = rows.map(c =>
      `<button role="option" data-city="${c.city}"
               ${c.city === now ? 'aria-selected="true"' : ''}>${c.city}
         <span class="n">${c.parts}</span></button>`).join('')
      + `<button role="option" data-city=""
                ${now ? '' : 'aria-selected="true"'} class="all">Все города</button>`;
    pop.querySelectorAll('button').forEach(b => b.onclick = () => {
      setCity(b.dataset.city);
      draw();
      open(false);
    });
  }

  function open(state){
    pop.hidden = !state;
    btn.setAttribute('aria-expanded', String(state));
  }

  btn.onclick = async () => {
    if (!pop.hidden){ open(false); return; }
    await draw();
    open(true);
    ask.hidden = true;
  };

  document.addEventListener('click', e => {
    if (!box.contains(e.target)) open(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape'){ open(false); ask.hidden = true; }
  });

  // Первый заход: города в куке нет, спрашиваем geoip.
  // Ручной выбор сюда не попадает никогда — кука уже стоит
  if (document.cookie.match(/(?:^|; )city=/) === null){
    (async () => {
      try {
        const geo = await (await fetch('/api/catalog/geo')).json();
        if (!geo.city) return;          // не определили или мы там не работаем

        if (geo.parts > 0){
          setCity(geo.city);
          document.getElementById('askName').textContent = geo.city;
        } else {
          // Филиал есть, товара пока нет. Включить такой фильтр — значит
          // встретить человека пустым каталогом; показываем всё и говорим
          // почему. Пустую куку всё равно ставим, иначе объяснение будет
          // выскакивать на каждой странице
          setCity('', false);
          ask.querySelector('p').innerHTML =
            `Мы работаем в городе <b>${geo.city}</b>, но все детали сейчас `
            + `в других городах — показываем весь склад.`;
          ask.querySelector('#askYes').textContent = 'Понятно';
        }

        ask.hidden = false;
        // Молча не исчезает: догадка должна дождаться ответа
        document.getElementById('askYes').onclick = () => { ask.hidden = true; };
        document.getElementById('askNo').onclick = async () => {
          ask.hidden = true; await draw(); open(true);
        };
      } catch { /* без geoip шапка просто предлагает выбрать город */ }
    })();
  }
})();
