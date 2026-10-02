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

  // ── Доставка ─────────────────────────────────────────────────
  // Страна и город → варианты служб с ценой → пункт выдачи или адрес
  // по полям. Цена здесь — только показать; при оформлении сервер
  // считает её заново
  const shipBox = $('shipBox');
  const hasCarriers = !!$('dOptions');
  const hints = shipBox && shipBox.dataset.hints === '1';
  const D = {city: '', cdek_code: null, options: [], point: null, points: [], street_fias: ''};
  const opt = () => D.options.find(o => `${o.carrier}:${o.mode}` === val('dopt')) || null;
  const postcode = () => $('dPost').value.length === 6 ? $('dPost').value : null;

  // Сводка справа повторяет выбор — его видно рядом с итогом и кнопкой
  const sync = () => {
    const ship = val('dm') === 'shipping';
    const o = ship && hasCarriers ? opt() : null;
    $('pickupBox').hidden = ship;
    shipBox.hidden = !ship;
    // Адрес не нужен только для пункта выдачи: его адрес — у пункта
    $('addrBox').hidden = !ship || !!(o && o.mode === 'pvz');
    if (hasCarriers) $('dPointBox').hidden = !(o && o.mode === 'pvz');
    // Выбранный пункт — и в сводке: адрес рядом с итогом и кнопкой
    const pt = o && o.mode === 'pvz' && D.points.find(x => x.code === D.point);
    $('sumPointL').hidden = $('sumPoint').hidden = !pt;
    if (pt) $('sumPoint').textContent = pt.address;
    const br = $('branch').selectedOptions[0];
    $('sumShip').textContent = !ship ? 'Самовывоз' + (br ? ' — ' + br.text : '')
      : o ? o.title : hasCarriers ? 'Доставка — выберите вариант' : 'Доставка ТК';
    $('shipLine').textContent = !ship ? 'бесплатно'
      : o ? (o.from && !D.point ? 'от ' : '') + rub(o.price) : hasCarriers ? '—' : 'по тарифу ТК';
    const goods = +$('grandTotal').dataset.goods;
    $('grandTotal').textContent = rub(goods + (o ? +o.price : 0));
    $('sumPay').textContent = picked('pm') ? picked('pm').dataset.label : '';
    $('place').textContent = val('pm') && val('pm') !== 'on_receipt'
      ? 'Оформить и оплатить' : 'Оформить заказ';
  };
  form.querySelectorAll('input[name=dm], input[name=pm]').forEach(r => r.onchange = sync);
  $('branch').onchange = sync;

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
    if (hasCarriers){ $('dOptions').innerHTML = ''; $('dState').textContent = 'Укажите город — посчитаем стоимость и срок доставки.'; }
    sync();
  };
  document.addEventListener('click', e => {
    if (!e.target.closest('.city-fld')) $('dCityList').hidden = true;
    if (!e.target.closest('.a-street')) $('aStreetList').hidden = true;
    if (!e.target.closest('.a-house')) $('aHouseList').hidden = true;
  });

  // ── Варианты служб и пункты выдачи ───────────────────────────
  let qseq = 0;
  async function loadQuotes(point){
    if (!hasCarriers) return;
    const city = $('dCity').value.trim();
    if (city.length < 2) return;
    const my = ++qseq;
    if (!point){ $('dState').textContent = 'Считаем доставку…'; $('dOptions').innerHTML = ''; }
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
      if (o && y){ o.price = y.price; o.days = y.days; o.from = false; $('dPointErr').hidden = true; }
      else {
        $('dPointErr').textContent = 'В этот пункт Яндекс не доставляет — выберите другой';
        $('dPointErr').hidden = false; D.point = null;
        drawPoints(); showPicked();
      }
      drawOptions(); sync(); return;
    }
    const keep = val('dopt');
    D.city = city; D.options = d.options || [];
    if (!D.options.some(o => `${o.carrier}:${o.mode}` === keep)) D.point = null;
    $('dState').textContent = D.options.length
      ? `Отправим из филиала: ${d.from}.`
        + (d.need_postcode ? ' Почта России посчитает, когда будет индекс.' : '')
        + ' Выберите вариант:'
      : 'Служба не посчитала доставку в этот город. Проверьте название'
        + (d.need_postcode ? ' или укажите адрес с индексом' : '') + ' — или выберите самовывоз.';
    drawOptions(keep); sync();
  }

  function drawOptions(keep){
    const cur = keep || val('dopt');
    $('dOptions').innerHTML = D.options.map(o => {
      const k = `${o.carrier}:${o.mode}`;
      return `<label class="tile"><input type="radio" name="dopt" value="${k}" ${k === cur ? 'checked' : ''}>
        <span><b>${esc(o.title)}</b>
        <small>${o.from && !D.point ? 'от ' : ''}${rub(o.price)}${o.days ? ' · ' + esc(o.days) : ''}</small></span></label>`;
    }).join('');
    $('dOptions').querySelectorAll('input').forEach(i => i.onchange = () => {
      D.point = null; loadPoints(); sync();
    });
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
    $('dPointPicked').hidden = !p;
    $('dPointPick').hidden = !!p;
    if (!p) return;
    $('dPointAddr').textContent = p.address;
    $('dPointMeta').textContent = p.name + (p.hours ? ' · ' + p.hours : '');
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

  phoneMask($('cphone'));
  if ($('cphone').value) $('cphone').dispatchEvent(new Event('input'));

  // ── Проверка полей ───────────────────────────────────────────
  // Адресные поля проверяются, только когда они на экране: validate()
  // пропускает спрятанное — для пункта выдачи адрес не нужен
  // Буквы — из validate.js (LETTERS): латиница и кириллица
  const ADDR_STREET = new RegExp(`^[${LETTERS}\\d .,'«»"()№/-]{2,120}$`);
  const ADDR_SHORT = new RegExp(`^[${LETTERS}\\d/ .-]{1,20}$`);
  const rules = [
    [$('cname'), v => Check.name(v)],
    [$('cphone'), v => Check.phoneRule(v)],
    [$('branch'), v => v ? '' : 'Выберите филиал'],
    [$('dCity'), v => Check.text(v, {min: 2, max: 120, what: 'Город'})],
    [$('aStreet'), v => !v.trim() ? 'Укажите улицу'
      : ADDR_STREET.test(v.trim()) ? '' : 'Улица: буквы, цифры, точка, дефис'],
    [$('aHouse'), v => !v.trim() ? 'Укажите дом'
      : /\d/.test(v) && ADDR_SHORT.test(v.trim()) ? '' : 'Дом: номер, например 10 или 10/2'],
    [$('aBlock'), v => !v.trim() || ADDR_SHORT.test(v.trim()) ? '' : 'До 20 символов: буквы и цифры'],
    [$('aFlat'), v => !v.trim() || ADDR_SHORT.test(v.trim()) ? '' : 'До 20 символов: буквы и цифры'],
    [$('dPost'), v => {
      const o = opt();
      if (!v) return o && o.mode === 'post' ? 'Для Почты России нужен индекс' : '';
      return /^\d{6}$/.test(v) ? '' : 'Индекс — шесть цифр';
    }],
    [$('cmt'), v => Check.text(v, {max: 1000, what: 'Комментарий'})],
  ];
  live(rules);
  $('agree').onchange = () => { if ($('agree').checked) fieldError($('agree'), ''); };

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
