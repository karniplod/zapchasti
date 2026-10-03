// Скрипт шаблона templates/shop/account_profile.html — профиль.

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3200); }

async function send(method, url, body){
  try {
    const r = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (r.ok) return d;
    toast(typeof d.detail === 'string' ? d.detail : 'Не получилось сохранить');
  } catch { toast('Нет связи с сервером'); }
  return null;
}

// ── Личные данные ───────────────────────────────────────────
// Телефон и email — логины: хотя бы один должен остаться
phoneMask($('pPhone'));
const prules = [
  [$('pName'), v => Check.name(v)],
  [$('pPhone'), v => Check.phoneRule(v, {optional: !!$('pEmail').value.trim()})],
  [$('pEmail'), v => Check.emailRule(v, {optional: !!$('pPhone').value.trim()})],
];
live(prules);
$('profForm').onsubmit = async e => {
  e.preventDefault();
  if (!validate(prules)) return;
  $('profSave').disabled = true;
  const d = await send('PATCH', '/api/account/profile', {
    name: $('pName').value.trim(), phone: $('pPhone').value.trim() || null, email: $('pEmail').value.trim() || null});
  $('profSave').disabled = false;
  if (!d) return;
  if (d.verify_sent){ toast('Сохранено. На новый email ушло письмо — подтвердите его'); setTimeout(() => location.reload(), 2500); }
  else { toast('Сохранено'); setTimeout(() => location.reload(), 900); }
};
if ($('resend')) $('resend').onclick = async () => {
  $('resend').disabled = true;
  if (await send('POST', '/api/account/verify/resend', {login: $('pEmail').value.trim()}))
    toast('Письмо отправлено — проверьте почту');
};

// ── Пароль ──────────────────────────────────────────────────
const has = $('pwForm').dataset.has === '1';
const wrules = [
  ...(has ? [[$('pwCur'), v => v ? '' : 'Введите нынешний пароль']] : []),
  [$('pwNew'), v => Check.newPassword(v)],
  [$('pwNew2'), v => v === $('pwNew').value ? '' : 'Пароли не совпадают'],
];
live(wrules);
$('pwForm').onsubmit = async e => {
  e.preventDefault();
  if (!validate(wrules)) return;
  $('pwSave').disabled = true;
  const d = await send('POST', '/api/account/password', {current: has ? $('pwCur').value : null, new: $('pwNew').value});
  $('pwSave').disabled = false;
  if (d){ toast(has ? 'Пароль изменён' : 'Пароль задан'); $('pwForm').reset(); if (!has) setTimeout(() => location.reload(), 1200); }
};

// ── Уведомления ─────────────────────────────────────────────
// Сохраняются сразу, как переключили
[['nOrders', 'orders'], ['nPromo', 'promo']].forEach(([id, key]) => {
  $(id).onchange = async () => {
    const d = await send('PATCH', '/api/account/notifications', {[key]: $(id).checked});
    if (d) toast($(id).checked ? 'Уведомления включены' : 'Уведомления выключены');
    else $(id).checked = !$(id).checked;
  };
});
