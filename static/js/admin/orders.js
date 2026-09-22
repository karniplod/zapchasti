// Скрипт шаблона templates/admin/orders.html.

const $ = id => document.getElementById(id);
let status = '';
// Со сводки приходят сразу на нужную вкладку: /orders?tab=leads
let mode = new URLSearchParams(location.search).get('tab') === 'leads'
           ? 'leads' : 'orders';
const startStatus = new URLSearchParams(location.search).get('s') || '';

const STATUSES = {new:'новый', confirmed:'подтверждён', paid:'оплачен',
                  shipped:'отправлен', completed:'выдан', cancelled:'отменён'};

const money = v => v ? Number(v).toLocaleString('ru') + ' ₽' : '—';
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
  const rows = await (await fetch('/api/manage/orders' + (status ? `?status=${status}` : ''))).json();
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
          ? 'доставка' + (o.delivery_address ? ': ' + o.delivery_address : '')
          : 'самовывоз'}${o.paid_at ? ' · оплачен ' + when(o.paid_at) : ''}
        ${o.comment ? ' · ' + o.comment : ''}</div>

      <ul class="lines">
        ${o.items.map(i => `<li>
          <span class="sku">${i.sku}</span>
          <span class="nm">${i.name}</span>
          <span class="where">${i.branch || ''}</span>
          <span class="pr">${money(i.price)}</span>
        </li>`).join('')}
      </ul>

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
        ${l.donor_status === 'dismantling' ? '(в разборе — можно снять)' : ''}</div>` : ''}
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

load();
