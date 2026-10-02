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
      method: 'POST',
      // Куда вернуть после ссылки из письма — туда, откуда пришли
      headers: {'Content-Type': 'application/json', 'X-Next': $('authForm').dataset.next},
      body: JSON.stringify({
        login: $('login').value.trim(),
        password: $('password').value,
        name: mode === 'register' ? ($('name').value.trim() || null) : null,
      })});
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.confirm){ showSent(d.email, d.sent); return; }
    if (r.ok){ location.href = $('authForm').dataset.next; return; }
    // Пароль верный, но почта не подтверждена — предлагаем письмо
    if (d.code === 'email_unverified'){
      show(d.detail);
      $('resend').hidden = false;
      $('resend').dataset.email = d.email;
      $('go').disabled = false;
      return;
    }
    if (!serverErrors(d, {login: $('login'), password: $('password'), name: $('name')}))
      show(typeof d.detail === 'string' ? d.detail : 'Не получилось');
  } catch { show('Нет связи с сервером'); }
  $('go').disabled = false;
};

function show(m){ $('err').textContent = m; $('err').hidden = false; }

// ── Подтверждение email ─────────────────────────────────────────
function showSent(email, sent){
  $('authForm').hidden = true;
  $('tabs').hidden = true;
  $('sentTo').textContent = email;
  $('sentBox').hidden = false;
  $('resend2').dataset.email = email;
  // Письмо только что ушло — раньше минуты сервер второе не отправит
  cooldown($('resend2'), 60);
  if (sent === false){
    $('resendNote').textContent = 'Письмо не ушло — попробуйте через минуту.';
  }
}

// Повторное письмо — не чаще раза в минуту: так же считает сервер
async function resend(btn){
  btn.disabled = true;
  try {
    const r = await fetch('/api/account/verify/resend', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Next': $('authForm').dataset.next},
      body: JSON.stringify({login: btn.dataset.email})});
    const d = await r.json().catch(() => ({}));
    const note = btn.id === 'resend2' ? $('resendNote') : $('err');
    note.hidden = false;
    note.textContent = r.ok ? 'Письмо отправлено. Проверьте почту и папку «Спам».'
                            : (d.detail || 'Не получилось отправить');
    cooldown(btn, (d.cooldown || 60));
  } catch { btn.disabled = false; }
}

function cooldown(btn, sec){
  const label = btn.dataset.label || (btn.dataset.label = btn.textContent);
  btn.disabled = true;
  const tick = () => {
    if (sec <= 0){ btn.disabled = false; btn.textContent = label; return; }
    btn.textContent = `${label} (через ${sec--} с)`;
    setTimeout(tick, 1000);
  };
  tick();
}

$('resend').onclick = () => resend($('resend'));
$('resend2').onclick = () => resend($('resend2'));

// ── Вход через Telegram ─────────────────────────────────────────
// Окно подтверждения — официальное, Telegram.Login.auth из скрипта
// виджета. Он возвращает данные с подписью; сервер проверяет её
// токеном бота в /auth/telegram/callback
const tg = $('tgLogin');
if (tg) tg.onclick = () => {
  if (!window.Telegram || !Telegram.Login){
    show('Telegram не загрузился — проверьте связь и обновите страницу');
    return;
  }
  Telegram.Login.auth({bot_id: tg.dataset.botId, request_access: 'write', lang: 'ru'}, data => {
    if (!data) return;          // окно закрыли, ничего не подтвердив
    location.href = '/auth/telegram/callback?'
      + new URLSearchParams({...data, next: tg.dataset.next});
  });
};
