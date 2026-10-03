// Скрипт шаблона templates/admin/order.html — карточка заказа.
//
// Как заказ в CRM: этапы сверху, данные блоками слева, лента справа.
// Покупатель ошибся — менеджер правит прямо здесь; сервер проверяет
// поля, пересчитывает сумму и пишет в ленту «было → стало». После
// каждой правки карточка перечитывается целиком.

const $ = id => document.getElementById(id);
const ID = +$('card').dataset.id;
let D = null;                       // карточка с сервера
const edit = {contact: false, receive: false};
let R = null;                       // правка получения: город, пункт, список пунктов
let recalc = null;                  // показанный пересчёт доставки

const STAGES = [['new', 'Новый'], ['confirmed', 'Подтверждён'], ['paid', 'Оплачен'],
                ['shipped', 'Отправлен'], ['completed', 'Выдан']];
const CARRIER = {cdek: 'СДЭК', yandex: 'Яндекс Доставка', pochta: 'Почта России'};
const MODE = {pvz: 'до пункта выдачи', door: 'курьером до двери', post: 'до отделения'};
const SHIP_ST = {assembling: 'собирается', sent: 'отправлена', delivered: 'доставлена',
                 cancelled: 'отменена'};
// Откуда деталь в заказе: оформлена из корзины или добавлена потом
const SOURCE = {cart: 'из корзины', customer: 'добавил покупатель', manager: 'добавил менеджер'};
const KIND = {created: 'Оформление', status: 'Статус', edit: 'Правка', item: 'Состав',
              shipment: 'Посылка', delivery: 'Доставка', payment: 'Оплата', note: 'Комментарий'};

