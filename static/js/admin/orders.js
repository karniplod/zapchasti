// Скрипт шаблона templates/admin/orders.html.
// Список заказов — таблицей, как в CRM: кто, куда, что, сколько, статус.
// Работа с заказом — в его карточке /orders/<номер> (static/js/admin/order.js).

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
const CARRIERS = {cdek: 'СДЭК', yandex: 'Яндекс', pochta: 'Почта'};
const MODES = {pvz: 'до пункта', door: 'до двери', post: 'до отделения'};

const money = v => Math.round(+v || 0).toLocaleString('ru') + ' ₽';
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const when = s => new Date(s).toLocaleDateString('ru');
const time = s => new Date(s).toLocaleTimeString('ru', {hour: '2-digit', minute: '2-digit'});
// +79125554433 → +7 912 555-44-33
const phone = p => {
  const d = String(p || '').replace(/\D/g, '');
  return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : (p || '');
};

$('mode').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.m;
  [...$('mode').children].forEach(x => x.classList.toggle('on', x === b));
  // Поиск и статусы относятся к заказам, у заявок их нет
  $('ordersBox').hidden = mode === 'leads';
  load();
};

$('tabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  status = b.dataset.s;
  [...$('tabs').children].forEach(x => x.classList.toggle('on', x === b));
  load();
};

let qt;
$('q').addEventListener('input', () => { clearTimeout(qt); qt = setTimeout(load, 300); });

async function counts(){
  const q = $('mine') && $('mine').checked ? '?mine=1' : '';
  const r = await fetch('/api/manage/orders/counts' + q);
  if (!r.ok) return;
  const n = await r.json();
  [...$('tabs').children].forEach(b => { b.querySelector('i').textContent = n[b.dataset.s] || ''; });
}

let seq = 0;
async function load(){
  if (mode === 'leads') return loadLeads();
  const my = ++seq;
  const q = new URLSearchParams();
  if (status) q.set('status', status);
  if ($('mine') && $('mine').checked) q.set('mine', '1');
  if ($('q').value.trim()) q.set('q', $('q').value.trim());
  const res = await fetch('/api/manage/orders?' + q);
  const rows = await res.json();
  if (my !== seq) return;
  if (!res.ok){ $('list').innerHTML = `<p class="blank">${esc(rows.detail || 'Не получилось загрузить')}</p>`; return; }
  if (!rows.length){
    $('list').innerHTML = `<p class="blank">${$('q').value.trim() ? 'Ничего не нашлось' : 'Заказов нет'}</p>`;
    return;
  }

  // Строка — ссылка на карточку. Главное крупно: кто, телефон, куда
  $('list').innerHTML = `<div class="o-table">
    <div class="o-row o-hd"><span>Заказ</span><span>Покупатель</span><span>Получение</span>
      <span class="r">Сумма</span><span>Статус</span></div>
    ${rows.map(o => {
      const ship = o.delivery_method === 'shipping';
      const n = o.shipments.length;
      const qty = o.items.reduce((s, i) => s + i.qty, 0);
      const paid = o.paid_at || ['paid', 'shipped', 'completed'].includes(o.status);
      return `<a class="o-row" href="/orders/${encodeURIComponent(o.number)}">
        <span class="o-num"><b>№ ${esc(o.number)}</b>
          <small>${when(o.created_at)}, ${time(o.created_at)}</small></span>
        <span class="o-who"><b>${esc(o.customer_name || 'без имени')}</b>
          <small class="o-phone">${esc(phone(o.phone))}</small></span>
        <span class="o-where">${ship
          ? `<b>${esc(o.delivery_address || o.delivery_city || 'адрес не указан')}</b>
             <small>${o.delivery_carrier ? CARRIERS[o.delivery_carrier] + ' ' + (MODES[o.delivery_mode] || '') : 'доставка ТК'}${n > 1 ? ` · ${n} посылки` : ''}</small>`
          : `<b>Самовывоз</b><small>${esc(o.pickup_branch || '')}</small>`}</span>
        <span class="o-sum r"><b>${money(o.total)}</b>
          <small class="${paid ? 'ok' : ''}">${paid ? 'оплачен' : o.payment_method === 'online' ? 'ждём оплату' : 'при получении'} · ${qty} шт</small></span>
        <span class="o-st"><span class="st ${o.status}">${STATUSES[o.status] || o.status}</span>
          ${o.manager_note ? `<small class="o-note" title="${esc(o.manager_note)}">✎ ${esc(o.manager_note)}</small>` : ''}</span>
      </a>`;
    }).join('')}
  </div>`;
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
$('ordersBox').hidden = mode === 'leads';
if ($('mine')) $('mine').onchange = () => { counts(); load(); };

counts();
load();
