// Скрипт шаблона templates/admin/orders.html.

const $ = id => document.getElementById(id);
let status = '';
// Со сводки приходят сразу на нужную вкладку: /orders?tab=leads
let mode = new URLSearchParams(location.search).get('tab') === 'leads'
           ? 'leads' : 'orders';
const startStatus = new URLSearchParams(location.search).get('s') || '';

// С каких машин ещё можно снять деталь под заказ — для заявок по машине
const CAN_REMOVE = {accepted: '(ждёт разбора — можно снять)',
                    dismantling: '(в разборе — можно снять)'};

const STATUSES = {new:'новый', confirmed:'подтверждён', paid:'оплачен',
                  shipped:'отправлен', completed:'выдан', cancelled:'отменён'};

const money = v => v ? Number(v).toLocaleString('ru') + ' ₽' : '—';
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// Посылка: собирает и отправляет её филиал, где лежат детали
const SHIP_ST = {assembling: 'собирается', sent: 'отправлена', delivered: 'доставлена',
                 cancelled: 'отменена'};
const CARRIERS = {cdek: 'СДЭК', yandex: 'Яндекс Доставка', pochta: 'Почта России'};
const MY_BRANCH = +($('list').dataset.branch || 0);
const when = s => new Date(s).toLocaleDateString('ru');

$('mode').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.m;
  [...$('mode').children].forEach(x => x.classList.toggle('on', x === b));
  // Фильтр по статусу относится к заказам, у заявок его нет
  $('tabs').hidden = mode === 'leads';
  load();
};

$('tabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  status = b.dataset.s;
  [...$('tabs').children].forEach(x => x.classList.toggle('on', x === b));
  load();
};

async function load(){
  if (mode === 'leads') return loadLeads();
  const q = new URLSearchParams();
  if (status) q.set('status', status);
  if ($('mine') && $('mine').checked) q.set('mine', '1');
  const res = await fetch('/api/manage/orders?' + q);
  const rows = await res.json();
  if (!res.ok){ $('list').innerHTML = `<p class="blank">${esc(rows.detail || 'Не получилось загрузить')}</p>`; return; }
  if (!rows.length){ $('list').innerHTML = '<p class="blank">Заказов нет</p>'; return; }

  $('list').innerHTML = rows.map(o => `
    <div class="ord" data-id="${o.id}">
      <div class="h">
        <span class="num">№ ${o.number}</span>
        <span class="who">${o.customer_name || 'без имени'}
          <small>${o.phone || ''}</small></span>
        <span class="st ${o.status}">${STATUSES[o.status] || o.status}</span>
        <span class="total">${money(o.total)}</span>
      </div>
      <div class="meta">${when(o.created_at)} ·
        ${o.delivery_method === 'shipping'
          ? (o.delivery_carrier
              ? ({cdek: 'СДЭК', yandex: 'Яндекс Доставка', pochta: 'Почта России'}[o.delivery_carrier] || o.delivery_carrier)
                + ' ' + ({pvz: 'до ПВЗ', door: 'до двери', post: 'до отделения'}[o.delivery_mode] || '')
                + (o.delivery_price ? ' — ' + money(o.delivery_price) : '')
                + (o.delivery_postcode ? ', индекс ' + o.delivery_postcode : '')
              : 'доставка')
            + (o.delivery_address ? ': ' + o.delivery_address : '')
          : 'самовывоз' + (o.pickup_branch ? ': ' + o.pickup_branch : '')}
        ${o.payment_method ? ' · оплата ' + (o.payment_method === 'online' ? 'онлайн' : 'при получении') : ''}${o.paid_at ? ' · оплачен ' + when(o.paid_at) : ''}
        ${o.comment ? ' · ' + o.comment : ''}</div>

      ${o.shipments.length ? o.shipments.map((sh, n) => shipment(o, sh, n)).join('')
        : lines(o.items)}

      <div class="actions-row">
        <select class="f-status">
          ${Object.entries(STATUSES).map(([k, v]) =>
            `<option value="${k}" ${k === o.status ? 'selected' : ''}>${v}</option>`).join('')}
        </select>
        <button class="btn btn-accent save">Сохранить</button>
      </div>
    </div>`).join('');

  document.querySelectorAll('.ord').forEach(el => {
    el.querySelector('.save').onclick = () => save(el);
  });
  document.querySelectorAll('.ship-edit').forEach(el => {
    el.querySelectorAll('[data-to]').forEach(b => b.onclick = () => saveShip(el, b.dataset.to));
  });
}

const lines = items => `<ul class="lines">
  ${items.map(i => `<li>
    <span class="sku">${i.sku}</span>
    <span class="nm">${i.name}</span>
    <span class="where">${i.branch || ''}</span>
    <span class="pr">${i.qty > 1 ? `${i.qty} шт × ${money(i.price)} = ` : ''}${money(i.price * i.qty)}</span>
  </li>`).join('')}
</ul>`;

