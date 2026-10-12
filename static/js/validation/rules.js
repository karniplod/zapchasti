// Правила проверки полей — одни на все формы сайта и бэкенда.
//
// Правило — функция от значения: пустая строка значит «всё в порядке»,
// иначе это текст ошибки. Те же правила на сервере — app/validation/:
// форма подсказывает сразу, а сервер проверяет ещё раз (скрипт можно
// выключить, а запрос отправить в обход формы). Меняя правило здесь,
// меняйте и там.
//
// Папка static/js/validation/ подключается целиком — шаблоном
// templates/_validation.html:
//   rules.js  — Check: телефон, email, ФИО, адрес, VIN, год, числа…
//   form.js   — ошибка у поля, проверка формы, живая проверка
//   inputs.js — маски и поведение полей: телефон, ФИО, VIN

// Буквы — латиница с акцентами и кириллица. Через \p{L} было бы короче,
// но проверка скриптов (tools/check_js.py) понимает язык до ES2017
const LETTERS = 'A-Za-z\u00C0-\u024F\u0400-\u04FF';
const LETTER = `[${LETTERS}]`;
// Дом, корпус, квартира: буквы, цифры, дробь, точка, дефис
const ADDR_SHORT = new RegExp(`^[${LETTERS}\\d/ .-]{1,20}$`);

// Страны для телефона: код, сколько цифр после него, с чего начинается
// номер, группы для показа. Тот же список на сервере —
// PHONE_COUNTRIES в app/validation/people.py. У России и Казахстана общий
// код +7: российские номера начинаются с 3–5, 8, 9, казахстанские — с 6, 7
const PHONE_COUNTRIES = [
  {iso: 'RU', name: 'Россия', code: '7', len: 10, first: /^[3-589]/, groups: [3, 3, 2, 2], sample: '912 345-67-89'},
  {iso: 'KZ', name: 'Казахстан', code: '7', len: 10, first: /^[67]/, groups: [3, 3, 2, 2], sample: '701 234-56-78'},
  {iso: 'BY', name: 'Беларусь', code: '375', len: 9, first: /^(1[5-7]|2[1-59]|33|44)/, groups: [2, 3, 2, 2], sample: '29 123-45-67'},
  {iso: 'UA', name: 'Украина', code: '380', len: 9, first: /^[3-9]/, groups: [2, 3, 2, 2], sample: '50 123-45-67'},
  {iso: 'UZ', name: 'Узбекистан', code: '998', len: 9, first: /^[2-9]/, groups: [2, 3, 2, 2], sample: '90 123-45-67'},
  {iso: 'KG', name: 'Киргизия', code: '996', len: 9, first: /^[2-9]/, groups: [3, 3, 3], sample: '555 123-456'},
  {iso: 'TJ', name: 'Таджикистан', code: '992', len: 9, first: /^[3-9]/, groups: [2, 3, 2, 2], sample: '93 123-45-67'},
  {iso: 'AM', name: 'Армения', code: '374', len: 8, first: /^[1-9]/, groups: [2, 3, 3], sample: '91 123-456'},
  {iso: 'AZ', name: 'Азербайджан', code: '994', len: 9, first: /^[1-9]/, groups: [2, 3, 2, 2], sample: '50 123-45-67'},
  {iso: 'GE', name: 'Грузия', code: '995', len: 9, first: /^[3-9]/, groups: [3, 2, 2, 2], sample: '555 12-34-56'},
  {iso: 'MD', name: 'Молдова', code: '373', len: 8, first: /^[2-9]/, groups: [2, 3, 3], sample: '69 123-456'},
  {iso: 'TM', name: 'Туркменистан', code: '993', len: 8, first: /^[1-9]/, groups: [2, 3, 3], sample: '65 123-456'},
];

