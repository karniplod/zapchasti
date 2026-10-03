// Скрипт шаблона templates/admin/users.html — покупатели и сотрудники.
// Сервер — app/routers/users_admin.py. Карточка покупателя — отдельная
// страница /users/customers/<id> (static/js/admin/customer.js).

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const money = v => Math.round(+v || 0).toLocaleString('ru') + ' ₽';
const date = s => s ? new Date(s).toLocaleDateString('ru') : '—';
// +79125554433 → +7 912 555-44-33
const phone = p => {
  const d = String(p || '').replace(/\D/g, '');
  return d.length === 11 ? `+7 ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}` : (p || '');
};
// «3 дня назад» — когда последний раз заходил
const ago = s => {
  if (!s) return 'не входил';
  const days = Math.floor((Date.now() - new Date(s)) / 864e5);
  return days < 1 ? 'сегодня' : days === 1 ? 'вчера' : days < 30 ? `${days} дн. назад` : date(s);
};
const PROVIDERS = {google: 'Google', vk: 'VK', yandex: 'Яндекс', telegram: 'Telegram', max: 'MAX'};

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

// ── Вкладки ─────────────────────────────────────────────────────
let mode = location.hash === '#staff' ? 'staff' : 'customers';
function setMode(m){
  mode = m;
  [...$('mode').children].forEach(b => b.classList.toggle('on', b.dataset.m === m));
  $('custBox').hidden = m !== 'customers';
  $('staffBox').hidden = m !== 'staff';
  history.replaceState(null, '', m === 'staff' ? '#staff' : location.pathname);
  if (m === 'staff') loadStaff(); else loadCustomers();
}
$('mode').onclick = e => { const b = e.target.closest('button'); if (b) setMode(b.dataset.m); };

// ── Покупатели ──────────────────────────────────────────────────
let cseq = 0, cpage = 1, qt;
async function loadCustomers(more){
  const my = ++cseq;
  cpage = more ? cpage + 1 : 1;
  const q = new URLSearchParams({q: $('q').value.trim(), show: $('show').value,
                                 sort: $('sort').value, page: cpage});
  const r = await fetch('/api/manage/customers?' + q);
  const d = await r.json();
  if (my !== cseq) return;
  if (!r.ok){ $('custList').innerHTML = `<p class="blank">${esc(d.detail || 'Не получилось загрузить')}</p>`; return; }
  $('nCust').textContent = d.counts.all;
  $('custTotals').innerHTML = `Найдено <b>${d.total}</b> · всего покупателей <b>${d.counts.all}</b>`
    + ` · новых за 30 дней <b>${d.counts.month}</b>`
    + (d.counts.blocked ? ` · заблокировано <b>${d.counts.blocked}</b>` : '');
  $('custMore').hidden = d.page >= d.pages;
  if (!d.items.length && !more){
    $('custList').innerHTML = `<p class="blank">${$('q').value.trim() || $('show').value ? 'Никого не нашлось' : 'Покупателей пока нет'}</p>`;
    return;
  }
  const html = d.items.map(c => `<a class="u-row ${c.is_blocked ? 'is-blocked' : ''}" href="/users/customers/${c.id}">
      <span class="u-who"><b>${esc(c.name || 'без имени')}</b>
        <small>${esc([phone(c.phone), c.email].filter(Boolean).join(' · ') || 'контактов нет')}</small></span>
      <span class="u-tags">${c.is_blocked ? '<span class="tag bad">заблокирован</span>' : ''}
        ${c.providers ? c.providers.split(',').map(p => `<span class="tag">${esc(PROVIDERS[p] || p)}</span>`).join('') : ''}
        ${+c.personal_discount ? `<span class="tag ok">скидка ${Math.round(+c.personal_discount * 10) / 10}%</span>` : ''}</span>
      <span class="u-num r"><b>${c.orders}</b><small>заказов</small></span>
      <span class="u-num r"><b>${money(c.spent)}</b><small>оплачено</small></span>
      <span class="u-num r"><b>${(+c.bonus).toLocaleString('ru')}</b><small>баллов</small></span>
      <span class="u-when r"><b>${ago(c.last_login_at)}</b><small>с ${date(c.created_at)}</small></span>
    </a>`).join('');
  if (more) $('custList').querySelector('.u-table').insertAdjacentHTML('beforeend', html);
  else $('custList').innerHTML = `<div class="u-table">
      <div class="u-row u-hd"><span>Покупатель</span><span></span><span class="r">Заказы</span>
        <span class="r">Покупки</span><span class="r">Баллы</span><span class="r">Вход</span></div>${html}</div>`;
}
$('q').addEventListener('input', () => { clearTimeout(qt); qt = setTimeout(() => loadCustomers(), 300); });
$('show').onchange = $('sort').onchange = () => loadCustomers();
$('custMoreBtn').onclick = () => loadCustomers(true);