// Посылка: откуда, чем, за сколько, номер для отслеживания и статус.
// Своя посылка филиала подсвечена — её собирать здесь
function shipment(o, sh, n){
  const live = sh.status !== 'cancelled' && o.status !== 'cancelled';
  const days = sh.days_min ? (sh.days_min === sh.days_max ? `${sh.days_min} дн.`
                                                          : `${sh.days_min}–${sh.days_max} дн.`) : '';
  const next = {assembling: ['sent', 'Отправлена'], sent: ['delivered', 'Доставлена']}[sh.status];
  return `<div class="ship ${MY_BRANCH && sh.branch_id === MY_BRANCH ? 'my' : ''}">
    <div class="sh">
      <b>${o.shipments.length > 1 ? `Посылка ${n + 1} ` : 'Посылка '}${esc(sh.from_city)}</b>
      <span class="st ${sh.status}">${SHIP_ST[sh.status] || sh.status}</span>
      <span class="sm">${CARRIERS[sh.carrier] || ''}${sh.tariff ? ', тариф ' + esc(sh.tariff) : ''}${days ? ', ' + days : ''}
        ${sh.price ? ' · ' + money(sh.price) : ''}${sh.weight_g ? ' · ' + (sh.weight_g / 1000).toLocaleString('ru') + ' кг' : ''}</span>
    </div>
    ${lines(o.items.filter(i => i.shipment_id === sh.id))}
    ${live ? `<div class="ship-edit" data-id="${sh.id}">
      <input class="f-track" placeholder="Номер для отслеживания" maxlength="40"
             value="${esc(sh.track_number || '')}">
      <button class="btn" data-to="">Сохранить номер</button>
      ${next ? `<button class="btn btn-accent" data-to="${next[0]}">${next[1]}</button>` : ''}
      ${sh.status !== 'assembling' ? '<button class="btn" data-to="assembling">Вернуть в сборку</button>' : ''}
      ${sh.track_url ? `<a href="${esc(sh.track_url)}" target="_blank" rel="noopener">где посылка →</a>` : ''}
    </div>` : (sh.track_number ? `<div class="meta">номер ${esc(sh.track_number)}</div>` : '')}
  </div>`;
}

// Номер и статус посылки. Ушли все посылки — сервер сам переводит заказ
// в «отправлен», доставлены все — в «выдан»
async function saveShip(el, to){
  el.querySelectorAll('button').forEach(b => b.disabled = true);
  const body = {track_number: el.querySelector('.f-track').value.trim()};
  if (to) body.status = to;
  try {
    const r = await fetch(`/api/manage/shipments/${el.dataset.id}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (!r.ok){
      toast(typeof d.detail === 'string' ? d.detail : 'Не удалось сохранить', 'err');
      el.querySelectorAll('button').forEach(b => b.disabled = false);
      return;
    }
    toast('Сохранено', 'ok');
    load();
  } catch {
    toast('Нет связи с сервером', 'err');
    el.querySelectorAll('button').forEach(b => b.disabled = false);
  }
}

// Оплату отмечает менеджер: онлайн-оплаты нет, деньги приходят
// при получении. Отмена возвращает детали на витрину — это делает сервер
async function save(el){
  const btn = el.querySelector('.save');
  btn.disabled = true;
  try {
    const r = await fetch(`/api/manage/orders/${el.dataset.id}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status: el.querySelector('.f-status').value})});
    if (!r.ok){
      const d = await r.json().catch(() => ({}));
      toast(d.detail || 'Не удалось сохранить', 'err');
      btn.disabled = false; return;
    }
    toast('Сохранено', 'ok');
    load();
  } catch { toast('Нет связи с сервером', 'err'); btn.disabled = false; }
}

// Заявка — это телефон и вопрос. Всё, что с ней делают: позвонить
// и отметить обработанной
async function loadLeads(){
  const rows = await (await fetch('/api/manage/leads')).json();
  if (!rows.length){ $('list').innerHTML = '<p class="blank">Заявок нет</p>'; return; }

  $('list').innerHTML = rows.map(l => `
    <div class="ord ${l.processed ? 'done' : ''}" data-lead="${l.id}">
      <div class="h">
        <a class="num" href="tel:${l.phone.replace(/[^+\d]/g, '')}">${l.phone}</a>
        <span class="who">${l.name || 'без имени'}</span>
        <span class="st ${l.processed ? '' : 'new'}">
          ${l.processed ? 'обработана' : 'ждёт ответа'}</span>
        <span class="total">${when(l.created_at)}</span>
      </div>
      ${l.sku ? `<div class="meta">по детали
        <a href="/p/${l.sku}">${l.sku}</a> — ${l.part_name}
        ${l.part_status !== 'in_stock' ? ' (уже не в наличии)' : ''}</div>` : ''}
      ${l.donor_code ? `<div class="meta">по машине
        <a href="/cars/${l.donor_code}" target="_blank">${l.donor_code}</a> — ${l.donor_car}
        ${CAN_REMOVE[l.donor_status] || ''}</div>` : ''}
      ${l.message ? `<div class="meta">${l.message}</div>` : ''}
      <div class="actions-row">
        <button class="btn ${l.processed ? '' : 'btn-accent'} mark">
          ${l.processed ? 'Вернуть в работу' : 'Отметить обработанной'}</button>
      </div>
    </div>`).join('');

  document.querySelectorAll('[data-lead]').forEach(el => {
    el.querySelector('.mark').onclick = async () => {
      const done = !el.classList.contains('done');
      const r = await fetch(`/api/manage/leads/${el.dataset.lead}`, {
        method: 'PATCH', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({processed: done})});
      if (r.ok){ toast('Сохранено', 'ok'); loadLeads(); }
      else toast('Не удалось сохранить', 'err');
    };
  });
}

let tt;
function toast(m, k=''){ const el = $('toast'); el.textContent = m;
  el.className = `toast show ${k}`;
  clearTimeout(tt); tt = setTimeout(() => el.className = 'toast', 2400); }

// Статус и вкладка могут прийти из адреса: со сводки ведут прямые ссылки
if (startStatus){
  status = startStatus;
  [...$('tabs').children].forEach(x => x.classList.toggle('on', x.dataset.s === status));
}
[...$('mode').children].forEach(x => x.classList.toggle('on', x.dataset.m === mode));
$('tabs').hidden = mode === 'leads';
if ($('mine')) $('mine').onchange = load;

load();
