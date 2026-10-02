// Скрипт шаблона templates/car.html: вопрос «что можно снять под заказ».
// Уходит в те же заявки, что и форма на «Контактах», с кодом машины —
// менеджер в бэкенде видит, о какой машине речь.

const $ = id => document.getElementById(id);

function fail(msg){
  $('aErr').textContent = msg;
  $('aErr').hidden = false;
}

// Проверяем здесь же, чтобы не гонять заведомо неверное на сервер.
// Сервер проверяет ещё раз — форму можно обойти
const rules = [
  [$('aPhone'), v => Check.phoneRule(v)],
  [$('aName'), v => Check.name(v, {optional: true})],
  [$('aMsg'), v => v.trim().length < 3 ? 'Напишите, какая деталь нужна'
                                        : Check.text(v, {max: 2000, what: 'Сообщение'})],
];
live(rules);
phoneMask($('aPhone'));

$('askForm').onsubmit = async e => {
  e.preventDefault();
  $('aErr').hidden = true;
  if (!validate(rules)) return;
  const phone = $('aPhone').value.trim();

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