// ── Сотрудники ──────────────────────────────────────────────────
let S = null, editing = null;
const ROLE_CLASS = {admin: 'bad', manager: 'ok', dismantler: ''};

async function loadStaff(){
  const r = await fetch('/api/manage/staff');
  if (!r.ok){ $('staffList').innerHTML = '<p class="blank">Не получилось загрузить</p>'; return; }
  S = await r.json();
  $('sfRole').innerHTML = Object.entries(S.roles).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
  $('sfBranch').innerHTML = '<option value="">Все филиалы</option>'
    + S.branches.map(b => `<option value="${b.id}">${esc(b.title)}</option>`).join('');
  $('staffList').innerHTML = S.items.map(u => `<div class="panel s-card ${u.is_active ? '' : 'is-off'}" data-id="${u.id}">
      <div class="s-top">
        <span class="s-ava">${esc((u.full_name || u.login)[0].toUpperCase())}</span>
        <span class="s-who"><b>${esc(u.full_name || u.login)}</b>
          <small><span class="mono">${esc(u.login)}</span>${u.id === S.me ? ' · это вы' : ''}</small></span>
        <span class="tag ${ROLE_CLASS[u.role]}">${esc(S.roles[u.role] || u.role)}</span>
        ${u.is_active ? '' : '<span class="tag bad">отключён</span>'}
      </div>
      <dl class="s-facts">
        <dt>Филиал</dt><dd>${esc(u.branch || 'все')}</dd>
        <dt>Контакты</dt><dd>${esc([u.email, phone(u.phone)].filter(Boolean).join(' · ') || '—')}</dd>
        <dt>Письма о заказах</dt><dd>${u.notify_orders ? 'да' : 'нет'}</dd>
        <dt>Последний вход</dt><dd>${u.last_login_at ? new Date(u.last_login_at).toLocaleString('ru', {dateStyle: 'short', timeStyle: 'short'}) : 'не входил'}</dd>
        <dt>Ведёт заказов</dt><dd>${u.open_orders ? `<a href="/orders">${u.open_orders}</a>` : '0'}</dd>
      </dl>
      <div class="s-acts">
        <button type="button" class="lnk s-edit">Изменить</button>
        ${S.is_admin ? `<button type="button" class="lnk s-pass">Новый пароль</button>
          <button type="button" class="lnk s-out">Завершить сеансы</button>
          ${u.id !== S.me ? `<button type="button" class="lnk s-toggle ${u.is_active ? 'danger' : ''}">${u.is_active ? 'Отключить' : 'Включить'}</button>` : ''}
          <button type="button" class="lnk s-hist">История</button>` : ''}
      </div>
      <div class="s-histbox" hidden></div>
    </div>`).join('');
}

// Форма сотрудника: новый — со всеми полями; правка — без логина; не
// админ правит только себя и без роли, филиала и пароля
const rules = [
  [$('sfLogin'), v => editing ? '' : /^[a-z0-9._-]{3,32}$/i.test(v.trim()) ? '' : 'Логин: латиница, цифры, точка, дефис — от 3 до 32'],
  [$('sfName'), v => Check.name(v, {optional: true})],
  [$('sfEmail'), v => Check.emailRule(v, {optional: !$('sfNotify').checked})],
  [$('sfPhone'), v => Check.phoneRule(v, {optional: true})],
  [$('sfPass'), v => !editing || v ? (v.length < 8 ? 'Не короче 8 символов' : Check.newPassword(v)) : ''],
];
live(rules);
phoneMask($('sfPhone'));

function openForm(u){
  editing = u ? u.id : null;
  $('sfTitle').textContent = u ? `Изменить: ${u.full_name || u.login}` : 'Новый сотрудник';
  $('sfLogin').value = u ? u.login : ''; $('sfLogin').disabled = !!u;
  $('sfName').value = u ? u.full_name || '' : '';
  $('sfRole').value = u ? u.role : 'manager';
  $('sfBranch').value = u && u.branch_id ? u.branch_id : '';
  $('sfEmail').value = u ? u.email || '' : '';
  $('sfPhone').value = u ? phone(u.phone) : '';
  $('sfNotify').checked = u ? u.notify_orders : false;
  $('sfPass').value = '';
  // Пароль у правки — только «Новый пароль», отдельной кнопкой
  $('sfPass').closest('div').hidden = !!u;
  document.querySelectorAll('#staffForm .adm-only').forEach(el => { if (el !== $('sfPass').closest('div')) el.hidden = !S.is_admin; });
  rules.forEach(([i]) => fieldError(i, ''));
  $('staffForm').hidden = false;
  $('staffForm').scrollIntoView({behavior: 'smooth', block: 'start'});
  (u ? $('sfName') : $('sfLogin')).focus({preventScroll: true});
}
if ($('staffNew')) $('staffNew').onclick = () => openForm(null);
$('sfCancel').onclick = () => { $('staffForm').hidden = true; };
// Пароль, который можно продиктовать: без похожих букв и цифр
const genPass = () => {
  const a = 'abcdefghjkmnpqrstuvwxyz', d = '23456789';
  let s = '';
  for (let i = 0; i < 6; i++) s += a[crypto.getRandomValues(new Uint32Array(1))[0] % a.length];
  for (let i = 0; i < 4; i++) s += d[crypto.getRandomValues(new Uint32Array(1))[0] % d.length];
  return s[0].toUpperCase() + s.slice(1);
};
$('sfGen').onclick = () => { $('sfPass').value = genPass(); fieldError($('sfPass'), ''); };

