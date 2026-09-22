// Скрипт шаблона templates/car.html: вопрос «что можно снять под заказ».
// Уходит в те же заявки, что и форма на «Контактах», с кодом машины —
// менеджер в бэкенде видит, о какой машине речь.

const $ = id => document.getElementById(id);

function fail(msg, field){
  $('aErr').textContent = msg;
  $('aErr').hidden = false;
  if (field){ field.classList.add('bad'); field.focus(); }
}

$('askForm').onsubmit = async e => {
  e.preventDefault();
  $('aErr').hidden = true;
  $('aPhone').classList.remove('bad');

  const phone = $('aPhone').value.trim();
  // Проверяем здесь же, чтобы не гонять заведомо пустое на сервер.
  // Сервер проверяет ещё раз — форму можно обойти
  if (phone.replace(/\D/g, '').length < 10) return fail('Проверьте номер телефона', $('aPhone'));
  if (!$('aMsg').value.trim()) return fail('Напишите, какая деталь нужна', $('aMsg'));

  const btn = $('aSend');
  btn.disabled = true;
  try {
    const r = await fetch('/api/leads', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        phone,
        name: $('aName').value.trim() || null,
        message: $('aMsg').value.trim(),
        donor: $('askForm').dataset.donor,
      })});

    if (r.ok){
      // Форму убираем: повторная отправка того же — только лишний звонок
      $('askForm').innerHTML =
        '<div class="notice notice-accent"><b>Вопрос отправлен.</b> ' +
        'Проверим машину и перезвоним на указанный номер.</div>';
      return;
    }
    const d = await r.json().catch(() => ({}));
    // Ошибка проверки полей приходит списком, а не строкой
    fail(typeof d.detail === 'string' ? d.detail : 'Проверьте поля формы');
  } catch {
    fail('Нет связи с сервером');
  }
  btn.disabled = false;
};
