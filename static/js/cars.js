// Страница «Машины». Выбор марки списком: со скриптом страница
// уходит сразу по выбору, поэтому кнопка «Показать» лишняя. Без
// скрипта она остаётся и форма работает обычной отправкой.
(function(){
  const sel = document.getElementById('brandPick');
  const go = document.getElementById('brandGo');
  if (!sel || !go) return;
  go.hidden = true;
  sel.addEventListener('change', () => sel.form.submit());
})();
