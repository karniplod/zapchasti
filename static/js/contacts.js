// Скрипт шаблона templates/contacts.html.

const $ = id => document.getElementById(id);

let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3000); }

// Проверяем здесь же, чтобы не гонять заведомо неверное на сервер.
// Сервер проверяет ещё раз — форму можно обойти
const rules = [
  [$('lPhone'), v => Check.phoneRule(v)],
  [$('lName'), v => Check.name(v, {optional: true})],
  [$('lMsg'), v => Check.text(v, {max: 2000, what: 'Сообщение'})],
];
live(rules);
phoneMask($('lPhone'));

$('leadForm').onsubmit = async e => {
  e.preventDefault();
  if (!validate(rules)) return;
  const phone = $('lPhone').value.trim();

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
    if (!serverErrors(d, {phone: $('lPhone'), name: $('lName'), message: $('lMsg')}))
      toast(typeof d.detail === 'string' ? d.detail : 'Не получилось отправить');
  } catch {
    toast('Нет связи с сервером');
  }
  btn.disabled = false;
};
