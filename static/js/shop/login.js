// Скрипт шаблона templates/shop/login.html.

const $ = id => document.getElementById(id);
let mode = 'login';

const rules = {
  login: [$('login'), v => Check.loginRule(v)],
  name: [$('name'), v => Check.name(v, {optional: true})],
  // При входе пароль не учим жизни — какой есть, такой и проверим
  password: [$('password'), v => mode === 'register' ? Check.newPassword(v) : Check.password(v)],
  password2: [$('password2'), v => v === $('password').value ? '' : 'Пароли не совпадают'],
};
live(Object.values(rules));

$('tabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.mode;
  [...$('tabs').children].forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.only-register').forEach(x => x.hidden = mode !== 'register');
  $('go').textContent = mode === 'login' ? 'Войти' : 'Создать кабинет';
  $('password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('err').hidden = true;
  // Ошибки прошлого режима к новому не относятся
  Object.values(rules).forEach(([i]) => fieldError(i, ''));
};

// Пароль можно подсмотреть — на телефоне опечатка в скрытом поле
// стоит лишней попытки
$('pwEye').onclick = () => {
  const show = $('password').type === 'password';
  $('password').type = $('password2').type = show ? 'text' : 'password';
  $('pwEye').setAttribute('aria-pressed', String(show));
  $('pwEye').setAttribute('aria-label', show ? 'Скрыть пароль' : 'Показать пароль');
};

$('authForm').onsubmit = async e => {
  e.preventDefault();
  $('err').hidden = true;
  if (!validate(Object.values(rules))) return;

  $('go').disabled = true;
  try {
    const r = await fetch('/api/account/' + mode, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        login: $('login').value.trim(),
        password: $('password').value,
        name: mode === 'register' ? ($('name').value.trim() || null) : null,
      })});
    if (r.ok){ location.href = $('authForm').dataset.next; return; }
    const d = await r.json().catch(() => ({}));
    if (!serverErrors(d, {login: $('login'), password: $('password'), name: $('name')}))
      show(typeof d.detail === 'string' ? d.detail : 'Не получилось');
  } catch { show('Нет связи с сервером'); }
  $('go').disabled = false;
};

function show(m){ $('err').textContent = m; $('err').hidden = false; }
