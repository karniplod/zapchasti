// Маски и поведение полей ввода (static/js/validation/): телефон
// набирается в виде +7 900 000-00-00, ФИО — с заглавных букв, VIN —
// только допустимыми знаками.

// Телефон с выбором страны: слева от поля кнопка «флаг +код», по ней —
// список стран (PHONE_COUNTRIES в rules.js). Номер набирается по маске
// страны: +7 912 345-67-89, +375 29 123-45-67. Вставили номер с «+» —
// страна выбирается сама; «8 (912) …» без «+» — это Россия
function phoneMask(input){
  if (!input || input.dataset.phone) return;
  input.dataset.phone = '1';
  const wrap = document.createElement('div');
  wrap.className = 'phone-fld';
  input.parentNode.insertBefore(wrap, input);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'phone-cc';
  btn.setAttribute('aria-haspopup', 'listbox');
  btn.setAttribute('aria-expanded', 'false');
  const list = document.createElement('div');
  list.className = 'phone-list';
  list.setAttribute('role', 'listbox');
  list.hidden = true;
  list.innerHTML = PHONE_COUNTRIES.map((c, i) =>
    `<button type="button" role="option" data-i="${i}"><span class="flag flag-${c.iso}"></span>`
    + `<span class="pc-name">${c.name}</span><span class="pc-code">+${c.code}</span></button>`).join('');
  wrap.append(btn, input, list);

  let cur = PHONE_COUNTRIES[0];
  const show = c => {
    cur = c;
    // На кнопке — флаг: код страны уже стоит в начале номера
    btn.innerHTML = `<span class="flag flag-${c.iso}"></span>`
      + '<svg viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4" stroke="currentColor" stroke-width="1.6" fill="none"/></svg>';
    btn.setAttribute('aria-label', `Страна: ${c.name}, +${c.code}`);
    input.placeholder = `+${c.code} ${c.sample}`;
    list.querySelectorAll('[role=option]').forEach(o =>
      o.setAttribute('aria-selected', String(PHONE_COUNTRIES[+o.dataset.i] === c)));
  };
  // Номер по маске страны: «+код» и сколько цифр набрано
  const render = national => {
    const d = national.slice(0, cur.len);
    input.value = d ? '+' + cur.code + ' ' + Phone.group(d, cur) : '';
  };
  const open = on => {
    list.hidden = !on;
    btn.setAttribute('aria-expanded', String(on));
    if (on) (list.querySelector('[aria-selected=true]') || list.querySelector('button')).focus();
  };

  input.addEventListener('input', e => {
    const raw = input.value.trim();
    let d = raw.replace(/\D/g, '');
    // Стирают — не мешаем: иначе «+375 » возвращался бы на каждое нажатие
    const erasing = (e.inputType || '').startsWith('delete');
    if (!d){ input.value = raw.startsWith('+') && !erasing ? '+' : ''; return; }
    if (raw.startsWith('+')){
      // Код набран или вставлен — страна по нему; у +7 до первой цифры номера оставляем выбранную
      const c = Phone.country(d) || (d.startsWith(cur.code) ? cur : null)
        || PHONE_COUNTRIES.find(x => x.code !== '7' && d.startsWith(x.code));
      // Код ещё набирают (+3, +37) — оставляем как есть
      if (!c){ input.value = '+' + d.slice(0, 15); return; }
      if (c !== cur) show(c);
      d = d.slice(c.code.length);
      if (!d){ input.value = erasing ? '+' + c.code : '+' + c.code + ' '; return; }
    } else if (cur.code === '7' && (/^8/.test(d) || (d.length === 11 && /^7/.test(d)))){
      // 8 912 … и 7 912 … — привычная запись без «+»: восьмёрка в начале — не номер
      d = d.slice(1);
      if (!d){ input.value = '+7 '; return; }
    } else if (cur.code !== '7' && d.startsWith(cur.code) && d.length > cur.len){
      d = d.slice(cur.code.length);
    }
    // Россия и Казахстан делят +7 — по первой цифре номера понятно, чей он
    if (cur.code === '7' && d){
      const kz = /^[67]/.test(d);
      if (kz !== (cur.iso === 'KZ')) show(PHONE_COUNTRIES.find(x => x.iso === (kz ? 'KZ' : 'RU')));
    }
    render(d);
  });

  btn.onclick = () => open(list.hidden);
  list.addEventListener('click', e => {
    const o = e.target.closest('[role=option]');
    if (!o) return;
    const national = input.value.replace(/\D/g, '').slice(cur.code.length);
    show(PHONE_COUNTRIES[+o.dataset.i]);
    render(national);
    open(false);
    input.focus();
    input.dispatchEvent(new Event('change'));
  });
  list.addEventListener('keydown', e => {
    const opts = [...list.querySelectorAll('[role=option]')];
    const k = opts.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
      e.preventDefault();
      opts[(k + (e.key === 'ArrowDown' ? 1 : opts.length - 1)) % opts.length].focus();
    } else if (e.key === 'Escape'){ open(false); btn.focus(); }
  });
  document.addEventListener('click', e => { if (!wrap.contains(e.target)) open(false); });

  // Номер уже есть (профиль, правка заказа) — страна по нему
  const norm = Check.phone(input.value);
  show((norm && Phone.country(norm.slice(1))) || PHONE_COUNTRIES[0]);
  if (norm) input.value = Phone.format(norm);
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
