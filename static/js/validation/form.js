// Проверка формы и показ ошибки у самого поля (static/js/validation/).
//
//   const rules = [
//     [$('phone'), v => Check.phoneRule(v)],
//     [$('name'),  v => Check.name(v, {optional: true})],
//   ];
//   live(rules);                    — проверка при уходе с поля
//   if (!validate(rules)) return;   — перед отправкой: фокус на первой ошибке

// Ошибка у поля: подпись под ним, красная рамка, aria-invalid для
// программ чтения с экрана. Место под подпись — <p id="<id>Err">,
// а если его нет, создаём сразу после поля
let fieldSeq = 0;
function fieldError(input, msg){
  // Поля строк в списках (правка детали, машины) id не имеют — даём свой,
  // чтобы подпись с ошибкой нашлась при следующей проверке
  if (!input.id) input.id = 'fld' + (++fieldSeq);
  let box = document.getElementById(input.id + 'Err');
  if (!box && msg){
    box = document.createElement('p');
    box.className = 'fld-err';
    box.id = input.id + 'Err';
    const anchor = input.closest('.pw') || input;
    anchor.after(box);
    input.setAttribute('aria-describedby', box.id);
  }
  input.classList.toggle('is-bad', !!msg);
  input.setAttribute('aria-invalid', msg ? 'true' : 'false');
  if (box){ box.textContent = msg || ''; box.hidden = !msg; }
}

// Проверка всех правил разом; скрытые поля не в счёт — их не видно,
// и ругаться на них бессмысленно
function validate(rules){
  let first = null;
  for (const [input, rule] of rules){
    if (!input || input.closest('[hidden]')) continue;
    const msg = rule(input.value);
    fieldError(input, msg);
    if (msg && !first) first = input;
  }
  if (first) first.focus();
  return !first;
}

// Живая проверка: ошибка показывается, когда человек ушёл с поля, и
// исчезает, как только он её исправил — не дожидаясь нового ухода
function live(rules){
  for (const [input, rule] of rules){
    if (!input) continue;
    // С задержкой: человек уходит с поля нажатием на кнопку — ошибка,
    // появившись сразу, сдвигала кнопку вниз, и клик приходился мимо.
    // Первое нажатие «Сохранить» ничего не делало
    input.addEventListener('blur', () => setTimeout(() => {
      if (input.value !== '') fieldError(input, rule(input.value));
    }, 200));
    input.addEventListener('input', () => {
      if (input.classList.contains('is-bad') && !rule(input.value)) fieldError(input, '');
    });
  }
}

// Ошибку сервера кладём к полю, если понятно, к какому она: на 422
// сервер отдаёт errors — список {field, msg} (см. app/validation/errors.py)
function serverErrors(d, map){
  if (!Array.isArray(d.errors)) return false;
  let shown = false;
  for (const e of d.errors){
    if (map[e.field]){ fieldError(map[e.field], e.msg); shown = true; }
  }
  return shown;
}

// Правила по селекторам внутри одного блока — для форм в строках списка:
//   const rules = bindRules(row, {'.f-price': v => Check.money(v)});
function bindRules(root, map){
  const rules = Object.entries(map)
    .map(([sel, rule]) => [root.querySelector(sel), rule])
    .filter(([input]) => input);
  live(rules);
  return rules;
}
