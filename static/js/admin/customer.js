// Скрипт шаблона templates/admin/customer.html — карточка покупателя.
// Сервер — app/routers/users_admin.py; баллы и персональная скидка —
// app/routers/promo_admin.py (те же, что в карточке заказа).

const $ = id => document.getElementById(id);
const ID = +$('card').dataset.id;
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const money = v => Math.round(+v || 0).toLocaleString('ru') + ' ₽';
const date = s => s ? new Date(s).toLocaleDateString('ru') : '—';
const dt = s => s ? new Date(s).toLocaleString('ru', {dateStyle: 'short', timeStyle: 'short'}) : '—';
const phone = p => {
  const d = String(p || '').replace(/\D/g, '');
  return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : (p || '');
};

let tt;
function toast(m, k = ''){ const el = $('toast'); el.textContent = m;
  el.className = `toast show ${k}`;
  clearTimeout(tt); tt = setTimeout(() => el.className = 'toast', 2600); }

async function api(method, url, body){
  try {
    const r = await fetch(url, {method, headers: body ? {'Content-Type': 'application/json'} : {},
                                body: body ? JSON.stringify(body) : undefined});
    const d = await r.json().catch(() => ({}));
    if (r.ok) return d;
    toast(typeof d.detail === 'string' ? d.detail : 'Не получилось сохранить', 'err');
  } catch { toast('Нет связи с сервером', 'err'); }
  return null;
}

let D = null, editing = false;
async function load(){
  const r = await fetch(`/api/manage/customers/${ID}`);
  if (!r.ok){ $('card').innerHTML = '<p class="blank">Покупатель не найден</p>'; return; }
  D = await r.json();
  document.title = (D.customer.name || 'Покупатель') + ' — покупатели';
  render();
}

function render(){
  const c = D.customer, s = D.stats;
  $('card').innerHTML = `
    <div class="c-head">
      <div><h1>${esc(c.name || 'Без имени')}</h1>
        <p class="sub">Покупатель с ${date(c.created_at)} · последний вход ${c.last_login_at ? dt(c.last_login_at) : '— не входил'}</p></div>
      ${c.is_blocked ? `<div class="blocked-bar">Заблокирован${c.blocked_reason ? ': ' + esc(c.blocked_reason) : ''}</div>` : ''}
    </div>
    <div class="c-stats">
      <div><b>${s.orders}</b><small>заказов</small></div>
      <div><b>${s.completed}</b><small>выдано</small></div>
      <div><b>${s.cancelled}</b><small>отменено</small></div>
      <div><b>${money(s.spent)}</b><small>оплачено</small></div>
      <div><b>${(+D.bonus).toLocaleString('ru')}</b><small>баллов</small></div>
      <div><b>${+c.personal_discount ? Math.round(+c.personal_discount * 10) / 10 + '%' : '—'}</b><small>скидка</small></div>
    </div>
    <div class="c-grid">
      <div class="c-main">
        ${contacts(c)}
        ${orders()}
        ${loyaltyBlock()}
      </div>
      <aside class="c-side">
        ${access(c)}
        ${logins()}
        ${addresses()}
        ${history()}
      </aside>
    </div>`;
  if (editing) setupEdit();
}

function contacts(c){
  if (editing) return `<section class="panel"><div class="blk-h"><h2>Контакты</h2></div>
      <form id="cForm" novalidate>
        <div class="s-grid">
          <div><label for="cName">Имя</label><input id="cName" maxlength="80" value="${esc(c.name || '')}"></div>
          <div><label for="cPhone">Телефон</label><input id="cPhone" type="tel" maxlength="32" value="${esc(phone(c.phone))}"></div>
          <div><label for="cEmail">Email</label><input id="cEmail" type="email" maxlength="254" value="${esc(c.email || '')}"></div>
        </div>
        <p class="u-hint">Телефон и email — логин для входа в кабинет. Новый email придётся подтвердить заново.</p>
        <div class="actions-row"><button type="submit" class="btn btn-accent">Сохранить</button>
          <button type="button" class="btn" id="cCancel">Отмена</button></div>
      </form></section>`;
  return `<section class="panel"><div class="blk-h"><h2>Контакты</h2>
      <button type="button" class="lnk" id="cEdit">Изменить</button></div>
    <dl class="s-facts wide">
      <dt>Телефон</dt><dd>${c.phone ? `<a href="tel:${esc(c.phone)}">${esc(phone(c.phone))}</a>` : '—'}</dd>
      <dt>Email</dt><dd>${c.email ? `<a href="mailto:${esc(c.email)}">${esc(c.email)}</a> ${c.email_verified ? '<span class="tag ok">подтверждён</span>' : '<span class="tag">не подтверждён</span>'}` : '—'}</dd>
      <dt>Вход</dt><dd>${[c.has_password ? 'пароль' : '', ...D.identities.map(i => i.provider + (i.display ? ` (${esc(i.display)})` : ''))].filter(Boolean).join(', ') || '—'}</dd>
      <dt>Письма</dt><dd>${c.notify_orders ? 'о заказах' : 'о заказах — выключены'}${c.notify_promo ? ', о баллах и акциях' : ''}</dd>
    </dl>
    <label for="cNote" class="mt-12">Заметка для сотрудников <small class="muted">— покупатель её не видит</small></label>
    <textarea id="cNote" maxlength="2000" placeholder="Например: оптовик, звонить после 18:00">${esc(c.staff_note || '')}</textarea>
    <div class="actions-row"><button type="button" class="btn" id="cNoteSave">Сохранить заметку</button></div>
  </section>`;
}

