// Скрипт шаблона templates/shop/account_addresses.html — мои адреса.
// Правила полей — static/js/validation/rules.js (как при оформлении).

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3000); }
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

let editing = null, cdek = null;
const F = ['fTitle', 'fCity', 'fStreet', 'fHouse', 'fBlock', 'fFlat', 'fPost'];
const rules = [
  [$('fCity'), v => Check.city(v)],
  [$('fStreet'), v => Check.street(v)],
  [$('fHouse'), v => Check.house(v)],
  [$('fBlock'), v => Check.addrPart(v)],
  [$('fFlat'), v => Check.addrPart(v)],
  [$('fPost'), v => Check.postcode(v)],
  [$('fTitle'), v => Check.text(v, {max: 40, what: 'Название'})],
];
live(rules);

function open(a){
  editing = a ? a.id : null;
  cdek = a ? a.cdek_code : null;
  const v = {fTitle: 'title', fCity: 'city', fStreet: 'street', fHouse: 'house', fBlock: 'block', fFlat: 'flat', fPost: 'postcode'};
  F.forEach(id => { $(id).value = a ? (a[v[id]] || '') : ''; fieldError($(id), ''); });
  $('fDefault').checked = a ? a.is_default : !document.querySelector('#addrList li');
  $('addrFormTitle').textContent = a ? 'Изменить адрес' : 'Новый адрес';
  markTitle();
  $('addrForm').hidden = false;
  $('addrForm').scrollIntoView({behavior: 'smooth', block: 'start'});
  $('fCity').focus({preventScroll: true});
}
if ($('addrNew')) $('addrNew').onclick = () => open(null);

// Готовые названия: кнопка ставит название в поле, выбранная подсвечена.
// Вписали своё — подсветка у совпадающей кнопки, если такая есть
const markTitle = () => $('fTitles').querySelectorAll('.chip-btn').forEach(b =>
  b.classList.toggle('on', b.dataset.t.toLowerCase() === $('fTitle').value.trim().toLowerCase()));
$('fTitles').addEventListener('click', e => {
  const b = e.target.closest('.chip-btn');
  if (!b) return;
  $('fTitle').value = b.classList.contains('on') ? '' : b.dataset.t;
  fieldError($('fTitle'), '');
  markTitle();
});
$('fTitle').addEventListener('input', markTitle);
$('fCancel').onclick = () => { $('addrForm').hidden = true; };

// Город — подсказка из справочника СДЭК: код города нужен для тарифов
let ct, seq = 0;
$('fCity').addEventListener('input', () => {
  cdek = null; clearTimeout(ct);
  const q = $('fCity').value.trim();
  if (q.length < 2){ $('fCityList').hidden = true; return; }
  ct = setTimeout(async () => {
    const my = ++seq;
    const rows = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(q))).json();
    if (my !== seq) return;
    $('fCityList').innerHTML = rows.map((c, i) =>
      `<button type="button" role="option" data-i="${i}">${esc(c.full_name)}</button>`).join('');
    $('fCityList').hidden = !rows.length;
    $('fCityList').querySelectorAll('button').forEach(b => b.onclick = () => {
      const c = rows[+b.dataset.i];
      $('fCity').value = c.name; cdek = c.cdek_code; $('fCityList').hidden = true;
      fieldError($('fCity'), ''); $('fStreet').focus();
    });
  }, 250);
});
document.addEventListener('click', e => { if (!e.target.closest('.city-fld')) $('fCityList').hidden = true; });
addressSuggest({city: $('fCity'), street: $('fStreet'), house: $('fHouse'), block: $('fBlock'),
                post: $('fPost'), streetList: $('fStreetList'), houseList: $('fHouseList')});
$('fPost').addEventListener('input', () => { $('fPost').value = $('fPost').value.replace(/\D/g, '').slice(0, 6); });

async function send(method, url, body){
  try {
    const r = await fetch(url, {method, headers: body ? {'Content-Type': 'application/json'} : {},
                                body: body ? JSON.stringify(body) : undefined});
    if (r.ok) return true;
    const d = await r.json().catch(() => ({}));
    toast(typeof d.detail === 'string' ? d.detail : 'Не получилось сохранить');
  } catch { toast('Нет связи с сервером'); }
  return false;
}

$('addrForm').onsubmit = async e => {
  e.preventDefault();
  if (!validate(rules)) return;
  $('fSave').disabled = true;
  const body = {title: $('fTitle').value.trim() || null, city: $('fCity').value.trim(), cdek_code: cdek,
                street: $('fStreet').value.trim(), house: $('fHouse').value.trim(),
                block: $('fBlock').value.trim() || null, flat: $('fFlat').value.trim() || null,
                postcode: $('fPost').value.trim() || null, is_default: $('fDefault').checked};
  if (await send(editing ? 'PUT' : 'POST', '/api/account/addresses' + (editing ? '/' + editing : ''), body)){
    location.reload(); return;
  }
  $('fSave').disabled = false;
};

$('addrList').addEventListener('click', async e => {
  const li = e.target.closest('li[data-id]');
  if (!li) return;
  const a = JSON.parse(li.dataset.addr);
  if (e.target.closest('.edit')) open(a);
  else if (e.target.closest('.make-default')){
    if (await send('PUT', `/api/account/addresses/${a.id}`, {...a, is_default: true})) location.reload();
  } else if (e.target.closest('.del')){
    if (!await askConfirm(`${a.title || a.city}: ${a.street}, ${a.house}`,
                          {title: 'Удалить адрес?', ok: 'Удалить', danger: true})) return;
    if (await send('DELETE', `/api/account/addresses/${a.id}`)) location.reload();
  }
});
