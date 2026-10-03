// Скрипт шаблона templates/admin/promo.html — промокоды.
// Правила полей те же, что на сервере (app/validation/promo.py)

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const money = v => Math.round(+v || 0).toLocaleString('ru') + ' ₽';
const date = s => s ? new Date(s + 'T00:00:00').toLocaleDateString('ru') : '';
let rows = [], editing = null;

let tt;
function toast(m, k = ''){ const el = $('toast'); el.textContent = m;
  el.className = `toast show ${k}`; clearTimeout(tt); tt = setTimeout(() => el.className = 'toast', 2600); }

const rules = [
  [$('pCode'), v => /^[A-Za-z0-9_-]{3,30}$/.test(v.trim()) ? '' : 'Латиница, цифры, дефис — от 3 до 30 знаков'],
  [$('pValue'), v => {
    const n = +v;
    if (!v || !(n > 0)) return 'Больше нуля';
    return $('pKind').value === 'percent' && n > 90 ? 'Не больше 90%' : '';
  }],
  [$('pMin'), v => Check.number(v, {min: 0, max: 100000000, what: 'Сумма'})],
  [$('pMax'), v => !v || (Number.isInteger(+v) && +v >= 1) ? '' : 'Целое число от 1'],
  [$('pEnd'), v => v && $('pStart').value && v < $('pStart').value ? 'Окончание раньше начала' : ''],
];
live(rules);
$('pCode').addEventListener('input', () => { $('pCode').value = $('pCode').value.toUpperCase().replace(/\s/g, ''); });

// Код, который легко продиктовать: без 0/O и 1/I
$('pGen').onclick = () => {
  const abc = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  $('pCode').value = 'AD' + Array.from({length: 6}, () => abc[Math.floor(Math.random() * abc.length)]).join('');
  fieldError($('pCode'), '');
};

function fill(p){
  editing = p ? p.id : null;
  $('pCode').value = p ? p.code : '';
  $('pKind').value = p ? p.kind : 'percent';
  $('pValue').value = p ? Math.round(+p.value) : '';
  $('pMin').value = p && +p.min_total ? Math.round(+p.min_total) : '';
  $('pStart').value = p && p.starts_at || '';
  $('pEnd').value = p && p.ends_at || '';
  $('pMax').value = p && p.max_uses || '';
  $('pComment').value = p && p.comment || '';
  $('pOnce').checked = p ? p.once_per_customer : true;
  $('pActive').checked = p ? p.active : true;
  $('formTitle').textContent = p ? `Промокод ${p.code}` : 'Новый промокод';
  $('pSave').textContent = p ? 'Сохранить' : 'Создать промокод';
  $('pCancel').hidden = !p;
  rules.forEach(([i]) => fieldError(i, ''));
}
$('pCancel').onclick = () => fill(null);

$('promoForm').onsubmit = async e => {
  e.preventDefault();
  if (!validate(rules)) return;
  const body = {
    code: $('pCode').value.trim(), kind: $('pKind').value, value: +$('pValue').value,
    min_total: +$('pMin').value || 0, starts_at: $('pStart').value || null, ends_at: $('pEnd').value || null,
    max_uses: $('pMax').value ? +$('pMax').value : null, once_per_customer: $('pOnce').checked,
    active: $('pActive').checked, comment: $('pComment').value.trim() || null};
  $('pSave').disabled = true;
  try {
    const r = await fetch('/api/manage/promo' + (editing ? '/' + editing : ''), {
      method: editing ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)});
    const d = await r.json().catch(() => ({}));
    if (r.ok){ toast(editing ? 'Сохранено' : `Промокод ${body.code.toUpperCase()} создан`, 'ok'); fill(null); load(); }
    else if (!serverErrors(d, {code: $('pCode'), value: $('pValue'), min_total: $('pMin'), max_uses: $('pMax')}))
      toast(typeof d.detail === 'string' ? d.detail : 'Не получилось сохранить', 'err');
  } catch { toast('Нет связи с сервером', 'err'); }
  $('pSave').disabled = false;
};

async function load(){
  rows = await (await fetch('/api/manage/promo')).json();
  if (!rows.length){ $('list').innerHTML = '<p class="blank">Промокодов пока нет</p>'; return; }
  $('list').innerHTML = `<div class="p-table">
    <div class="p-row p-hd"><span>Код</span><span>Скидка</span><span>Условия</span><span>Срок</span>
      <span class="r">Заказов</span><span class="r">Скидок дано</span><span>Состояние</span><span></span></div>
    ${rows.map(p => `<div class="p-row ${p.state === 'действует' ? '' : 'is-off'}">
      <span class="p-code">${esc(p.code)}<small>${esc(p.comment || '')}</small></span>
      <span><b>${p.kind === 'percent' ? Math.round(+p.value) + '%' : money(p.value)}</b></span>
      <span class="p-cond">${+p.min_total ? 'от ' + money(p.min_total) : 'любой заказ'}${p.once_per_customer ? '<br>один раз на покупателя' : ''}</span>
      <span class="p-cond">${p.starts_at || p.ends_at ? (p.starts_at ? 'с ' + date(p.starts_at) : '') + (p.ends_at ? ' по ' + date(p.ends_at) : '') : 'бессрочно'}</span>
      <span class="r">${p.uses}${p.max_uses ? ' из ' + p.max_uses : ''}</span>
      <span class="r">${money(p.given)}</span>
      <span><span class="st ${p.state === 'действует' ? 'on' : ''}">${p.state}</span></span>
      <span class="p-act"><button type="button" class="btn" data-edit="${p.id}">Изменить</button></span>
    </div>`).join('')}</div>`;
}
$('list').addEventListener('click', e => {
  const b = e.target.closest('[data-edit]');
  if (!b) return;
  fill(rows.find(p => p.id === +b.dataset.edit));
  $('formBox').scrollIntoView({behavior: 'smooth'});
});

load();