const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const money = v => Math.round(+v || 0).toLocaleString('ru') + ' ₽';
const date = s => s ? new Date(s).toLocaleDateString('ru') : '';
const dt = s => s ? new Date(s).toLocaleString('ru', {day: '2-digit', month: '2-digit',
  year: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
// +79125554433 → +7 912 555-44-33
const phoneFmt = p => {
  const d = String(p || '').replace(/\D/g, '');
  return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : (p || '');
};
const COPY_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"
  fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3"
  fill="none" stroke="currentColor" stroke-width="1.8"/></svg>`;
const copyBtn = (t, title = 'Скопировать') =>
  `<button type="button" class="copy" data-copy="${esc(t)}" title="${title}" aria-label="${title}">${COPY_ICON}</button>`;

let tt;
function toast(m, k = ''){ const el = $('toast'); el.textContent = m;
  el.className = `toast show ${k}`;
  clearTimeout(tt); tt = setTimeout(() => el.className = 'toast', 2600); }

// Запрос к API: ошибка — тостом, результат — null
async function api(method, url, body){
  try {
    const r = await fetch(url, {method, headers: body ? {'Content-Type': 'application/json'} : {},
                                body: body ? JSON.stringify(body) : undefined});
    const d = await r.json().catch(() => ({}));
    if (!r.ok){
      toast(typeof d.detail === 'string' ? d.detail : 'Не получилось сохранить', 'err');
      return null;
    }
    return d;
  } catch {
    toast('Нет связи с сервером', 'err');
    return null;
  }
}

async function load(){
  const r = await fetch(`/api/manage/orders/${ID}`);
  if (!r.ok){ $('card').innerHTML = '<p class="blank">Заказ не найден</p>'; return; }
  D = await r.json();
  render();
}

const canEdit = () => D.can_edit && !D.edit_locked;
const canMoney = () => D.can_edit && !D.money_locked;

function render(){
  const o = D.order;
  $('card').innerHTML = `${head(o)}
    <div class="grid">
      <div class="main">${contact(o)}${receive(o)}${goods(o)}${parcels(o)}${payment(o)}${comments(o)}</div>
      <aside class="side">${feed(o)}</aside>
    </div>`;
  if (edit.contact) setupContact();
  if (edit.receive) setupReceive();
}

// ── Шапка и этапы ───────────────────────────────────────────────
function head(o){
  const idx = STAGES.findIndex(s => s[0] === o.status);
  return `<div class="c-head">
      <div><h1>Заказ № ${esc(o.number)}</h1>
        <p class="sub">от ${dt(o.created_at)}${o.source === 'site' ? ' · с сайта' : o.source ? ' · ' + esc(o.source) : ''}
          ${o.updated_at ? ' · изменён ' + dt(o.updated_at) : ''}</p></div>
      <div class="c-total"><small>Итого</small><b>${money(o.total)}</b>
        <small class="${o.paid_at ? 'ok' : ''}">${o.paid_at ? 'оплачен ' + date(o.paid_at)
          : 'не оплачен · ' + esc(D.order.payment_label || '')}</small></div>
    </div>
    <div class="stages">
      ${o.status === 'cancelled'
        ? '<div class="cancelled-bar">Заказ отменён — детали вернулись на склад</div>'
        : `<div class="stage-bar">${STAGES.map(([k, t], i) => `<button type="button" class="stage
            ${i < idx ? 'done' : ''} ${i === idx ? 'on' : ''}" data-st="${k}" ${D.can_edit ? '' : 'disabled'}>${t}</button>`).join('')}</div>
          ${D.can_edit && o.status !== 'completed' ? '<button type="button" class="btn btn-danger" id="cancelOrder">Отменить заказ</button>' : ''}`}
    </div>`;
}

// ── Покупатель ──────────────────────────────────────────────────
function contact(o){
  const c = D.customer;
  if (edit.contact) return `<section class="panel blk hl">
      <div class="blk-h"><h2>Покупатель</h2></div>
      <div class="f3">
        <div><label for="eName">ФИО получателя</label>
          <input id="eName" maxlength="80" autocomplete="off" value="${esc(o.contact_name || '')}"></div>
        <div><label for="ePhone">Телефон</label>
          <input id="ePhone" type="tel" autocomplete="off" value="${esc(phoneFmt(o.contact_phone))}"></div>
        <div><label for="eEmail">Email для чека</label>
          <input id="eEmail" type="email" autocomplete="off" value="${esc(o.contact_email || '')}"
                 placeholder="${esc((c && c.email) || 'не обязательно')}"></div>
      </div>
      <div class="actions-row"><button type="button" class="btn btn-accent" id="saveContact">Сохранить</button>
        <button type="button" class="btn" data-cancel="contact">Отмена</button></div>
    </section>`;
  const email = o.contact_email || (c && c.email);
  const ph = phoneFmt(o.contact_phone || (c && c.phone));
  return `<section class="panel blk hl">
      <div class="blk-h"><h2>Покупатель</h2>
        ${canEdit() ? '<button type="button" class="lnk" data-edit="contact">Изменить</button>' : ''}</div>
      <p class="big">${esc(o.contact_name || (c && c.name) || 'без имени')}
        ${o.contact_name ? copyBtn(o.contact_name, 'Скопировать ФИО') : ''}</p>
      <div class="contacts">
        ${ph ? `<span class="big-phone"><a href="tel:${esc(ph.replace(/[^+\d]/g, ''))}">${esc(ph)}</a>${copyBtn(ph, 'Скопировать телефон')}</span>`
             : '<span class="muted">телефона нет</span>'}
        ${email ? `<span class="mail"><a href="mailto:${esc(email)}">${esc(email)}</a>${copyBtn(email, 'Скопировать email')}</span>` : ''}
      </div>
      ${c ? `<p class="acct">Аккаунт на сайте: ${esc([phoneFmt(c.phone), c.email].filter(Boolean).join(', ') || c.name || '—')}
          · с ${date(c.created_at)} · заказов ${c.orders}${+c.spent ? ', оплачено на ' + money(c.spent) : ''}</p>` : ''}
    </section>`;
}

function setupContact(){
  phoneMask($('ePhone'));
  const rules = [
    [$('eName'), v => Check.name(v)],
    [$('ePhone'), v => Check.phoneRule(v)],
    [$('eEmail'), v => Check.emailRule(v, {optional: true})],
  ];
  live(rules);
  $('eName').focus();
  $('saveContact').onclick = async () => {
    if (!validate(rules)) return;
    const ok = await api('PATCH', `/api/manage/orders/${ID}`, {
      contact_name: $('eName').value.trim(), contact_phone: $('ePhone').value.trim(),
      contact_email: $('eEmail').value.trim()});
    if (ok){ edit.contact = false; toast('Сохранено', 'ok'); load(); }
  };
}

// ── Получение ───────────────────────────────────────────────────
function receive(o){
  const ship = o.delivery_method === 'shipping';
  const n = D.shipments.length;
  if (edit.receive) return receiveForm(o);
  const title = ship ? (o.delivery_carrier ? `${CARRIER[o.delivery_carrier]} — ${MODE[o.delivery_mode] || ''}`
                                           : 'Доставка транспортной компанией') : 'Самовывоз';
  const addr = ship ? (o.delivery_address || '') : (o.pickup_branch || '');
  const courier = [o.contact_name, phoneFmt(o.contact_phone), addr].filter(Boolean).join('\n');
  return `<section class="panel blk hl">
      <div class="blk-h"><h2>Получение</h2>
        ${canEdit() ? '<button type="button" class="lnk" data-edit="receive">Изменить</button>' : ''}</div>
      <p class="kind">${esc(title)}</p>
      <p class="big addr">${esc(addr || (ship ? 'адрес не указан' : 'филиал не выбран'))}${addr ? copyBtn(addr, 'Скопировать адрес') : ''}</p>
      <p class="muted">${[ship && o.delivery_city, ship && o.delivery_postcode && 'индекс ' + o.delivery_postcode,
                          n > 1 && `${n} посылки — придут в разные дни`, o.delivery_days && 'срок ' + o.delivery_days]
                         .filter(Boolean).map(esc).join(' · ')}</p>
      <div class="actions-row">
        ${ship ? `<button type="button" class="btn" data-copy="${esc(courier)}">Скопировать для курьера</button>` : ''}
        ${o.delivery_carrier && D.can_edit ? '<button type="button" class="btn" id="recalcBtn">Пересчитать доставку</button>' : ''}
      </div>
      ${recalc ? recalcBox() : ''}
    </section>`;
}

function recalcBox(){
  const same = +recalc.old === +recalc.new;
  return `<div class="recalc">
      <p>Доставка по нынешнему адресу и составу: <b>${money(recalc.old)} → ${money(recalc.new)}</b>
        ${recalc.days ? ', срок ' + esc(recalc.days) : ''}${same ? ' — цена та же' : ''}</p>
      ${recalc.parcels.length > 1 ? `<ul>${recalc.parcels.map(p =>
        `<li><span>Посылка ${esc(p.from_city)}</span><span>${money(p.old)} → ${money(p.new)}${p.days ? ' · ' + esc(p.days) : ''}</span></li>`).join('')}</ul>` : ''}
      <div class="actions-row">
        ${!same && canMoney() ? '<button type="button" class="btn btn-accent" id="recalcApply">Применить новую цену</button>' : ''}
        ${!same && !canMoney() ? `<span class="muted">${esc(D.money_locked || '')}</span>` : ''}
        <button type="button" class="btn" id="recalcHide">Скрыть</button>
      </div>
    </div>`;
}

function receiveForm(o){
  const pvz = o.delivery_mode === 'pvz';
  const noShips = !D.shipments.length;
  return `<section class="panel blk hl">
      <div class="blk-h"><h2>Получение</h2></div>
      <div class="seg" role="radiogroup">
        <label><input type="radio" name="eMethod" value="shipping" ${R.method === 'shipping' ? 'checked' : ''}> Доставка</label>
        <label><input type="radio" name="eMethod" value="pickup" ${R.method === 'pickup' ? 'checked' : ''}> Самовывоз</label>
      </div>
      <div id="ePickup" ${R.method === 'pickup' ? '' : 'hidden'}>
        <label for="eBranch">Филиал самовывоза</label>
        <select id="eBranch"><option value="">Загружаю…</option></select>
        ${o.delivery_method === 'shipping' && o.delivery_carrier
          ? '<p class="hint">Посылки отменятся, доставка уйдёт из суммы заказа.</p>' : ''}
      </div>
      <div id="eShip" ${R.method === 'shipping' ? '' : 'hidden'}>
        ${o.delivery_carrier ? `<p class="hint">${esc(CARRIER[o.delivery_carrier])} — ${esc(MODE[o.delivery_mode] || '')}.
            После смены адреса проверьте цену кнопкой «Пересчитать доставку».</p>` : ''}
        <div class="sug">
          <label for="eCity">Город</label>
          <input id="eCity" autocomplete="off" maxlength="120" value="${esc(R.city || '')}">
          <div class="suggest" id="eCityList" hidden></div>
        </div>
        ${pvz && o.delivery_method === 'shipping' ? `
          <label for="ePointQ">Пункт выдачи</label>
          <input id="ePointQ" placeholder="Поиск по адресу или коду пункта" autocomplete="off">
          <div class="points" id="ePoints"><p class="hint">Загружаю пункты…</p></div>
          <p class="fld-err" id="ePointErr" hidden></p>` : `
          ${o.delivery_address && !o.delivery_street ? `<p class="hint">Сейчас: ${esc(o.delivery_address)}</p>` : ''}
          <div class="addr">
            <div><label for="eCountry">Страна</label>
              <select id="eCountry">${[['RU', 'Россия'], ['BY', 'Беларусь'], ['KZ', 'Казахстан']].map(([k, t]) =>
                `<option value="${k}" ${(o.delivery_country || 'RU') === k ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
            <div><label for="ePost">Индекс</label>
              <input id="ePost" inputmode="numeric" maxlength="6" value="${esc(o.delivery_postcode || '')}"></div>
            <div class="wide sug"><label for="eStreet">Улица</label>
              <input id="eStreet" maxlength="120" autocomplete="off" value="${esc(o.delivery_street || '')}">
              <div class="suggest" id="eStreetList" hidden></div></div>
            <div class="sug"><label for="eHouse">Дом</label>
              <input id="eHouse" maxlength="20" autocomplete="off" value="${esc(o.delivery_house || '')}">
              <div class="suggest" id="eHouseList" hidden></div></div>
            <div><label for="eBlock">Корпус</label><input id="eBlock" maxlength="20" value="${esc(o.delivery_block || '')}"></div>
            <div><label for="eFlat">Кв. / офис</label><input id="eFlat" maxlength="20" value="${esc(o.delivery_flat || '')}"></div>
          </div>`}
        ${!o.delivery_carrier && noShips ? `<div class="price1"><label for="ePrice">Цена доставки, ₽</label>
          <input id="ePrice" type="number" min="0" step="1" value="${Math.round(+o.delivery_price || 0)}"
                 ${canMoney() ? '' : 'disabled'}></div>` : ''}
      </div>
      <div class="actions-row"><button type="button" class="btn btn-accent" id="saveReceive">Сохранить</button>
        <button type="button" class="btn" data-cancel="receive">Отмена</button></div>
    </section>`;
}

async function setupReceive(){
  const o = D.order;
  formRadio();
  // Филиалы для самовывоза
  fetch('/api/branches').then(r => r.json()).then(rows => {
    $('eBranch').innerHTML = '<option value="">Выберите филиал</option>' + rows.map(b =>
      `<option value="${b.id}" ${b.id === (o.pickup_branch_id || 0) ? 'selected' : ''}>${esc(b.label)}</option>`).join('');
  });
  // Город — подсказка из справочника СДЭК: у города там код для тарифов
  let ct, seq = 0;
  $('eCity').addEventListener('input', () => {
    R.cdek_code = null;
    clearTimeout(ct);
    const q = $('eCity').value.trim();
    if (q.length < 2){ $('eCityList').hidden = true; return; }
    ct = setTimeout(async () => {
      const my = ++seq;
      const rows = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(q))).json();
      if (my !== seq) return;
      $('eCityList').innerHTML = rows.map((c, i) =>
        `<button type="button" data-i="${i}">${esc(c.full_name)}</button>`).join('');
      $('eCityList').hidden = !rows.length;
      $('eCityList').querySelectorAll('button').forEach(b => b.onclick = () => {
        const c = rows[+b.dataset.i];
        $('eCity').value = c.name; R.city = c.name; R.cdek_code = c.cdek_code;
        $('eCityList').hidden = true; fieldError($('eCity'), '');
        if ($('ePoints')){ R.point = null; loadPoints(); }
      });
    }, 250);
  });
  if ($('ePost')) $('ePost').addEventListener('input', () => {
    $('ePost').value = $('ePost').value.replace(/\D/g, '').slice(0, 6);
  });
  if ($('ePoints')){
    $('ePointQ').addEventListener('input', drawPoints);
    loadPoints();
  }
  // Подсказки улицы и дома, индекс по адресу (static/js/addr_suggest.js)
  addressSuggest({city: $('eCity'), street: $('eStreet'), house: $('eHouse'), block: $('eBlock'),
                  post: $('ePost'), streetList: $('eStreetList'), houseList: $('eHouseList'),
                  country: () => ($('eCountry') ? $('eCountry').value : 'RU')});
  $('saveReceive').onclick = saveReceive;
}

function formRadio(){
  document.querySelectorAll('input[name=eMethod]').forEach(i => i.onchange = () => {
    R.method = i.value;
    $('ePickup').hidden = R.method !== 'pickup';
    $('eShip').hidden = R.method !== 'shipping';
  });
}

async function loadPoints(){
  const o = D.order;
  const city = $('eCity').value.trim();
  if (city.length < 2) return;
  if (!R.cdek_code && o.delivery_carrier === 'cdek'){
    const c = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(city))).json();
    R.cdek_code = c.length ? c[0].cdek_code : null;
  }
  const p = new URLSearchParams({carrier: o.delivery_carrier, city});
  if (R.cdek_code) p.set('cdek_code', R.cdek_code);
  R.points = await (await fetch('/api/delivery/points?' + p)).json();
  drawPoints();
}

function drawPoints(){
  const q = $('ePointQ').value.trim().toLowerCase();
  const list = R.points.filter(p => !q || (p.code + ' ' + p.name + ' ' + p.address).toLowerCase().includes(q))
                       .slice(0, 80);
  // Выбранный пункт — всегда первым: его видно без прокрутки
  const cur = R.points.find(p => p.code === R.point);
  const rows = cur ? [cur, ...list.filter(p => p !== cur)] : list;
  $('ePoints').innerHTML = rows.length ? rows.map(p => `
    <label class="pt"><input type="radio" name="ePoint" value="${esc(p.code)}" ${p.code === R.point ? 'checked' : ''}>
      <span><b>${esc(p.address)}</b><small>${esc(p.name)}${p.hours ? ' · ' + esc(p.hours) : ''}</small></span></label>`).join('')
    : '<p class="hint">Пунктов не нашлось — проверьте город</p>';
  $('ePoints').querySelectorAll('input').forEach(i => i.onchange = () => {
    R.point = i.value; $('ePointErr').hidden = true;
  });
}

const ADDR_SHORT = new RegExp(`^[${LETTERS}\\d/ .-]{1,20}$`);
async function saveReceive(){
  const o = D.order;
  let body;
  if (R.method === 'pickup'){
    if (!$('eBranch').value){ fieldError($('eBranch'), 'Выберите филиал'); return; }
    if (o.delivery_method === 'shipping' && D.shipments.length
        && !await askConfirm('Посылки отменятся, доставка уйдёт из суммы заказа.',
                             {title: 'Перевести на самовывоз?', ok: 'Перевести'})) return;
    body = {delivery_method: 'pickup', pickup_branch_id: +$('eBranch').value};
  } else {
    const rules = [[$('eCity'), v => Check.text(v, {min: 2, max: 120, what: 'Город'})]];
    if ($('eStreet')) rules.push(
      [$('eStreet'), v => v.trim().length >= 2 ? '' : 'Укажите улицу'],
      [$('eHouse'), v => /\d/.test(v) && ADDR_SHORT.test(v.trim()) ? '' : 'Дом: номер, например 10 или 10/2'],
      [$('ePost'), v => !v ? (o.delivery_mode === 'post' ? 'Для Почты нужен индекс' : '')
                           : /^\d{6}$/.test(v) ? '' : 'Индекс — шесть цифр']);
    if (!validate(rules)) return;
    body = {delivery_method: 'shipping', delivery_city: $('eCity').value.trim(),
            delivery_cdek_code: R.cdek_code || null};
    if ($('ePoints')){
      if (!R.point){ $('ePointErr').textContent = 'Выберите пункт выдачи'; $('ePointErr').hidden = false; return; }
      body.delivery_point = R.point;
    } else {
      Object.assign(body, {
        delivery_country: $('eCountry').value, delivery_postcode: $('ePost').value || null,
        delivery_street: $('eStreet').value.trim(), delivery_house: $('eHouse').value.trim(),
        delivery_block: $('eBlock').value.trim() || null, delivery_flat: $('eFlat').value.trim() || null});
    }
    if ($('ePrice') && !$('ePrice').disabled) body.delivery_price = +$('ePrice').value || 0;
  }
  $('saveReceive').disabled = true;
  const res = await api('PATCH', `/api/manage/orders/${ID}`, body);
  if (!res){ $('saveReceive').disabled = false; return; }
  edit.receive = false;
  toast('Сохранено', 'ok');
  await load();
  // Адрес другой — у службы может быть другая цена: сразу показываем
  if (res.need_recalc) runRecalc(false);
}

async function runRecalc(apply){
  const btn = $(apply ? 'recalcApply' : 'recalcBtn');
  if (btn){ btn.disabled = true; btn.textContent = apply ? 'Применяю…' : 'Считаю…'; }
  const d = await api('POST', `/api/manage/orders/${ID}/recalc`, {apply});
  if (apply && d){ recalc = null; toast('Цена доставки обновлена', 'ok'); load(); return; }
  recalc = d; render();
}

// ── Состав ──────────────────────────────────────────────────────
function goods(o){
  const qty = D.items.reduce((s, i) => s + i.qty, 0);
  const n = D.shipments.length;
  const m = canMoney();
  return `<section class="panel blk">
      <div class="blk-h"><h2>Состав заказа</h2><span class="muted">${qty} шт</span></div>
      ${D.money_locked && D.can_edit && o.status !== 'cancelled'
        ? `<p class="lock">Состав и цены не меняются: ${esc(D.money_locked.toLowerCase())}</p>` : ''}
      <div class="items">${D.items.map(i => `
        <div class="it" data-item="${i.id}">
          <a class="pic" href="/p/${encodeURIComponent(i.sku)}" target="_blank" rel="noopener">
            ${i.photo ? `<img src="${esc(i.photo)}" alt="" loading="lazy">` : ''}</a>
          <div class="nm"><a href="/p/${encodeURIComponent(i.sku)}" target="_blank" rel="noopener">${esc(i.name)}</a>
            <small><span class="mono">${esc(i.sku)}</span> · ${esc(i.branch || 'без филиала')}
              ${i.location ? ` · <b class="shelf">полка ${esc(i.location)}</b>` : ''} · сост. ${esc(i.condition || '—')}
              · <span class="src src-${esc(i.source || 'cart')}">${SOURCE[i.source] || SOURCE.cart}</span>
              ${+i.price_now && +i.price_now !== +i.price ? ` · на витрине ${money(i.price_now)}` : ''}</small></div>
          ${m ? `<label class="mini">Шт<input class="i-qty" type="number" min="1" max="999" value="${i.qty}"></label>
                 <label class="mini">Цена, ₽<input class="i-price" type="number" min="0" step="1" value="${Math.round(+i.price)}"></label>`
              : `<span class="q">${i.qty} шт × ${money(i.price)}</span>`}
          <b class="sum">${money(i.sum)}</b>
          ${m ? '<button type="button" class="del" title="Убрать из заказа" aria-label="Убрать из заказа">×</button>' : ''}
        </div>`).join('')}</div>
      ${m ? `<div class="add">
          <input id="addSku" placeholder="Добавить деталь: артикул, например D-0046-0002" autocomplete="off">
          <input id="addQty" type="number" min="1" max="999" value="1" aria-label="Количество">
          <button type="button" class="btn" id="addItem">Добавить</button></div>` : ''}
      <dl class="totals">
        <dt>Товары</dt><dd>${money(D.goods)}</dd>
        <dt>Доставка${n > 1 ? `, ${n} посылки` : ''}</dt><dd>${money(o.delivery_price)}</dd>
        <dt class="t">Итого</dt><dd class="t">${money(o.total)}</dd>
      </dl>
    </section>`;
}

// ── Посылки ─────────────────────────────────────────────────────
function parcels(o){
  if (!D.shipments.length) return '';
  const m = canMoney();
  return `<section class="panel blk">
      <div class="blk-h"><h2>Посылки</h2><span class="muted">у каждой свой статус и номер</span></div>
      ${D.shipments.map((s, n) => {
        const live = s.status !== 'cancelled' && o.status !== 'cancelled' && D.can_edit;
        const lines = D.items.filter(i => i.shipment_id === s.id);
        return `<div class="ship" data-ship="${s.id}">
          <div class="sh"><b>${D.shipments.length > 1 ? `Посылка ${n + 1} ` : 'Посылка '}${esc(s.from_city)}</b>
            <span class="st ${s.status}">${SHIP_ST[s.status] || s.status}</span>
            <span class="muted">${esc(CARRIER[s.carrier] || '')}${s.tariff ? ', тариф ' + esc(s.tariff) : ''}${s.days ? ', ' + esc(s.days) : ''}
              ${s.weight_g ? ' · ' + (s.weight_g / 1000).toLocaleString('ru') + ' кг' : ''} · ${money(s.price)}</span></div>
          <p class="ship-items">${lines.map(i => `${esc(i.sku)} ${esc(i.name)}${i.qty > 1 ? ' × ' + i.qty : ''}${i.location ? ` <b class="shelf">[${esc(i.location)}]</b>` : ''}`).join(', ') || '—'}</p>
          ${live ? `<div class="ship-edit">
            <label class="mini">Статус<select class="f-ship">${['assembling', 'sent', 'delivered'].map(k =>
              `<option value="${k}" ${k === s.status ? 'selected' : ''}>${SHIP_ST[k]}</option>`).join('')}</select></label>
            <label class="mini grow">Номер для отслеживания<input class="f-track" maxlength="40" value="${esc(s.track_number || '')}"></label>
            ${m ? `<label class="mini">Цена, ₽<input class="f-sprice" type="number" min="0" step="1" value="${Math.round(+s.price || 0)}"></label>` : ''}
            <button type="button" class="btn btn-accent ship-save">Сохранить</button>
            ${s.track_url ? `<a class="lnk" href="${esc(s.track_url)}" target="_blank" rel="noopener">где посылка →</a>` : ''}
          </div>` : (s.track_number ? `<p class="muted">номер ${esc(s.track_number)}</p>` : '')}
        </div>`;
      }).join('')}
    </section>`;
}

// ── Оплата ──────────────────────────────────────────────────────
function payment(o){
  return `<section class="panel blk">
      <div class="blk-h"><h2>Оплата</h2></div>
      <dl class="facts">
        <dt>Способ</dt><dd>${esc(o.payment_label || '—')}</dd>
        <dt>Статус</dt><dd>${o.paid_at ? `<span class="ok">оплачен ${dt(o.paid_at)}</span>` : 'не оплачен'}</dd>
      </dl>
      ${D.payments.length ? `<ul class="pays">${D.payments.map(p => `<li><span>${esc(p.method_label)}</span>
          <span>${money(p.amount)}</span><span class="pay-${p.status}">${esc(p.status_label)}, ${dt(p.created_at)}</span></li>`).join('')}</ul>`
        : '<p class="muted">Онлайн-оплат не было.</p>'}
    </section>`;
}

// ── Комментарии ─────────────────────────────────────────────────
function comments(o){
  return `<section class="panel blk">
      <div class="blk-h"><h2>Комментарии</h2></div>
      <label for="eComment">Комментарий покупателя</label>
      <textarea id="eComment" maxlength="1000" ${canEdit() ? '' : 'disabled'}>${esc(o.comment || '')}</textarea>
      <label for="eNote" class="mt">Заметка для сотрудников — покупатель её не видит</label>
      <textarea id="eNote" maxlength="4000" placeholder="Например: упаковать бампер в плёнку" ${D.can_edit ? '' : 'disabled'}>${esc(o.manager_note || '')}</textarea>
      ${D.can_edit ? '<div class="actions-row"><button type="button" class="btn btn-accent" id="saveComments">Сохранить</button></div>' : ''}
    </section>`;
}

// ── Лента ───────────────────────────────────────────────────────
function feed(o){
  const has = D.events.some(e => e.kind === 'created');
  return `<section class="panel feed">
      <h2>Лента</h2>
      <textarea id="noteText" maxlength="2000" placeholder="Комментарий для коллег: звонил, договорились, просит…"></textarea>
      <button type="button" class="btn btn-accent" id="addNote">Добавить</button>
      <ol class="ev">${D.events.map(e => `<li class="k-${esc(e.kind)}">
          <div class="ev-h"><b>${KIND[e.kind] || esc(e.kind)}</b><time>${dt(e.created_at)}</time></div>
          <p>${e.text.split('; ').map(esc).join('<br>')}</p>
          <small>${esc(e.who || 'покупатель / сайт')}</small></li>`).join('')}
        ${has ? '' : `<li class="k-created"><div class="ev-h"><b>Оформление</b><time>${dt(o.created_at)}</time></div>
          <p>Заказ оформлен${o.source === 'site' ? ' на сайте' : ''}</p><small>покупатель</small></li>`}
      </ol>
    </section>`;
}

// ── Действия ────────────────────────────────────────────────────
async function copy(t){
  try { await navigator.clipboard.writeText(t); toast('Скопировано', 'ok'); }
  catch {
    const a = document.createElement('textarea'); a.value = t; document.body.append(a);
    a.select(); document.execCommand('copy'); a.remove(); toast('Скопировано', 'ok');
  }
}

$('card').addEventListener('click', async e => {
  const t = e.target.closest('button, [data-copy]');
  if (!t) return;
  if (t.dataset.copy !== undefined){ copy(t.dataset.copy); return; }
  if (t.dataset.edit){
    edit[t.dataset.edit] = true;
    if (t.dataset.edit === 'receive'){
      const o = D.order;
      R = {method: o.delivery_method || 'shipping', city: o.delivery_city || '',
           cdek_code: o.delivery_cdek_code, point: o.delivery_point, points: []};
    }
    render(); return;
  }
  if (t.dataset.cancel){ edit[t.dataset.cancel] = false; render(); return; }
  if (t.classList.contains('stage')){
    const st = t.dataset.st, o = D.order;
    if (st === o.status) return;
    const title = t.textContent.trim();
    const text = ['paid', 'shipped', 'completed'].includes(st) && !o.paid_at
      ? 'Заказ будет отмечен оплаченным, детали без остатка — проданными.'
      : 'Покупатель увидит новый статус в личном кабинете.';
    if (!await askConfirm(text, {title: `Перевести заказ в «${title}»?`, ok: 'Перевести'})) return;
    if (await api('PATCH', `/api/manage/orders/${ID}`, {status: st})){ toast('Статус изменён', 'ok'); load(); }
    return;
  }
  if (t.id === 'cancelOrder'){
    if (!await askConfirm('Детали вернутся на склад и витрину, посылки отменятся. Вернуть заказ будет нельзя.',
                          {title: 'Отменить заказ?', ok: 'Отменить заказ', cancel: 'Не отменять',
                           danger: true})) return;
    if (await api('PATCH', `/api/manage/orders/${ID}`, {status: 'cancelled'})){ toast('Заказ отменён', 'ok'); load(); }
    return;
  }
  if (t.id === 'recalcBtn'){ runRecalc(false); return; }
  if (t.id === 'recalcApply'){ runRecalc(true); return; }
  if (t.id === 'recalcHide'){ recalc = null; render(); return; }
  if (t.classList.contains('del')){
    const it = D.items.find(i => i.id === +t.closest('[data-item]').dataset.item);
    if (!await askConfirm(`${it.sku} ${it.name}. Штуки вернутся на склад, сумма заказа пересчитается.`,
                          {title: 'Убрать деталь из заказа?', ok: 'Убрать', danger: true})) return;
    if (await api('DELETE', `/api/manage/orders/${ID}/items/${it.id}`)){ toast('Убрано', 'ok'); load(); }
    return;
  }
  if (t.id === 'addItem'){
    const sku = $('addSku').value.trim();
    if (sku.length < 3){ fieldError($('addSku'), 'Впишите артикул'); return; }
    t.disabled = true;
    const r = await api('POST', `/api/manage/orders/${ID}/items`, {sku, qty: +$('addQty').value || 1});
    t.disabled = false;
    if (r){ toast('Добавлено', 'ok'); await load(); if (r.need_recalc) runRecalc(false); }
    return;
  }
  if (t.classList.contains('ship-save')){
    const box = t.closest('[data-ship]');
    const body = {status: box.querySelector('.f-ship').value,
                  track_number: box.querySelector('.f-track').value.trim()};
    const pr = box.querySelector('.f-sprice');
    if (pr) body.price = +pr.value || 0;
    t.disabled = true;
    if (await api('PATCH', `/api/manage/shipments/${box.dataset.ship}`, body)){ toast('Сохранено', 'ok'); load(); }
    else t.disabled = false;
    return;
  }
  if (t.id === 'saveComments'){
    const body = {manager_note: $('eNote').value};
    if (!$('eComment').disabled) body.comment = $('eComment').value;
    if (await api('PATCH', `/api/manage/orders/${ID}`, body)){ toast('Сохранено', 'ok'); load(); }
    return;
  }
  if (t.id === 'addNote'){
    const v = $('noteText').value.trim();
    if (!v){ $('noteText').focus(); return; }
    t.disabled = true;
    if (await api('POST', `/api/manage/orders/${ID}/notes`, {text: v})){ load(); }
    else t.disabled = false;
  }
});

// Количество и цена строки сохраняются сразу, как поле отпустили
$('card').addEventListener('change', async e => {
  const inp = e.target.closest('.i-qty, .i-price');
  if (!inp) return;
  const row = inp.closest('[data-item]');
  const qty = +row.querySelector('.i-qty').value, price = +row.querySelector('.i-price').value;
  if (!(qty >= 1) || !(price >= 0)){ toast('Количество от 1, цена от 0', 'err'); return; }
  if (await api('PATCH', `/api/manage/orders/${ID}/items/${row.dataset.item}`, {qty, price})) toast('Сохранено', 'ok');
  load();
});

document.addEventListener('click', e => {
  if ($('eCityList') && !e.target.closest('.sug')) $('eCityList').hidden = true;
});

load();
