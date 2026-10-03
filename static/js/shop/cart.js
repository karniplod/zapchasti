// Скрипт шаблона templates/shop/cart.html.

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3200); }

async function drop(ids){
  const r = await Promise.all(ids.map(id => fetch('/api/cart/' + id, {method: 'DELETE'})));
  // Перезагружаем, а не убираем строку руками: сумма, предупреждения
  // и счётчик в шапке считаются на сервере
  if (r.every(x => x.ok)) location.reload(); else toast('Не получилось убрать');
}

document.querySelectorAll('.cart-row .drop').forEach(b => b.onclick = () => {
  b.disabled = true;
  drop([b.closest('.cart-row').dataset.part]);
});

// Количество: − и + сохраняются сразу, страница перезагружается — сумма
// строки, итог и счётчик в шапке считаются на сервере. Больше остатка
// сервер не даст и скажет, сколько есть
document.querySelectorAll('.cart-row .qty').forEach(box => {
  const row = box.closest('.cart-row');
  qtyStepper(box, async n => {
    box.querySelectorAll('button,input').forEach(x => x.disabled = true);
    try {
      const r = await fetch('/api/cart/' + row.dataset.part, {method: 'PATCH',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({qty: n})});
      const d = await r.json().catch(() => ({}));
      if (r.ok){ location.reload(); return; }
      toast(typeof d.detail === 'string' ? d.detail : 'Не получилось изменить количество');
    } catch { toast('Нет связи с сервером'); }
    box.querySelectorAll('button,input').forEach(x => x.disabled = false);
  });
});

const dropGone = $('dropGone');
if (dropGone) dropGone.onclick = () => drop(
  [...document.querySelectorAll('.cart-row[data-gone="1"]')].map(r => r.dataset.part));

// Выпадающая подсказка под полем: rows → кнопки; выбор отдаёт строку
function suggestList(box, rows, label, onPick){
  box.innerHTML = rows.map((r, i) =>
    `<button type="button" role="option" data-i="${i}">${label(r)}</button>`).join('');
  box.hidden = !rows.length;
  box.querySelectorAll('button').forEach(b => b.onclick = () => {
    box.hidden = true;
    onPick(rows[+b.dataset.i]);
  });
}

