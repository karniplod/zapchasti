// Подсказки улицы и дома для форм адреса — правка заказа в кабинете
// покупателя и в карточке заказа у менеджера. Источник — DaData через
// /api/address/suggest (ключ на сервере); без ключа подсказок просто нет,
// адрес вводят руками.
//
// Улицы ищем в выбранном городе, дома — на выбранной улице. Выбрали дом
// из подсказки (или ввели руками, а такой дом есть) — подставляем индекс.
//
//   addressSuggest({city: el, street: el, house: el, block: el, post: el,
//                   streetList: el, houseList: el, country: () => 'RU',
//                   onChange: () => {...}});

function addressSuggest(o){
  if (!o.street || !o.house) return;
  const esc = s => String(s ?? '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  let fias = '', st, hs, s1 = 0, s2 = 0;
  const changed = () => { if (o.onChange) o.onChange(); };

  const ask = async (level, q, extra = {}) => {
    const p = new URLSearchParams({level, q, city: o.city ? o.city.value.trim() : '',
                                   country: o.country ? o.country() : 'RU', ...extra});
    try {
      const r = await fetch('/api/address/suggest?' + p);
      return r.ok ? r.json() : [];
    } catch { return []; }
  };
  const show = (box, rows, label, pick) => {
    box.innerHTML = rows.map((r, i) =>
      `<button type="button" role="option" data-i="${i}">${label(r)}</button>`).join('');
    box.hidden = !rows.length;
    box.querySelectorAll('button').forEach(b => b.onclick = () => {
      box.hidden = true;
      pick(rows[+b.dataset.i]);
    });
  };
  // Индекс подставляем, только если его не вводили руками
  const setPost = code => {
    if (!code || !o.post || o.post.dataset.manual === '1') return;
    if (o.post.value !== code){
      o.post.value = code;
      if (typeof fieldError === 'function') fieldError(o.post, '');
      changed();
    }
  };
  const houseQuery = h => fias ? [h, {street_fias: fias}] : [`${o.street.value.trim()} ${h}`, {}];

  o.street.addEventListener('input', () => {
    fias = '';
    clearTimeout(st);
    const q = o.street.value.trim();
    if (q.length < 2){ o.streetList.hidden = true; return; }
    st = setTimeout(async () => {
      const my = ++s1;
      const rows = await ask('street', q);
      if (my !== s1) return;
      show(o.streetList, rows, r => esc(r.street) + (r.postcode ? `<small>${esc(r.postcode)}</small>` : ''), r => {
        o.street.value = r.street; fias = r.street_fias || '';
        if (typeof fieldError === 'function') fieldError(o.street, '');
        changed();
        o.house.focus();
      });
    }, 250);
  });

  o.house.addEventListener('input', () => {
    clearTimeout(hs);
    const h = o.house.value.trim();
    if (!h || !o.street.value.trim()){ o.houseList.hidden = true; return; }
    hs = setTimeout(async () => {
      const my = ++s2;
      const rows = await ask('house', ...houseQuery(h));
      if (my !== s2) return;
      show(o.houseList, rows,
        r => esc(r.house) + (r.block ? ' ' + esc(r.block) : '') + (r.postcode ? `<small>${esc(r.postcode)}</small>` : ''),
        r => {
          o.house.value = r.house_num || r.house;
          if (r.block && o.block) o.block.value = r.block;
          if (typeof fieldError === 'function') fieldError(o.house, '');
          setPost(r.postcode);
          changed();
        });
    }, 250);
  });

  // Дом ввели руками, без подсказки, — всё равно найдём индекс,
  // если такой дом на этой улице есть
  o.house.addEventListener('change', () => setTimeout(async () => {
    const h = o.house.value.trim();
    if (!h || !o.street.value.trim() || !o.houseList.hidden) return;
    const rows = await ask('house', ...houseQuery(h));
    const hit = rows.find(r => (r.house_num || '').toLowerCase() === h.toLowerCase());
    if (hit) setPost(hit.postcode);
  }, 250));

  if (o.post) o.post.addEventListener('input', () => { o.post.dataset.manual = o.post.value ? '1' : ''; });
  // Другой город — прежняя улица к нему не относится
  if (o.city) o.city.addEventListener('input', () => { fias = ''; });

  document.addEventListener('click', e => {
    if (!o.streetList.contains(e.target) && e.target !== o.street) o.streetList.hidden = true;
    if (!o.houseList.contains(e.target) && e.target !== o.house) o.houseList.hidden = true;
  });
}