function orders(){
  return `<section class="panel"><div class="blk-h"><h2>Заказы</h2>
      ${D.orders.length ? `<a class="lnk" href="/orders?q=${encodeURIComponent(D.customer.phone || D.customer.email || '')}">Открыть в заказах</a>` : ''}</div>
    ${D.orders.length ? `<div class="c-orders">${D.orders.map(o => `<a class="c-ord" href="/orders/${encodeURIComponent(o.number)}">
        <span class="mono">№ ${esc(o.number)}</span><span>${date(o.created_at)}</span>
        <span>${o.items} поз. · ${o.delivery_method === 'shipping' ? 'доставка' + (o.delivery_city ? ', ' + esc(o.delivery_city) : '') : 'самовывоз'}</span>
        <span class="r"><b>${money(o.total)}</b></span><span class="st ${o.status}">${esc(o.label)}</span></a>`).join('')}</div>`
      : '<p class="muted">Заказов пока нет</p>'}
  </section>`;
}

// Баллы и персональная скидка — те же ручки, что в карточке заказа
function loyaltyBlock(){
  return `<section class="panel"><div class="blk-h"><h2>Баллы и скидка</h2></div>
    <div class="s-grid">
      <div><label for="lDisc">Персональная скидка, %</label>
        <input id="lDisc" type="number" min="0" max="50" step="0.5" value="${+D.customer.personal_discount || 0}"></div>
      <div><label for="lBonus">Начислить (+) или списать (−) баллы</label>
        <input id="lBonus" type="number" step="1" placeholder="например, 500 или −200"></div>
      <div><label for="lWhy">За что</label><input id="lWhy" maxlength="200" placeholder="Компенсация за задержку"></div>
    </div>
    <div class="actions-row"><button type="button" class="btn btn-accent" id="lSave">Сохранить</button></div>
    <div class="c-ledger" id="ledger"></div>
  </section>`;
}

function access(c){
  return `<section class="panel"><div class="blk-h"><h2>Доступ</h2></div>
    <p class="u-hint">${c.is_blocked ? 'Покупатель не может войти в кабинет и оформить заказ.'
      : 'Блокировка закрывает вход и оформление. Заказы остаются как есть.'}</p>
    <div class="c-acts">
      ${c.is_blocked ? '<button type="button" class="btn" id="aUnblock">Разблокировать</button>'
        : `<input id="aReason" maxlength="300" placeholder="Причина блокировки">
           <button type="button" class="btn btn-danger" id="aBlock">Заблокировать</button>`}
      <button type="button" class="btn" id="aLogout">Выйти на всех устройствах</button>
    </div>
  </section>`;
}

function logins(){
  return `<section class="panel"><div class="blk-h"><h2>Входы</h2></div>
    ${D.logins.length ? `<ul class="c-list">${D.logins.map(l => `<li><b>${esc(l.device)}</b>
        <small>${dt(l.at)} · ${esc(l.method)}</small></li>`).join('')}</ul>`
      : '<p class="muted">Журнал входов пуст — покупатель не входил после его запуска</p>'}
  </section>`;
}

function addresses(){
  return `<section class="panel"><div class="blk-h"><h2>Адреса</h2></div>
    ${D.addresses.length ? `<ul class="c-list">${D.addresses.map(a => `<li><b>${esc(a.title || a.city)}${a.is_default ? ' · основной' : ''}</b>
        <small>${esc([a.city, a.street, a.house, a.flat && 'кв. ' + a.flat].filter(Boolean).join(', '))}</small></li>`).join('')}</ul>`
      : '<p class="muted">Сохранённых адресов нет</p>'}
  </section>`;
}