// Без входа на странице только товары — оформлять нечего
const form = $('orderForm');
if (form && $('cname')){
  const picked = name => form.querySelector(`input[name=${name}]:checked`);
  const val = name => (picked(name) || {}).value;
  const rub = n => Math.round(+n).toLocaleString('ru') + ' ₽';
  const esc = s => String(s ?? '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  // 1 посылка, 2 посылки, 5 посылок
  const plural = (n, one, few, many) => {
    const a = n % 10, b = n % 100;
    return `${n} ${a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many}`;
  };
  const parcels = n => plural(n, 'посылка', 'посылки', 'посылок');
  const kg = g => (Math.round(g / 100) / 10).toLocaleString('ru') + ' кг';
  const CARRIER = {cdek: 'СДЭК', yandex: 'Яндекс Доставка', pochta: 'Почта России'};

  // ── Доставка ─────────────────────────────────────────────────
  // Страна и город → варианты служб с ценой → пункт выдачи или адрес
  // по полям. Цена здесь — только показать; при оформлении сервер
  // считает её заново
  const shipBox = $('shipBox');
  const hasCarriers = !!$('dOptions');
  const hints = shipBox && shipBox.dataset.hints === '1';
  // Город доставки — из шапки (templates/_city.html): сервер подставил его
  // в поле, код СДЭК — в data-cdek
  const D = {city: '', cdek_code: hasCarriers && +$('dCity').dataset.cdek || null, options: [],
             point: null, points: [], street_fias: '', parcels: [], issues: {}};
  const opt = () => D.options.find(o => `${o.carrier}:${o.mode}` === val('dopt')) || null;
  const isPvz = () => { const o = opt(); return !!(o && o.mode === 'pvz'); };
  const ADDR_FIELDS = ['aStreet', 'aHouse', 'aBlock', 'aFlat'];
  const postcode = () => $('dPost').value.length === 6 ? $('dPost').value : null;

  // Сводка справа повторяет выбор — его видно рядом с итогом и кнопкой
  // Скидка и баллы: discount — сколько рублей, bonus — сколько списываем
  const L = {discount: 0, bonus: 0, source: null, code: null, max: 0, balance: 0};
  const sync = () => {
    const ship = val('dm') === 'shipping';
    const o = ship && hasCarriers ? opt() : null;
    $('pickupBox').hidden = ship;
    shipBox.hidden = !ship;
    // Для пункта выдачи адрес не нужен — у пункта свой. Поля не прячем:
    // они выше вариантов, и прыгала бы вся форма. Подпись говорит, что
    // заполнять необязательно, проверка их пропускает
    $('addrBox').hidden = !ship;
    const pvz = !!(o && o.mode === 'pvz');
    $('addrBox').classList.toggle('is-optional', pvz);
    $('addrBox').querySelector('.opt-note').hidden = !pvz;
    if ($('saveAddrBox') && pvz) $('saveAddrBox').hidden = true;
    if (pvz) ADDR_FIELDS.forEach(id => fieldError($(id), ''));
    if (hasCarriers) $('dPointBox').hidden = !(o && o.mode === 'pvz');
    // Выбранный пункт — и в сводке: адрес рядом с итогом и кнопкой
    const pt = o && o.mode === 'pvz' && D.points.find(x => x.code === D.point);
    $('sumPointL').hidden = $('sumPoint').hidden = !pt;
    if (pt) $('sumPoint').textContent = pt.address;
    const br = $('branch').selectedOptions[0];
    // Посылок несколько — это видно и в сводке: придут не одним днём
    const many = o && o.parcels && o.parcels.length > 1 ? `, ${parcels(o.parcels.length)}` : '';
    $('sumShip').textContent = !ship ? 'Самовывоз' + (br ? ' — ' + br.text : '')
      : o ? o.title + many : hasCarriers ? 'Доставка — выберите вариант' : 'Доставка ТК';
    if (hasCarriers) drawBreak(ship ? o : null);
    $('shipLine').textContent = !ship ? 'бесплатно'
      : o ? (o.from && !D.point ? 'от ' : '') + rub(o.price) : hasCarriers ? '—' : 'по тарифу ТК';
    const goods = +$('grandTotal').dataset.goods;
    // Скидка и баллы — товары дешевле, доставка та же
    const off = L.discount + L.bonus;
    $('discL').hidden = $('discLine').hidden = !L.discount;
    $('discLine').textContent = '−' + rub(L.discount);
    $('discL').textContent = L.source === 'promo' ? 'Скидка по промокоду' : 'Персональная скидка';
    $('bonusL').hidden = $('bonusLine').hidden = !L.bonus;
    $('bonusLine').textContent = '−' + rub(L.bonus);
    $('grandTotal').textContent = rub(goods - off + (o ? +o.price : 0));
    $('sumPay').textContent = picked('pm') ? picked('pm').dataset.label : '';
    $('place').textContent = val('pm') && val('pm') !== 'on_receipt'
      ? 'Оформить и оплатить' : 'Оформить заказ';
  };
  form.querySelectorAll('input[name=dm], input[name=pm]').forEach(r => r.onchange = sync);
  $('branch').onchange = sync;

  // Выбрали доставку, а город уже известен из шапки — считаем сразу,
  // не заставляя вводить его заново
  form.querySelectorAll('input[name=dm]').forEach(r => r.addEventListener('change', () => {
    if (val('dm') === 'shipping' && hasCarriers && $('dCity').value.trim() && !D.options.length) loadQuotes();
  }));

  // Самовывоз: в городе покупателя есть наш филиал — предлагаем его
  const pickBranch = city => {
    if (!city) return;
    const cur = $('branch').selectedOptions[0];
    const mine = [...$('branch').options].find(o => o.text.startsWith(city + ','));
    if (mine && !(cur && cur.text.startsWith(city + ','))){ mine.selected = true; sync(); }
  };
  pickBranch(window.siteCity ? window.siteCity.get().city : '');

  // Город сменили в шапке, не уходя со страницы, — доставка и самовывоз
  // следуют за ним. Смена отсюда же (подсказка в поле ниже) сюда тоже
  // приходит — тогда поле уже совпадает и пересчитывать нечего
  document.addEventListener('citychange', e => {
    const {city, code} = e.detail;
    pickBranch(city);
    if (!city || $('dCity').value.trim() === city) return;
    $('dCity').value = city; D.cdek_code = code; D.street_fias = '';
    fieldError($('dCity'), '');
    if (val('dm') === 'shipping') loadQuotes();
    else { D.options = []; if (hasCarriers) $('dOptions').innerHTML = ''; }
  });

  // Город — подсказка из справочника СДЭК: у города там код для тарифов
  let ct, seq = 0;
  $('dCity').addEventListener('input', () => {
    D.cdek_code = null; D.street_fias = '';
    clearTimeout(ct);
    const q = $('dCity').value.trim();
    if (q.length < 2){ $('dCityList').hidden = true; return; }
    ct = setTimeout(async () => {
      const my = ++seq;
      const rows = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(q))).json();
      if (my !== seq) return;
      suggestList($('dCityList'), rows, c => esc(c.full_name), c => {
        $('dCity').value = c.name; D.cdek_code = c.cdek_code;
        fieldError($('dCity'), '');
        // Город сменили здесь — он и город покупателя в шапке
        if (window.siteCity) window.siteCity.set(c.name, c.cdek_code);
        loadQuotes();
        $('aStreet').focus();
      });
    }, 250);
  });
  // Город набрали без подсказки — тоже считаем: Почта и Яндекс
  // справятся и без кода СДЭК
  $('dCity').addEventListener('change', () => setTimeout(() => {
    if ($('dCityList').hidden) loadQuotes();
  }, 200));

  // ── Улица и дом: подсказки DaData, индекс по полному адресу ──
  let st, hs, sseq = 0, hseq = 0;
  const ask = async (level, q, extra = {}) => {
    const p = new URLSearchParams({level, q, city: $('dCity').value.trim(),
                                   country: $('dCountry').value, ...extra});
    const r = await fetch('/api/address/suggest?' + p);
    return r.ok ? r.json() : [];
  };
  const setPost = code => {
    if (!code || $('dPost').dataset.manual === '1') return;
    if ($('dPost').value !== code){
      $('dPost').value = code;
      $('dPost').classList.add('autofilled');
      fieldError($('dPost'), '');
      $('postHint').textContent = 'Индекс подставлен по адресу — проверьте';
      if (hasCarriers && $('dCity').value.trim()) loadQuotes();
    }
  };
  if (hints){
    $('aStreet').addEventListener('input', () => {
      D.street_fias = '';
      clearTimeout(st);
      const q = $('aStreet').value.trim();
      if (q.length < 2){ $('aStreetList').hidden = true; return; }
      st = setTimeout(async () => {
        const my = ++sseq;
        const rows = await ask('street', q);
        if (my !== sseq) return;
        suggestList($('aStreetList'), rows, r => esc(r.street) + (r.postcode ? `<small>${esc(r.postcode)}</small>` : ''),
          r => {
            $('aStreet').value = r.street; D.street_fias = r.street_fias || '';
            fieldError($('aStreet'), '');
            $('aHouse').focus();
          });
      }, 250);
    });
    $('aHouse').addEventListener('input', () => {
      clearTimeout(hs);
      const h = $('aHouse').value.trim();
      if (!h || !$('aStreet').value.trim()){ $('aHouseList').hidden = true; return; }
      hs = setTimeout(async () => {
        const my = ++hseq;
        const rows = await ask('house', D.street_fias ? h : `${$('aStreet').value.trim()} ${h}`,
                               D.street_fias ? {street_fias: D.street_fias} : {});
        if (my !== hseq) return;
        suggestList($('aHouseList'), rows,
          r => esc(r.house) + (r.block ? ' ' + esc(r.block) : '') + (r.postcode ? `<small>${esc(r.postcode)}</small>` : ''),
          r => {
            $('aHouse').value = r.house_num || r.house;
            if (r.block) $('aBlock').value = r.block;
            fieldError($('aHouse'), '');
            setPost(r.postcode);
            $('aFlat').focus();
          });
      }, 250);
    });
    // Дом ввели руками, без подсказки, — всё равно найдём индекс,
    // если такой дом на этой улице есть
    $('aHouse').addEventListener('change', () => setTimeout(async () => {
      const h = $('aHouse').value.trim(), s = $('aStreet').value.trim();
      if (!h || !s || !$('aHouseList').hidden) return;
      const rows = await ask('house', D.street_fias ? h : `${s} ${h}`,
                             D.street_fias ? {street_fias: D.street_fias} : {});
      const hit = rows.find(r => (r.house_num || '').toLowerCase() === h.toLowerCase());
      if (hit) setPost(hit.postcode);
    }, 250));
  }
  // Индекс руками — дальше не трогаем его подсказкой
  $('dPost').addEventListener('input', () => {
    $('dPost').value = $('dPost').value.replace(/\D/g, '').slice(0, 6);
    $('dPost').dataset.manual = $('dPost').value ? '1' : '';
    $('dPost').classList.remove('autofilled');
    if ($('dPost').value.length === 6 && hasCarriers && $('dCity').value.trim()) loadQuotes();
  });
  $('dCountry').onchange = () => {
    // Другая страна — прежний адрес к ней не относится
    ['dCity', 'aStreet', 'aHouse', 'aBlock', 'aFlat', 'dPost'].forEach(id => { $(id).value = ''; });
    D.cdek_code = null; D.street_fias = ''; D.options = []; D.point = null;
    D.parcels = []; D.issues = {};
    if (hasCarriers){ $('dOptions').innerHTML = ''; $('dParcels').hidden = true; $('dState').textContent = 'Укажите город — посчитаем стоимость и срок доставки.'; }
    sync();
  };
  document.addEventListener('click', e => {
    if (!e.target.closest('.city-fld')) $('dCityList').hidden = true;
    if (!e.target.closest('.a-street')) $('aStreetList').hidden = true;
    if (!e.target.closest('.a-house')) $('aHouseList').hidden = true;
  });

  // ── Варианты служб и пункты выдачи ───────────────────────────
  let qseq = 0;
  // quiet — пересчёт по кнопке «Попробовать ещё раз»: варианты остаются
  // на экране, пока не придут новые
  async function loadQuotes(point, quiet){
    if (!hasCarriers) return;
    const city = $('dCity').value.trim();
    if (city.length < 2) return;
    const my = ++qseq;
    // Город из шапки без кода или набран без подсказки — код СДЭК находим
    // по точному названию: без кода СДЭК не считает
    if (!D.cdek_code && !point){
      try {
        const rows = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(city))).json();
        const hit = rows.find(c => c.name.toLowerCase() === city.toLowerCase());
        if (hit) D.cdek_code = hit.cdek_code;
      } catch {}
    }
    if (!point && !quiet){ $('dState').textContent = 'Считаем доставку…'; $('dOptions').innerHTML = ''; }
    const r = await fetch('/api/delivery/quotes', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({city, cdek_code: D.cdek_code, postcode: postcode(),
                            point: point || null})});
    const d = await r.json().catch(() => ({}));
    if (my !== qseq) return;
    if (point){
      // Пересчёт по выбранному пункту Яндекса: цена у него своя
      const y = (d.options || []).find(o => o.carrier === 'yandex');
      const o = D.options.find(o => o.carrier === 'yandex');
      if (o && y){ Object.assign(o, y, {from: false}); $('dPointErr').hidden = true; }
      else {
        // Посылок несколько — сервер скажет, какую из них Яндекс не берёт
        $('dPointErr').textContent = (d.issues && d.issues.yandex)
          || 'В этот пункт Яндекс не доставляет — выберите другой';
        $('dPointErr').hidden = false; D.point = null;
        drawPoints(); showPicked();
      }
      drawOptions(); sync(); return;
    }
    const keep = val('dopt');
    D.city = city; D.options = d.options || [];
    if (!D.options.some(o => `${o.carrier}:${o.mode}` === keep)) D.point = null;
    D.needPost = !!d.need_postcode;
    D.issues = d.issues || {};
    D.retry = d.retry || [];
    D.parcels = d.parcels || [];
    const any = D.options.length || D.needPost || Object.keys(D.issues).length;
    const one = D.parcels.length === 1 ? D.parcels[0] : null;
    $('dState').textContent = any
      ? (one ? `Отправим ${one.from_city}. ` : '') + 'Выберите вариант:'
      : 'Служба не посчитала доставку в этот город. Проверьте название — или выберите самовывоз.';
    drawParcels();
    drawOptions(keep); sync();
  }

  // Детали из разных филиалов — заказ придёт несколькими посылками.
  // Говорим об этом до выбора, а не после: цена — сумма посылок
  function drawParcels(){
    const box = $('dParcels');
    box.hidden = D.parcels.length < 2;
    if (box.hidden) return;
    box.innerHTML = `<b>Заказ придёт ${D.parcels.length === 2 ? 'двумя' : D.parcels.length === 3 ? 'тремя' : D.parcels.length}
        посылками — детали лежат в разных филиалах</b>
      <ul>${D.parcels.map(p => `<li><span>${esc(p.from_city[0].toUpperCase() + p.from_city.slice(1))}</span>
        <span>${plural(p.qty, 'деталь', 'детали', 'деталей')}, ${kg(p.weight_g)}</span></li>`).join('')}</ul>`;
  }

  // Из чего сложилась цена выбранного варианта — по посылкам
  function drawBreak(o){
    const box = $('dBreak');
    box.hidden = !(o && o.parcels && o.parcels.length > 1);
    if (box.hidden) return;
    const wasOpen = box.querySelector('details') && box.querySelector('details').open;
    box.innerHTML = `<details${wasOpen ? ' open' : ''}><summary>Подробнее: цена и срок каждой посылки</summary>
      <ul>${o.parcels.map(x => `<li><span>${esc(CARRIER[o.carrier] || '')} ${esc(x.from_city)}</span>
        <span>${o.from && !D.point ? 'от ' : ''}${rub(x.price)}${x.days ? ' · ' + esc(x.days) : ''}</span></li>`).join('')}</ul>
      </details>`;
  }

  function drawOptions(keep){
    const cur = keep || val('dopt');
    $('dOptions').innerHTML = D.options.map(o => {
      const k = `${o.carrier}:${o.mode}`;
      const n = (o.parcels || []).length;
      return `<label class="tile"><input type="radio" name="dopt" value="${k}" ${k === cur ? 'checked' : ''}>
        <span><b>${esc(o.title)}</b>
        <small>${o.from && !D.point ? 'от ' : ''}${rub(o.price)}${n > 1 ? ' за ' + parcels(n) : ''}${o.days ? ' · ' + esc(o.days) : ''}
          ${n > 1 ? '<br>посылки придут в разные дни' : ''}
          ${o.estimate ? '<br>цена примерная: учебная среда СДЭК' : ''}</small></span></label>`;
    }).join('')
    // Почта России считает только по индексу. Пока его нет — плитка видна,
    // но неактивна: человек сразу знает, что такой вариант есть и что для
    // него нужно. Нажатие ведёт к полю индекса
    + (D.needPost && !D.options.some(o => o.carrier === 'pochta')
      ? `<button type="button" class="tile is-off" id="pochtaOff">
          <span><b>Почта России — до отделения</b><small>Введите индекс — посчитаем стоимость</small></span></button>`
      : '')
    // Служба есть, но заказ целиком не возьмёт (посылка тяжелее 20 кг,
    // из этого филиала не возит) — показываем причину, а не прячем вариант
    + Object.entries(D.issues).map(([c, why]) =>
        `<div class="tile is-off is-na"><span><b>${esc(c === 'pochta' ? 'Почта России — до отделения' : CARRIER[c] || c)}</b>
          <small>${esc(why)}</small>
          ${(D.retry || []).includes(c) ? `<button type="button" class="linkish retry-q" data-c="${c}">Попробовать ещё раз</button>` : ''}
          </span></div>`).join('');
    // Служба не ответила — спросить её снова. Остальные службы уже
    // посчитаны и запомнены сервером: повтор ждёт только молчавшую
    $('dOptions').querySelectorAll('.retry-q').forEach(b => b.onclick = async () => {
      b.disabled = true; b.textContent = 'Спрашиваем…';
      await loadQuotes(null, true);
      if ((D.retry || []).includes(b.dataset.c)) toast(`${CARRIER[b.dataset.c] || 'Служба'} всё ещё не отвечает — попробуйте чуть позже`);
    });
    $('dOptions').querySelectorAll('input:not([disabled])').forEach(i => i.onchange = () => {
      D.point = null; loadPoints(); sync();
    });
    const off = $('pochtaOff');
    if (off) off.onclick = () => {
      // Индекс — в блоке адреса, а он спрятан, пока выбран пункт выдачи:
      // снимаем выбор, чтобы поле стало видно
      form.querySelectorAll('input[name=dopt]').forEach(i => { i.checked = false; });
      D.point = null; sync();
      $('dPost').scrollIntoView({block: 'center', behavior: 'smooth'});
      $('dPost').focus({preventScroll: true});
      fieldError($('dPost'), '');
      $('postHint').textContent = 'Введите индекс — Почта России посчитает стоимость';
    };
  }

  // Пункты выдачи — списком с поиском. Карте нужен свой ключ
  // Яндекс Карт; список работает и без него
  async function loadPoints(){
    const o = opt();
    if (!o || o.mode !== 'pvz'){ sync(); return; }
    $('dPoints').innerHTML = '<p class="hint">Загружаем пункты…</p>';
    const p = new URLSearchParams({carrier: o.carrier, city: D.city});
    if (D.cdek_code) p.set('cdek_code', D.cdek_code);
    D.points = await (await fetch('/api/delivery/points?' + p)).json();
    $('dPointQ').value = '';
    drawPoints();
    showPicked();
  }
  function drawPoints(){
    const q = $('dPointQ').value.trim().toLowerCase();
    const list = D.points.filter(p => !q || (p.name + ' ' + p.address).toLowerCase().includes(q))
                         .slice(0, 60);
    $('dPoints').innerHTML = list.length ? list.map(p => `
      <label class="pt"><input type="radio" name="dpoint" value="${esc(p.code)}"
             ${p.code === D.point ? 'checked' : ''}>
        <span><b>${esc(p.address)}</b><small>${esc(p.name)}${p.hours ? ' · ' + esc(p.hours) : ''}</small></span></label>`).join('')
      : '<p class="hint">Пунктов не нашлось</p>';
    $('dPoints').querySelectorAll('input').forEach(i => i.onchange = () => {
      D.point = i.value; $('dPointErr').hidden = true;
      showPicked();
      if (opt() && opt().carrier === 'yandex') loadQuotes(D.point); else sync();
    });
  }

  // Выбранный пункт — карточкой вместо списка; «Выбрать другой» снова
  // открывает список. Без пункта — сразу список
  function showPicked(){
    const p = D.points.find(x => x.code === D.point);
    // Список в сотни пикселей сворачивается в карточку — без поправки
    // браузер держит на месте то, что ниже, и страница уезжает вниз,
    // а выбранный пункт оказывается за верхним краем. Возвращаем блок
    // пункта выдачи туда, где он был на экране
    const box = $('dPointBox'), top = box.getBoundingClientRect().top;
    $('dPointPicked').hidden = !p;
    $('dPointPick').hidden = !!p;
    if (p){
      $('dPointAddr').textContent = p.address;
      $('dPointMeta').textContent = p.name + (p.hours ? ' · ' + p.hours : '');
    }
    const shift = box.getBoundingClientRect().top - top;
    if (shift) window.scrollBy({top: shift, behavior: 'instant'});
    // Блок ушёл за верх экрана (выбирали из самого низа длинного
    // списка) — показываем его целиком
    const r = box.getBoundingClientRect();
    if (r.top < 0 || r.bottom > innerHeight) box.scrollIntoView({block: 'nearest'});
  }
  if (hasCarriers){
    $('dPointQ').addEventListener('input', drawPoints);
    $('dPointChange').onclick = () => {
      $('dPointPicked').hidden = true;
      $('dPointPick').hidden = false;
      $('dPointQ').focus();
    };
  }
  sync();

  // ── Мои адреса ──────────────────────────────────────────────
  // Выпадающий список: адрес заполняет страну, город, улицу, дом и индекс
  // сразу. Первый в списке подставлен заранее — сервер ставит наверх
  // адрес из последнего заказа, без заказов — самый свежий; если в городе
  // из шапки есть свой адрес — берём его. Выбрали сохранённый —
  // «Запомнить адрес» не нужен; поправили поле — это уже новый адрес
  const saved = $('savedAddr') ? JSON.parse($('savedAddr').dataset.list) : [];
  const ADDR_IDS = ['aStreet', 'aHouse', 'aBlock', 'aFlat', 'dPost'];
  const markSaved = on => { if ($('saveAddrBox')) $('saveAddrBox').hidden = on || isPvz(); };
  const saOpen = on => {
    $('saMenu').hidden = !on;
    $('saBtn').setAttribute('aria-expanded', String(on));
    if (on) ($('saMenu').querySelector('[aria-selected=true]') || $('saMenu').querySelector('.sa-opt')).focus();
  };
  const saShow = i => {
    const a = saved[i];
    $('saTitle').textContent = a ? a.title || a.city : i === -1 ? 'Новый адрес' : 'Другой адрес';
    $('saLine').textContent = a ? a.line : 'Заполните поля ниже';
    $('saIco').innerHTML = a ? $('saMenu').querySelector(`.sa-opt[data-i="${i}"] .addr-ico`).outerHTML
                             : $('saMenu').querySelector('.sa-new .addr-ico').outerHTML;
    $('saMenu').querySelectorAll('.sa-opt').forEach(o => o.setAttribute('aria-selected', String(+o.dataset.i === i)));
  };
  function applySaved(a, i){
    $('dCountry').value = a.country || 'RU';
    $('dCity').value = a.city; D.cdek_code = a.cdek_code; D.street_fias = '';
    $('aStreet').value = a.street; $('aHouse').value = a.house;
    $('aBlock').value = a.block || ''; $('aFlat').value = a.flat || '';
    $('dPost').value = a.postcode || ''; $('dPost').dataset.manual = a.postcode ? '1' : '';
    ['dCity', ...ADDR_IDS].forEach(id => fieldError($(id), ''));
    saShow(i);
    markSaved(true);
    if (val('dm') === 'shipping' && hasCarriers) loadQuotes();
  }
  if (saved.length){
    $('saBtn').onclick = () => saOpen($('saMenu').hidden);
    $('saMenu').addEventListener('click', e => {
      const o = e.target.closest('.sa-opt');
      if (!o) return;
      const i = +o.dataset.i;
      saOpen(false);
      if (i >= 0){ applySaved(saved[i], i); return; }
      // Новый адрес: город оставляем, остальное — с чистого листа
      ADDR_IDS.forEach(id => { $(id).value = ''; fieldError($(id), ''); });
      $('dPost').dataset.manual = '';
      saShow(-1); markSaved(false);
      $('aStreet').focus();
    });
    // Стрелки и Esc в открытом списке
    $('saMenu').addEventListener('keydown', e => {
      const opts = [...$('saMenu').querySelectorAll('.sa-opt')];
      const k = opts.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
        e.preventDefault();
        opts[(k + (e.key === 'ArrowDown' ? 1 : opts.length - 1)) % opts.length].focus();
      } else if (e.key === 'Escape'){ saOpen(false); $('saBtn').focus(); }
    });
    document.addEventListener('click', e => { if (!e.target.closest('#savedAddr')) saOpen(false); });
    ['dCity', ...ADDR_IDS].forEach(id => $(id).addEventListener('input', () => {
      if ($('saMenu').querySelector('.sa-opt[aria-selected=true]:not(.sa-new)')) saShow(null);
      markSaved(false);
    }));
    const head = window.siteCity ? window.siteCity.get().city : '';
    const here = head ? saved.findIndex(a => a.city.toLowerCase() === head.toLowerCase()) : -1;
    const first = here >= 0 ? here : 0;
    applySaved(saved[first], first);
  }

  // Как назвать запоминаемый адрес: Дом, Работа — по желанию
  let saveTitle = null;
  if ($('saveTitles')){
    $('saveTitles').addEventListener('click', e => {
      const b = e.target.closest('.chip-btn');
      if (!b) return;
      saveTitle = saveTitle === b.dataset.t ? null : b.dataset.t;
      $('saveTitles').querySelectorAll('.chip-btn').forEach(x => {
        x.classList.toggle('on', x.dataset.t === saveTitle);
        x.setAttribute('aria-pressed', String(x.dataset.t === saveTitle));
      });
      $('saveAddr').checked = true;
    });
    $('saveAddr').addEventListener('change', () => { $('saveTitles').hidden = !$('saveAddr').checked; });
  }

  // ── Скидка и баллы ──────────────────────────────────────────
  // Сервер считает скидку по корзине (/api/cart/promo): с промокодом
  // или без — персональная скидка есть и без него. Баллы — до 30%
  // товаров после скидки и не больше, чем на счету
  async function loyalty(code){
    try {
      const r = await fetch('/api/cart/promo', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: code || ''})});
      const d = await r.json();
      if (code && d.error){ fieldError($('promoCode'), d.error); }
      else fieldError($('promoCode'), '');
      L.discount = +d.discount; L.source = d.source; L.code = d.code;
      L.balance = d.bonus_balance; L.max = d.bonus_max;
      // Код применён — поле прячем, показываем плашку с кодом и крестиком
      const applied = d.source === 'promo';
      if (applied || (code && d.error)) promoShow(true);
      $('promoBox').hidden = applied;
      $('promoTag').hidden = !applied;
      if (applied){
        $('promoTagCode').textContent = d.code;
        $('promoTagNote').textContent = 'скидка ' + (d.kind === 'percent' ? Math.round(+d.value) + '%' : rub(d.value))
          + ' · −' + rub(d.discount);
      }
      // Персональная скидка или пояснение сервера («ваша скидка выгоднее»)
      const note = d.note || (d.source === 'personal'
        ? `Действует ваша персональная скидка ${Math.round(+d.value * 10) / 10}%` : '');
      $('promoOk').hidden = !note;
      $('promoOk').textContent = note;
      $('bonusRow').hidden = !L.balance;
      $('bonusBal').textContent = L.balance.toLocaleString('ru');
      $('bonusHint').textContent = !L.balance ? ''
        : L.max ? `1 балл = 1 ₽. Можно списать до ${L.max.toLocaleString('ru')} — это до 30% стоимости товаров`
        : 'Списать баллы можно, когда в заказе есть товары с ценой';
      $('bonusOn').disabled = !L.max;
      $('bonusAmt').max = $('bonusRange').max = L.max;
      if (L.bonus > L.max){ L.bonus = L.max; bonusShow(); }
      sync();
    } catch { fieldError($('promoCode'), code ? 'Нет связи с сервером — попробуйте ещё раз' : ''); }
    finally { $('promoGo').disabled = false; $('promoGo').textContent = 'Применить'; }
  }
  // Сначала проверка в браузере (те же правила, что у менеджера при
  // заведении кода), потом сервер: есть ли такой, действует ли
  $('promoGo').onclick = () => {
    const code = $('promoCode').value.trim();
    const err = Check.promo(code);
    if (err){ fieldError($('promoCode'), err); $('promoCode').focus(); return; }
    $('promoGo').disabled = true; $('promoGo').textContent = 'Проверяем…';
    loyalty(code);
  };
  // «Есть промокод?» раскрывает поле; код применён — поле видно и так
  function promoShow(on){
    $('promoFld').hidden = !on;
    $('promoOpen').hidden = on;
    $('promoOpen').setAttribute('aria-expanded', String(on));
  }
  $('promoOpen').onclick = () => { promoShow(true); $('promoCode').focus(); };
  $('promoDrop').onclick = () => {
    $('promoCode').value = '';
    loyalty('');
    $('promoCode').focus();
  };
  $('promoCode').addEventListener('keydown', e => {
    if (e.key === 'Enter'){ e.preventDefault(); $('promoGo').click(); }
  });
  $('promoCode').addEventListener('input', () => {
    $('promoCode').value = $('promoCode').value.toUpperCase().replace(/\s/g, '');
    if ($('promoCode').classList.contains('is-bad')) fieldError($('promoCode'), '');
  });

  // Баллы: переключатель, число и ползунок. Больше, чем можно, не
  // обрезаем молча — говорим, сколько можно; спишется не больше этого
  const bonusShow = () => {
    $('bonusAmt').value = L.bonus || '';
    $('bonusRange').value = L.bonus;
    $('bonusRange').style.setProperty('--p', (L.max ? L.bonus / L.max * 100 : 0) + '%');
  };
  $('bonusOn').onchange = () => {
    $('bonusUse').hidden = !$('bonusOn').checked;
    L.bonus = $('bonusOn').checked ? L.max : 0;
    fieldError($('bonusAmt'), '');
    bonusShow(); sync();
  };
  $('bonusAmt').addEventListener('input', () => {
    const raw = $('bonusAmt').value;
    fieldError($('bonusAmt'), Check.bonus(raw, L.max));
    L.bonus = Math.max(0, Math.min(L.max, Math.floor(+raw || 0)));
    $('bonusRange').value = L.bonus;
    $('bonusRange').style.setProperty('--p', (L.max ? L.bonus / L.max * 100 : 0) + '%');
    sync();
  });
  // Ушли с поля — в нём то, что действительно спишется
  $('bonusAmt').addEventListener('blur', () => setTimeout(() => {
    if (!$('bonusAmt').classList.contains('is-bad')) return;
    bonusShow();
    setTimeout(() => fieldError($('bonusAmt'), ''), 2500);
  }, 200));
  $('bonusRange').addEventListener('input', () => {
    L.bonus = +$('bonusRange').value; fieldError($('bonusAmt'), ''); bonusShow(); sync();
  });
  $('bonusAll').onclick = () => { L.bonus = L.max; fieldError($('bonusAmt'), ''); bonusShow(); sync(); };
  loyalty('');

  phoneMask($('cphone'));
  if ($('cphone').value) $('cphone').dispatchEvent(new Event('input'));

  // ── Проверка полей ───────────────────────────────────────────
  // Адресные поля проверяются, только когда они на экране: validate()
  // пропускает спрятанное — для пункта выдачи адрес не нужен
  // Правила полей — static/js/validation/rules.js
  const rules = [
    [$('cname'), v => Check.fio(v, {noPatronymic: $('cnoPat').checked})],
    [$('cphone'), v => Check.phoneRule(v)],
    [$('branch'), v => v ? '' : 'Выберите филиал'],
    [$('dCity'), v => Check.city(v)],
    // Для пункта выдачи улица и дом необязательны
    [$('aStreet'), v => isPvz() && !v.trim() ? '' : Check.street(v)],
    [$('aHouse'), v => isPvz() && !v.trim() ? '' : Check.house(v)],
    [$('aBlock'), v => Check.addrPart(v)],
    [$('aFlat'), v => Check.addrPart(v)],
    [$('dPost'), v => { const o = opt(); return Check.postcode(v, {required: !!o && o.mode === 'post'}); }],
    [$('cmt'), v => Check.text(v, {max: 1000, what: 'Комментарий'})],
  ];
  live(rules);
  fioCase($('cname'));
  // «Нет отчества» меняет правило — ошибку у поля пересчитываем сразу
  $('cnoPat').onchange = () => {
    if ($('cname').value.trim()) fieldError($('cname'), Check.fio($('cname').value, {noPatronymic: $('cnoPat').checked}));
  };
  $('agree').onchange = () => { if ($('agree').checked) fieldError($('agree'), ''); };
  // Промокод ввели, но не применили — не теряем его молча при оформлении
  const promoPending = () => !$('promoFld').hidden && !$('promoBox').hidden && $('promoCode').value.trim() && !L.code;

  form.onsubmit = async e => {
    e.preventDefault();
    let ok = validate(rules);
    const ship = val('dm') === 'shipping';
    const o = ship && hasCarriers ? opt() : null;
    if (ship && hasCarriers && ok){
      if (!o){ toast('Выберите вариант доставки'); ok = false; }
      else if (o.mode === 'pvz' && !D.point){
        $('dPointErr').textContent = 'Выберите пункт выдачи'; $('dPointErr').hidden = false;
        $('dPointQ').focus(); ok = false;
      }
    }
    if (promoPending()){
      fieldError($('promoCode'), 'Нажмите «Применить» или сотрите код');
      if (ok) $('promoCode').focus();
      ok = false;
    }
    if (!$('agree').checked){
      fieldError($('agree'), 'Без согласия мы не можем принять заказ');
      if (ok) $('agree').focus();
      ok = false;
    }
    if (!ok) return;

    $('place').disabled = true;
    try {
      const pm = val('pm');
      const addr = ship && !(o && o.mode === 'pvz');
      const r = await fetch('/api/orders', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          contact_name: $('cname').value.trim(),
          no_patronymic: $('cnoPat').checked,
          contact_phone: $('cphone').value.trim(),
          delivery_method: val('dm'),
          pickup_branch_id: ship ? null : +$('branch').value,
          delivery_carrier: o ? o.carrier : null,
          delivery_mode: o ? o.mode : null,
          delivery_city: ship ? $('dCity').value.trim() : null,
          delivery_cdek_code: o ? D.cdek_code : null,
          delivery_point: o && o.mode === 'pvz' ? D.point : null,
          // Адрес по полям — сервер проверит и соберёт строку сам
          delivery_country: addr ? $('dCountry').value : null,
          delivery_street: addr ? $('aStreet').value.trim() : null,
          delivery_house: addr ? $('aHouse').value.trim() : null,
          delivery_block: addr ? $('aBlock').value.trim() || null : null,
          delivery_flat: addr ? $('aFlat').value.trim() || null : null,
          delivery_postcode: ship ? postcode() : null,
          promo_code: L.code,
          save_address: !!($('saveAddr') && $('saveAddr').checked && !$('saveAddrBox').hidden),
          address_title: saveTitle,
          bonus: L.bonus,
          payment_method: pm === 'on_receipt' ? 'on_receipt' : 'online',
          pay_with: pm === 'on_receipt' ? null : pm,
          comment: $('cmt').value.trim() || null,
          agree: true,
        })});
      const d = await r.json().catch(() => ({}));
      if (r.ok){
        // Онлайн — сразу к оплате; банк не ответил — на страницу заказа,
        // оплатить можно оттуда
        location.href = d.redirect_url || ('/account/orders/' + d.number
          + (d.payment_error ? '?payerr=1' : ''));
        return;
      }
      if (!serverErrors(d, {contact_name: $('cname'), contact_phone: $('cphone'),
                            delivery_city: $('dCity'), delivery_street: $('aStreet'),
                            delivery_house: $('aHouse'), delivery_postcode: $('dPost'),
                            comment: $('cmt')}))
        toast(typeof d.detail === 'string' ? d.detail : 'Не получилось оформить');
    } catch { toast('Нет связи с сервером'); }
    $('place').disabled = false;
  };
}

// Сводка справа прилипает к экрану. Помещается — держится сверху под
// шапкой; выше экрана (ноутбук, крупный масштаб) — держится низом, и
// кнопка «Оформить» видна всегда, а не только в самом конце страницы
(function(){
  const side = document.querySelector('.co-side');
  if (!side || !window.ResizeObserver) return;
  new ResizeObserver(() => side.style.setProperty('--side-h', side.offsetHeight + 'px')).observe(side);
})();