const Phone = {
  // Страна по цифрам с кодом: '375291234567' → Беларусь. Для +7 решает
  // первая цифра номера; пока её нет — null
  country(digits){
    for (const c of PHONE_COUNTRIES){
      if (!digits.startsWith(c.code)) continue;
      const rest = digits.slice(c.code.length);
      if (c.code !== '7' || (rest && c.first.test(rest))) return c;
    }
    return null;
  },
  // Национальная часть по группам: 912 345-67-89
  group(d, c){
    const out = []; let i = 0;
    for (const n of c.groups){ if (i >= d.length) break; out.push(d.slice(i, i + n)); i += n; }
    return out.length > 2 ? out.slice(0, 2).join(' ') + '-' + out.slice(2).join('-') : out.join(' ');
  },
  format(e164){
    const d = String(e164 || '').replace(/\D/g, '');
    const c = Phone.country(d);
    return c ? '+' + c.code + ' ' + Phone.group(d.slice(c.code.length), c) : String(e164 || '');
  },
};

const Check = {
  // Телефон: +код и номер страны из PHONE_COUNTRIES → +79123456789,
  // +375291234567. Без «+» — номер России: 8 и 7 в начале одно и то же
  phone(v){
    const raw = String(v || '').trim();
    let d = raw.replace(/\D/g, '');
    if (!raw.startsWith('+')){
      if (d.length === 11 && /^[78]/.test(d)) d = '7' + d.slice(1);
      else if (d.length === 10) d = '7' + d;
    }
    const c = Phone.country(d);
    if (!c) return null;
    const rest = d.slice(c.code.length);
    return rest.length === c.len && c.first.test(rest) ? '+' + d : null;
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
  // (app/validation/people.py, check_fio): фамилия, имя и отчество полностью;
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
    if (Check.phone(v)) return '';
    // Страна понятна — подсказываем её формат и сколько цифр не хватает
    const d = String(v).replace(/\D/g, '');
    const c = String(v).trim().startsWith('+') ? Phone.country(d)
      || PHONE_COUNTRIES.find(x => d.startsWith(x.code) && x.code !== '7') : null;
    if (!c) return 'Телефон в формате +7 912 345-67-89';
    const rest = d.slice(c.code.length);
    if (rest.length < c.len) return `${c.name}: не хватает цифр — нужно ${c.len} после +${c.code}`;
    if (rest.length > c.len) return `${c.name}: лишние цифры — нужно ${c.len} после +${c.code}`;
    return `${c.name}: такого номера нет — например, +${c.code} ${c.sample}`;
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
  // Адрес по полям — те же правила, что на сервере (app/validation/address.py)
  street(v){
    v = String(v || '').trim();
    if (!v) return 'Укажите улицу';
    return new RegExp(`^[${LETTERS}\\d .,'«»"()№/-]{2,120}$`).test(v) ? '' : 'Улица: буквы, цифры, точка, дефис';
  },
  house(v){
    v = String(v || '').trim();
    if (!v) return 'Укажите дом';
    return /\d/.test(v) && ADDR_SHORT.test(v) ? '' : 'Дом: номер, например 10 или 10/2';
  },
  // Корпус, строение, квартира, офис — необязательные
  addrPart(v){
    v = String(v || '').trim();
    return !v || ADDR_SHORT.test(v) ? '' : 'До 20 символов: буквы и цифры';
  },
  postcode(v, {required = false} = {}){
    v = String(v || '').trim();
    if (!v) return required ? 'Для Почты России нужен индекс' : '';
    return /^\d{6}$/.test(v) ? '' : 'Индекс — шесть цифр';
  },
  city(v){ return Check.text(v, {min: 2, max: 120, what: 'Город'}); },
  // Промокод — как его заводит менеджер (app/validation/promo.py)
  promo(v){
    v = String(v || '').trim().toUpperCase();
    if (!v) return 'Введите промокод';
    return /^[A-Z0-9_-]{3,30}$/.test(v) ? '' : 'Промокод: латиница, цифры и дефис, от 3 до 30 знаков';
  },
  // Сколько баллов списать: целое, не больше доступного
  bonus(v, max){
    v = String(v ?? '').trim();
    if (!v) return '';
    if (!/^\d+$/.test(v)) return 'Баллы — целым числом';
    return +v > max ? `Можно списать не больше ${max.toLocaleString('ru')}` : '';
  },
  // Номер отслеживания посылки (app/validation/orders.py)
  track(v){
    v = String(v || '').replace(/\s/g, '');
    return !v || /^[A-Za-z0-9-]{4,40}$/.test(v) ? '' : 'Номер: латиница, цифры и дефис, 4–40 знаков';
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