function history(){
  return `<section class="panel"><div class="blk-h"><h2>История изменений</h2></div>
    ${D.audit.length ? `<ul class="c-list">${D.audit.map(a => `<li><b>${esc(a.action)}</b>
        <small>${dt(a.at)} · ${esc(a.who || '')}${a.details ? ' · ' + esc(a.details) : ''}</small></li>`).join('')}</ul>`
      : '<p class="muted">Сотрудники карточку не меняли</p>'}
  </section>`;
}

async function ledger(){
  const r = await fetch(`/api/manage/customers/${ID}/loyalty`);
  if (!r.ok || !$('ledger')) return;
  const d = await r.json();
  $('ledger').innerHTML = d.history.length ? `<ul class="c-list">${d.history.map(h => `<li>
      <b class="${h.amount > 0 ? 'plus' : 'minus'}">${h.amount > 0 ? '+' : ''}${h.amount}</b>
      <small>${dt(h.created_at)} · ${esc(h.comment || h.kind)}${h.who ? ' · ' + esc(h.who) : ''}</small></li>`).join('')}</ul>` : '';
}

const crules = () => [
  [$('cName'), v => Check.name(v, {optional: true})],
  [$('cPhone'), v => Check.phoneRule(v, {optional: true})],
  [$('cEmail'), v => Check.emailRule(v, {optional: true})],
];
function setupEdit(){
  live(crules());
  phoneMask($('cPhone'));
  $('cName').focus();
}

$('card').addEventListener('submit', async e => {
  if (e.target.id !== 'cForm') return;
  e.preventDefault();
  if (!validate(crules())) return;
  if (!$('cPhone').value.trim() && !$('cEmail').value.trim()){
    fieldError($('cPhone'), 'Нужен телефон или email — иначе покупателю не войти'); return;
  }
  if (await api('PATCH', `/api/manage/customers/${ID}`, {name: $('cName').value.trim() || null,
      phone: $('cPhone').value.trim() || null, email: $('cEmail').value.trim() || null})){
    editing = false; toast('Сохранено', 'ok'); await load(); ledger();
  }
});

$('card').addEventListener('click', async e => {
  const t = e.target;
  if (t.id === 'cEdit'){ editing = true; render(); ledger(); }
  else if (t.id === 'cCancel'){ editing = false; render(); ledger(); }
  else if (t.id === 'cNoteSave'){
    if (await api('PATCH', `/api/manage/customers/${ID}`, {staff_note: $('cNote').value})){ toast('Заметка сохранена', 'ok'); await load(); ledger(); }
  } else if (t.id === 'lSave'){
    const body = {};
    const disc = $('lDisc').value === '' ? 0 : +$('lDisc').value;
    if (disc !== +D.customer.personal_discount){
      if (!(disc >= 0 && disc <= 50)){ fieldError($('lDisc'), 'От 0 до 50%'); return; }
      body.personal_discount = disc;
    }
    if ($('lBonus').value.trim()){
      const n = Math.trunc(+$('lBonus').value.replace('−', '-'));
      if (!n){ fieldError($('lBonus'), 'Целое число, например 500 или -200'); return; }
      if ($('lWhy').value.trim().length < 3){ fieldError($('lWhy'), 'Напишите, за что'); return; }
      body.bonus_add = n; body.comment = $('lWhy').value.trim();
    }
    if (!Object.keys(body).length){ toast('Нечего сохранять'); return; }
    if (await api('PATCH', `/api/manage/customers/${ID}/loyalty`, body)){ toast('Сохранено', 'ok'); await load(); ledger(); }
  } else if (t.id === 'aBlock'){
    const reason = $('aReason').value.trim();
    if (reason.length < 3){ fieldError($('aReason'), 'Напишите причину — её увидят другие сотрудники'); $('aReason').focus(); return; }
    if (!await askConfirm(`${D.customer.name || 'Покупатель'} не сможет войти и оформить заказ, текущие входы завершатся.`,
                          {title: 'Заблокировать покупателя?', ok: 'Заблокировать', danger: true})) return;
    if (await api('POST', `/api/manage/customers/${ID}/block`, {blocked: true, reason})){ toast('Заблокирован', 'ok'); await load(); ledger(); }
  } else if (t.id === 'aUnblock'){
    if (await api('POST', `/api/manage/customers/${ID}/block`, {blocked: false})){ toast('Разблокирован', 'ok'); await load(); ledger(); }
  } else if (t.id === 'aLogout'){
    if (!await askConfirm('Покупатель выйдет из кабинета на всех устройствах и войдёт заново.',
                          {title: 'Завершить все входы?', ok: 'Завершить'})) return;
    if (await api('POST', `/api/manage/customers/${ID}/logout`)){ toast('Все входы завершены', 'ok'); await load(); ledger(); }
  }
});

load().then(ledger);
