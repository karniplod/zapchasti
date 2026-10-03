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
  // ФИО получателя заказа — те же правила, что на сервере
  // (customer_auth.check_fio): фамилия, имя и отчество полностью;
  // noPatronymic — «Нет отчества», тогда хватит фамилии и имени
  fio(v, {noPatronymic = false} = {}){
    const words = String(v || '').trim().split(/\s+/).filter(Boolean);
    const need = noPatronymic ? 'фамилию и имя' : 'фамилию, имя и отчество';
    if (!words.length) return `Укажите ${need}`;
    if (words.join(' ').length > 80) return 'Слишком длинно — до 80 символов';
    const word = new RegExp(`^${LETTER}+(?:[-'’]${LETTER}+)*$`);
    for (const w of words){
      if (w.includes('.') || w.replace(/[-'’]/g, '').length < 2) return 'Полностью, без инициалов: Иванов Иван Иванович';
      if (!word.test(w)) return 'Только буквы, пробел и дефис';
    }
    if (words.length > 5) return 'Фамилия, имя и отчество — не больше пяти слов';
    if (words.length < (noPatronymic ? 2 : 3))
      return words.length === 2 ? 'Добавьте отчество — или отметьте «Нет отчества»'
                                : `Нужны ${need.replace('фамилию', 'фамилия')} — через пробел`;
    return '';
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
  // Год выпуска: с 1950 и не позже следующего — модельный год бывает вперёд
  year(v, {optional = true} = {}){
    return Check.number(v, {min: 1950, max: new Date().getFullYear() + 1,
                            optional, what: 'Год', plain: true})
      || (v !== '' && !Number.isInteger(+v) ? 'Год — целое число' : '');
  },
  // Госномер: буквы, цифры, пробел; иностранные тоже бывают — без шаблона РФ
  plate(v){
    v = String(v || '').trim();
    if (!v) return '';
    if (v.length > 15) return 'Госномер: не длиннее 15 знаков';
    return /^[0-9A-Za-zА-Яа-яЁё ]+$/.test(v) ? '' : 'Госномер: только буквы и цифры';
  },
  // Каталожный номер: латиница, цифры, дефис, точка, пробел, слэш
  oem(v){
    v = String(v || '').trim();
    if (!v) return '';
    if (v.length > 40) return 'Номер: не длиннее 40 знаков';
    return /^[0-9A-Za-z .\-\/]+$/.test(v) ? '' : 'Номер: латиница, цифры, дефис';
  },
  vinRule(v, {optional = true} = {}){
    v = String(v || '').trim();
    if (!v) return optional ? '' : 'Введите VIN';
    if (v.length !== 17) return `В VIN 17 знаков, сейчас ${v.length}`;
    return Check.vin(v) ? '' : 'В VIN только латиница и цифры, без I, O, Q';
  },
  // Дата не из будущего: приёмку задним числом вносят, вперёд — нет
  pastDate(v){
    if (!v) return '';
    const d = new Date(v + 'T00:00:00');
    if (isNaN(d)) return 'Неверная дата';
    if (d > new Date()) return 'Дата не может быть в будущем';
    return d.getFullYear() < 2000 ? 'Дата не раньше 2000 года' : '';
  },
  money(v, what = 'Цена'){ return Check.number(v, {min: 0, max: 100000000, what}); },

  text(v, {max = 1000, min = 0, what = 'Текст'} = {}){
    v = String(v || '').trim();
    if (v.length < min) return min === 1 ? 'Заполните поле' : `${what}: не короче ${min} символов`;
    return v.length > max ? `${what}: не длиннее ${max} символов` : '';
  },
  number(v, {min = 0, max = 1e9, optional = true, what = 'Число', plain = false} = {}){
    if (v === '' || v == null) return optional ? '' : 'Заполните поле';
    const n = Number(v);
    // Годы без разрядов: «1 950» читается как число, а не как год
    const f = x => plain ? String(x) : x.toLocaleString('ru');
    if (!Number.isFinite(n)) return `${what}: только цифры`;
    if (n < min) return `${what}: не меньше ${f(min)}`;
    if (n > max) return `${what}: не больше ${f(max)}`;
    return '';
  },
};

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
    input.addEventListener('blur', () => {
      if (input.value !== '') fieldError(input, rule(input.value));
    });
    input.addEventListener('input', () => {
      if (input.classList.contains('is-bad') && !rule(input.value)) fieldError(input, '');
    });
  }
}

// ФИО с заглавных букв, когда человек ушёл с поля: «иванов иван» →
// «Иванов Иван». Частицы отчества (оглы, кызы) остаются строчными
function fioCase(input){
  if (!input) return;
  const lower = ['оглы', 'кызы', 'улы', 'гызы', 'уулу'];
  input.addEventListener('blur', () => {
    input.value = input.value.trim().split(/\s+/).filter(Boolean).map(w =>
      lower.includes(w.toLowerCase()) ? w
        : w.split('-').map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('-')).join(' ');
  });
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

// Ошибку сервера кладём к полю, если понятно, к какому она: на 422
// сервер отдаёт errors — список {field, msg} (см. app/errors.py)
function serverErrors(d, map){
  if (!Array.isArray(d.errors)) return false;
  let shown = false;
  for (const e of d.errors){
    if (map[e.field]){ fieldError(map[e.field], e.msg); shown = true; }
  }
  return shown;
}

// Поле VIN: только допустимые знаки, верхний регистр, кнопка — при 17.
// Знаки, которых в VIN не бывает (I, O, Q, кириллица), не пропадают
// молча: человек видит, почему его буква не набралась
function vinInput(input, onGo, button){
  const err = document.getElementById(input.id + 'Err');
  const say = msg => { if (err){ err.textContent = msg; err.hidden = !msg; } };
  input.addEventListener('input', () => {
    const raw = input.value.toUpperCase();
    const v = raw.replace(/[^A-HJ-NPR-Z0-9]/g, '');
    input.value = v;
    input.classList.remove('bad');
    button.disabled = v.length !== 17;
    const dropped = raw.replace(/[A-HJ-NPR-Z0-9]/g, '').replace(/\s/g, '');
    say(/[IOQ]/.test(dropped) ? 'Букв I, O и Q в VIN нет — это цифры 1 и 0'
      : /[А-ЯЁ]/.test(dropped) ? 'VIN пишется латиницей — переключите раскладку'
      : dropped ? 'В VIN только латинские буквы и цифры' : '');
  });
  input.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    if (input.value.length === 17) onGo();
    else say(`В VIN 17 знаков, сейчас ${input.value.length}`);
  });
  button.onclick = onGo;
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
