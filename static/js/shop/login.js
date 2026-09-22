// Скрипт шаблона templates/shop/login.html.

const $ = id => document.getElementById(id);
let mode = 'login';

$('tabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  mode = b.dataset.mode;
  [...$('tabs').children].forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.only-register').forEach(x => x.hidden = mode !== 'register');
  $('go').textContent = mode === 'login' ? 'Войти' : 'Создать кабинет';
  $('password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('err').hidden = true;
};

$('authForm').onsubmit = async e => {
  e.preventDefault();
  const phone = $('phone').value.trim();
  const password = $('password').value;

  if (phone.replace(/\D/g, '').length < 10){
    show('Проверьте номер телефона'); return;
  }
  if (password.length < 6){ show('Пароль не короче шести символов'); return; }

  $('go').disabled = true;
  try {
    const r = await fetch('/api/account/' + mode, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone, password, name: $('name').value.trim() || null})});
    if (r.ok){ location.href = $('authForm').dataset.next; return; }
    const d = await r.json().catch(() => ({}));
    show(d.detail || 'Не получилось');
  } catch { show('Нет связи с сервером'); }
  $('go').disabled = false;
};

function show(m){ $('err').textContent = m; $('err').hidden = false; }