$('staffForm').onsubmit = async e => {
  e.preventDefault();
  if (!validate(rules)) return;
  const body = {full_name: $('sfName').value.trim() || null, email: $('sfEmail').value.trim() || null,
                phone: $('sfPhone').value.trim() || null, notify_orders: $('sfNotify').checked};
  if (S.is_admin){ body.role = $('sfRole').value; body.branch_id = $('sfBranch').value ? +$('sfBranch').value : null; }
  if (!editing){ body.login = $('sfLogin').value.trim().toLowerCase(); body.password = $('sfPass').value; }
  $('sfSave').disabled = true;
  const ok = await api(editing ? 'PATCH' : 'POST', '/api/manage/staff' + (editing ? '/' + editing : ''), body);
  $('sfSave').disabled = false;
  if (!ok) return;
  if (!editing) await askConfirm(`Логин: ${body.login}\nПароль: ${body.password}\n\nПередайте их сотруднику — пароль больше нигде не покажется.`,
                                 {title: 'Сотрудник заведён', ok: 'Понятно', cancel: null});
  toast('Сохранено', 'ok');
  $('staffForm').hidden = true;
  loadStaff();
};

$('staffList').addEventListener('click', async e => {
  const card = e.target.closest('.s-card');
  if (!card) return;
  const u = S.items.find(x => x.id === +card.dataset.id);
  const name = u.full_name || u.login;
  if (e.target.closest('.s-edit')) openForm(u);
  else if (e.target.closest('.s-pass')){
    const pw = genPass();
    if (!await askConfirm(`Новый пароль для ${name}: ${pw}\n\nСтарый перестанет подходить, все входы сотрудника — в браузере и в приложении — завершатся.`,
                          {title: 'Сменить пароль?', ok: 'Сменить'})) return;
    if (await api('PATCH', `/api/manage/staff/${u.id}`, {password: pw})){
      await askConfirm(`Логин: ${u.login}\nПароль: ${pw}`, {title: 'Пароль сменён — передайте сотруднику', ok: 'Понятно', cancel: null});
      loadStaff();
    }
  } else if (e.target.closest('.s-out')){
    if (!await askConfirm(`${name} выйдет из бэкенда на всех устройствах и войдёт заново со своим паролем.`,
                          {title: 'Завершить сеансы?', ok: 'Завершить'})) return;
    if (await api('POST', `/api/manage/staff/${u.id}/logout`)) toast('Сеансы завершены', 'ok');
  } else if (e.target.closest('.s-toggle')){
    const off = u.is_active;
    if (off && !await askConfirm(`${name} не сможет войти, текущие входы завершатся. Заказы, которые он ведёт, останутся за ним — передайте их в карточках.`,
                                 {title: 'Отключить сотрудника?', ok: 'Отключить', danger: true})) return;
    if (await api('PATCH', `/api/manage/staff/${u.id}`, {is_active: !off})){ toast(off ? 'Отключён' : 'Включён', 'ok'); loadStaff(); }
  } else if (e.target.closest('.s-hist')){
    const box = card.querySelector('.s-histbox');
    if (!box.hidden){ box.hidden = true; return; }
    const rows = await (await fetch(`/api/manage/staff/${u.id}/audit`)).json();
    box.innerHTML = rows.length ? rows.map(a => `<p><span class="when">${new Date(a.at).toLocaleString('ru', {dateStyle: 'short', timeStyle: 'short'})}</span>
        <b>${esc(a.action)}</b>${a.details ? ' — ' + esc(a.details) : ''} <span class="who">${esc(a.who || '')}</span></p>`).join('')
      : '<p class="muted">Изменений не было</p>';
    box.hidden = false;
  }
});

setMode(mode);
// Открыли сразу сотрудников — число покупателей на вкладке всё равно нужно
if (mode === 'staff') fetch('/api/manage/customers?page=1').then(r => r.json()).then(d => { $('nCust').textContent = d.counts.all; });
