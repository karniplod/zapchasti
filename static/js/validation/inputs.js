// Маски и поведение полей ввода (static/js/validation/): телефон
// набирается в виде +7 900 000-00-00, ФИО — с заглавных букв, VIN —
// только допустимыми знаками.

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
