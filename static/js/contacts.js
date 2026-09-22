// Скрипт шаблона templates/contacts.html.

const $ = id => document.getElementById(id);

let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3000); }

$('leadForm').onsubmit = async e => {
  e.preventDefault();
  const phone = $('lPhone').value.trim();
  // Проверяем здесь же, чтобы не гонять заведомо пустое на сервер.
  // Сервер проверяет ещё раз — форму можно обойти
  if (phone.replace(/\D/g, '').length < 10){
    $('lPhone').classList.add('bad');
    $('lPhone').focus();
    toast('Проверьте номер телефона');
    return;
  }
  $('lPhone').classList.remove('bad');

  const btn = $('lSend');
  btn.disabled = true;
  try {
    const r = await fetch('/api/leads', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        phone,
        name: $('lName').value.trim() || null,
        message: $('lMsg').value.trim() || null,
        sku: $('leadForm').dataset.sku || undefined,
      })});

    if (r.ok){
      // Форму убираем: повторная отправка того же — только лишний звонок
      $('leadForm').innerHTML =
        '<div class="notice notice-accent"><b>Заявка принята.</b> '
        + 'Перезвоним на указанный номер.</div>';
      return;
    }
    const d = await r.json().catch(() => ({}));
    toast(d.detail || 'Не получилось отправить');
  } catch {
    toast('Нет связи с сервером');
  }
  btn.disabled = false;
};
