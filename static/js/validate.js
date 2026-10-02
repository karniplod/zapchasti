// Проверка полей ввода — одна на все формы сайта.
//
// Раньше каждая форма проверяла по-своему, а многие не проверяли вовсе:
// ошибку человек узнавал от сервера после отправки, одной строкой на всю
// форму. Здесь правила и показ ошибки у самого поля:
//
//   const rules = [
//     [$('phone'), v => Check.phone(v) ? '' : 'Телефон: +7 900 000-00-00'],
//     [$('name'),  v => Check.name(v, {optional: true})],
//   ];
//   live(rules);                    — проверка при уходе с поля
//   if (!validate(rules)) return;   — перед отправкой: фокус на первой ошибке
//
// Правило — функция от значения: пустая строка значит «всё в порядке»,
// иначе это текст ошибки. Сервер проверяет то же самое ещё раз: скрипт
// может быть выключен, а запрос — отправлен в обход формы.

// Буквы — латиница с акцентами и кириллица. Через \p{L} было бы короче,
// но проверка скриптов (tools/check_js.py) понимает язык до ES2017
const LETTERS = 'A-Za-z\u00C0-\u024F\u0400-\u04FF';
const LETTER = `[${LETTERS}]`;

const Check = {
  // Телефон России: 10 цифр после кода страны; 8 и 7 в начале — одно и то же
  phone(v){
    let d = String(v || '').replace(/\D/g, '');
    if (d.length === 11 && /^[78]/.test(d)) d = d.slice(1);
    // Коды в России начинаются с 3–9: 9xx — мобильные, остальные — города
    return /^[3-9]\d{9}$/.test(d) ? '+7' + d : null;
  },
  email(v){
    v = String(v || '').trim().toLowerCase();
    return v.length <= 200 && /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(v) ? v : null;
  },
  // Поле «Телефон или email»: есть @ — почта, иначе телефон
  login(v){
    return String(v || '').includes('@') ? Check.email(v) : Check.phone(v);
  },
  // VIN: 17 знаков, без I, O и Q — их нет в VIN, чтобы не путать с 1 и 0
  vin(v){
    return /^[A-HJ-NPR-Z0-9]{17}$/.test(String(v || '').toUpperCase());
  },

  // Готовые правила: возвращают текст ошибки или ''
  required(v, what = 'Заполните поле'){
    return String(v || '').trim() ? '' : what;
  },
  name(v, {optional = false} = {}){
    v = String(v || '').trim();
    if (!v) return optional ? '' : 'Как к вам обращаться?';
    if (v.length > 80) return 'Слишком длинно — до 80 символов';
    return new RegExp(`^${LETTER}[${LETTERS} .'’-]*$`).test(v) ? '' : 'Только буквы, пробел и дефис';
  },
  phoneRule(v, {optional = false} = {}){
    if (!String(v || '').trim()) return optional ? '' : 'Укажите телефон';
    return Check.phone(v) ? '' : 'Телефон в формате +7 900 000-00-00';
  },
  emailRule(v, {optional = false} = {}){
    if (!String(v || '').trim()) return optional ? '' : 'Укажите email';
    return Check.email(v) ? '' : 'Проверьте email: mail@example.ru';
  },
  loginRule(v){
    if (!String(v || '').trim()) return 'Укажите телефон или email';
    if (String(v).includes('@')) return Check.email(v) ? '' : 'Проверьте email: mail@example.ru';
    return Check.phone(v) ? '' : 'Телефон в формате +7 900 000-00-00 или email';
  },
  password(v){
    if (!v) return 'Введите пароль';
    if (v.length < 6) return 'Пароль не короче шести символов';
    if (v.length > 200) return 'Слишком длинный пароль';
    return '';
  },
  // Для регистрации построже: пароль из одних цифр подбирается за минуты
  newPassword(v){
    const e = Check.password(v);
    if (e) return e;
    return new RegExp(LETTER).test(v) && /\d/.test(v) ? '' : 'Нужны и буквы, и цифры';
  },
  text(v, {max = 1000, min = 0, what = 'Текст'} = {}){
    v = String(v || '').trim();
    if (v.length < min) return min === 1 ? 'Заполните поле' : `${what}: не короче ${min} символов`;
    return v.length > max ? `${what}: не длиннее ${max} символов` : '';
  },
  number(v, {min = 0, max = 1e9, optional = true, what = 'Число'} = {}){
    if (v === '' || v == null) return optional ? '' : 'Заполните поле';
    const n = Number(v);
    if (!Number.isFinite(n)) return `${what}: только цифры`;
    if (n < min) return `${what}: не меньше ${min.toLocaleString('ru')}`;
    if (n > max) return `${what}: не больше ${max.toLocaleString('ru')}`;
    return '';
  },
};

// Ошибка у поля: подпись под ним, красная рамка, aria-invalid для
// программ чтения с экрана. Место под подпись — <p id="<id>Err">,
// а если его нет, создаём сразу после поля
function fieldError(input, msg){
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
    input.addEventListener('blur', () => {
      if (input.value !== '') fieldError(input, rule(input.value));
    });
    input.addEventListener('input', () => {
      if (input.classList.contains('is-bad') && !rule(input.value)) fieldError(input, '');
    });
  }
}

// Маска телефона: +7 900 000-00-00 по мере набора. Вставка
// «8 (900) 000 00 00» тоже приводится к этому виду
function phoneMask(input){
  if (!input) return;
  input.addEventListener('input', () => {
    let d = input.value.replace(/\D/g, '');
    if (!d){ input.value = ''; return; }
    if (/^[78]/.test(d)) d = d.slice(1);
    d = d.slice(0, 10);
    const p = [d.slice(0, 3), d.slice(3, 6), d.slice(6, 8), d.slice(8, 10)];
    input.value = '+7 ' + p[0] + (p[1] ? ' ' + p[1] : '') + (p[2] ? '-' + p[2] : '')
      + (p[3] ? '-' + p[3] : '');
  });
}

// Ошибку сервера кладём к полю, если понятно, к какому она: 422 от
// FastAPI приходит списком с путём до поля
function serverErrors(d, map){
  if (!Array.isArray(d.detail)) return false;
  let shown = false;
  for (const e of d.detail){
    const key = (e.loc || [])[e.loc.length - 1];
    if (map[key]){ fieldError(map[key], e.msg); shown = true; }
  }
  return shown;
}
