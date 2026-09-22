// Скрипт шаблона templates/admin/intake.html.

const $ = id => document.getElementById(id);
const SEGMENTS = ['wmi','wmi','wmi','vds','vds','vds','vds','vds','chk','yr','ser','ser','ser','ser','ser','ser','ser'];
let decoded = null, checkOk = true;

// --- линейка -------------------------------------------------------
const ruler = $('ruler');
SEGMENTS.forEach((seg, i) => {
  const c = document.createElement('div');
  c.className = `cell seg-${seg}`;
  c.textContent = i + 1;
  ruler.appendChild(c);
});

function paintRuler(vin){
  [...ruler.children].forEach((c, i) => {
    const ch = vin[i];
    c.classList.toggle('filled', !!ch);
    c.classList.toggle('bad', i === 8 && !!ch && !checkOk);
    c.textContent = ch || (i + 1);
  });
}

// --- ввод VIN ------------------------------------------------------
let timer;
$('vin').addEventListener('input', e => {
  const v = e.target.value.toUpperCase().replace(/[^A-HJ-NPR-Z0-9]/g, '');
  e.target.value = v;
  paintRuler(v);
  clearTimeout(timer);
  $('vin').classList.toggle('bad', v.length === 17 && !checkOk);
  if (v.length === 17) timer = setTimeout(() => decodeVin(v), 250);
  else { $('readout').innerHTML = ''; $('notes').innerHTML = ''; decoded = null; }
  refreshSave();
});

async function decodeVin(vin){
  $('status').textContent = 'Разбираю номер…';
  try {
    const r = await fetch('/api/vin/decode', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({vin})
    });
    const d = await r.json();
    $('status').textContent = '';
    render(d);
  } catch (err) {
    $('status').textContent = '';
    $('notes').innerHTML = note('err', 'Сервер не ответил. Данные можно ввести вручную.');
  }
}

const note = (kind, text) => `<div class="note ${kind}">${text}</div>`;

function render(d){
  decoded = d;
  checkOk = d.valid;
  paintRuler($('vin').value);
  $('vin').classList.toggle('bad', !d.valid);

  if (!d.valid){
    $('readout').innerHTML = '';
    $('notes').innerHTML = d.errors.map(e => note('err', e)).join('');
    refreshSave();
    return;
  }

  const cell = (label, value, mono) =>
    `<div><dt>${label}</dt><dd class="${mono?'mono':''}">${value ?? '—'}</dd></div>`;

  $('readout').innerHTML = '<dl class="readout">' +
    cell('Страна', d.country) +
    cell('Завод', d.manufacturer || `WMI ${d.wmi} неизвестен`) +
    cell('Год', d.year) +
    cell('Код модели', d.vds, true) +
    cell('Номер кузова', d.serial, true) +
    '</dl>';

  let n = '';
  if (d.duplicate)
    n += note('err', `Этот VIN уже принят — донор ${d.duplicate.code}, статус «${d.duplicate.status}».`);
  if (d.chain)
    n += note('ok', `Определено по прошлым машинам: ${d.chain.brand} ${d.chain.model} ${d.chain.generation}, ${d.chain.modification}. Проверьте и поправьте, если не сходится.`);
  n += (d.warnings || []).map(w => note('warn', w)).join('');
  $('notes').innerHTML = n;

  if (d.year && !$('year').value) $('year').value = d.year;
  if (d.chain) applyChain(d.chain);
  refreshSave();
}

// --- каскад справочников -------------------------------------------
async function fill(sel, url, format, keep){
  const el = $(sel);
  el.innerHTML = '<option value="">—</option>';
  el.disabled = true;
  if (!url) return;
  const rows = await (await fetch(url)).json();
  rows.forEach(r => {
    const o = document.createElement('option');
    o.value = r.id;
    o.textContent = format(r);
    el.appendChild(o);
  });
  el.disabled = false;
  if (keep) el.value = keep;
}

const fmtGen = g => `${g.name}${g.body_type ? ', ' + g.body_type : ''} (${g.year_from}–${g.year_to || 'н.в.'})`;
const fmtMod = m => [m.engine_volume && m.engine_volume + ' л', m.engine_code, m.power_hp && m.power_hp + ' л.с.', m.transmission, m.drive, m.doors && m.doors + ' дв.'].filter(Boolean).join(' · ');

// Комплектации есть далеко не у всех модификаций: если справочник пуст,
// поле не показываем, чтобы не мозолило глаза пустым списком
async function loadComplectations(modificationId){
  const box = $('complectationBox');
  if (!modificationId){ box.hidden = true; $('complectation').innerHTML = ''; return; }
  await fill('complectation', `/api/complectations?modification_id=${modificationId}`, r => r.name);
  box.hidden = $('complectation').options.length <= 1;
}

async function applyChain(c){
  await fill('model', `/api/models?brand_id=${c.brand_id}`, r => r.name, c.model_id);
  await fill('generation', `/api/generations?model_id=${c.model_id}`, fmtGen, c.generation_id);
  await fill('modification', `/api/modifications?generation_id=${c.generation_id}`, fmtMod, c.modification_id);
  $('brand').value = c.brand_id;
  ['brand','model','generation','modification'].forEach(id => $(id).classList.add('auto'));
  await loadComplectations(c.modification_id);
  refreshSave();
}

$('brand').addEventListener('change', async e => {
  clearAuto();
  await fill('model', e.target.value ? `/api/models?brand_id=${e.target.value}` : null, r => r.name);
  await fill('generation', null); await fill('modification', null);
  await loadComplectations(null);
  refreshSave();
});
$('model').addEventListener('change', async e => {
  clearAuto();
  await fill('generation', e.target.value ? `/api/generations?model_id=${e.target.value}` : null, fmtGen);
  await fill('modification', null);
  await loadComplectations(null);
  refreshSave();
});
$('generation').addEventListener('change', async e => {
  clearAuto();
  await fill('modification', e.target.value ? `/api/modifications?generation_id=${e.target.value}` : null, fmtMod);
  await loadComplectations(null);
  refreshSave();
});
$('modification').addEventListener('change', async e => {
  await loadComplectations(e.target.value || null);
  refreshSave();
});

const clearAuto = () => ['brand','model','generation','modification']
  .forEach(id => $(id).classList.remove('auto'));

// --- фото ----------------------------------------------------------
let files = [];
$('dropzone').addEventListener('click', () => $('photos').click());
$('photos').addEventListener('change', async e => {
  // Снимки осмотра тоже квадратные: одна логика на все формы
  files = [];
  for (const f of e.target.files){
    const blob = await cropImage(f);
    if (blob) files.push(new File([blob], 'photo.jpg', {type:'image/jpeg'}));
  }
  $('thumbs').innerHTML = '';
  files.forEach(f => {
    const img = document.createElement('img');
    img.src = URL.createObjectURL(f);
    img.onload = () => URL.revokeObjectURL(img.src);
    $('thumbs').appendChild(img);
  });
  $('dropzone').textContent = files.length ? `Выбрано фото: ${files.length}` : 'Снять или выбрать фото';
});

// --- сохранение ------------------------------------------------------
$('noVin').addEventListener('change', e => {
  $('vin').disabled = e.target.checked;
  if (e.target.checked){ $('vin').value=''; paintRuler(''); $('readout').innerHTML=''; $('notes').innerHTML=''; decoded=null; }
  refreshSave();
});

// Кнопка молча гасла, и приёмщик не понимал, чего от него хотят.
// Подсвечиваем незаполненное и пишем прямо на кнопке, что осталось
function refreshSave(){
  const vinOk = $('noVin').checked || (decoded && decoded.valid);
  const genOk = !!$('generation').value;
  const modOk = !!$('modification').value;

  $('vin').classList.toggle('need', !vinOk);
  $('needVin').hidden = vinOk;
  $('generation').classList.toggle('need', !genOk);
  $('needGen').hidden = genOk;
  // Модификация обязательна: в справочнике она есть у каждого поколения
  $('modification').classList.toggle('need', genOk && !modOk);
  $('needMod').hidden = !(genOk && !modOk);

  $('save').disabled = !(vinOk && genOk && modOk);
  if (!vinOk)       $('save').textContent = 'Введите VIN или отметьте, что его нет';
  else if (!genOk)  $('save').textContent = 'Выберите модель и поколение';
  else if (!modOk)  $('save').textContent = 'Выберите модификацию';
  else              $('save').textContent = 'Принять автомобиль';
}

$('save').addEventListener('click', async () => {
  $('save').disabled = true;
  $('status').textContent = 'Сохраняю…';
  const body = {
    vin: $('noVin').checked ? null : $('vin').value,
    generation_id: +$('generation').value,
    modification_id: $('modification').value ? +$('modification').value : null,
    complectation_id: $('complectation').value ? +$('complectation').value : null,
    year: $('year').value ? +$('year').value : null,
    color: $('color').value || null,
    mileage_km: $('mileage').value ? +$('mileage').value : null,
    plate: $('plate').value || null,
    purchase_price: $('price').value ? +$('price').value : null,
    accepted_at: $('acceptedAt').value || null,
    branch_id: $('branch').value ? +$('branch').value : null,
    notes: $('notes-field').value || null,
    public_note: $('pub-field').value.trim() || null
  };
  // Приёмка машины и загрузка фото разнесены намеренно: раньше всё
  // лежало в одном try, и любая ошибка после успешного сохранения
  // (в том числе опечатка в коде страницы) показывалась приёмщику как
  // «нет связи». Машина при этом была принята, он жал «Сохранить»
  // повторно и получал «VIN уже принят».
  let donor;
  try {
    const r = await fetch('/api/donors', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
    });
    if (!r.ok){
      const err = await r.json().catch(() => ({}));
      $('status').textContent = '';
      $('notes').innerHTML = note('err', err.detail || 'Сохранить не удалось');
      $('save').disabled = false;
      return;
    }
    donor = await r.json();
  } catch (e) {
    console.error('Приёмка не удалась:', e);
    $('status').textContent = '';
    $('notes').innerHTML = note('err', 'Нет связи с сервером. Не закрывайте вкладку.');
    $('save').disabled = false;
    return;
  }

  // Дальше машина уже в базе — что бы ни случилось, это не обрыв связи
  if (files.length){
    $('status').textContent = 'Загружаю фото…';
    try {
      const fd = new FormData();
      files.forEach(f => fd.append('files', f));
      const pr = await fetch(`/api/donors/${donor.id}/photos`, {method:'POST', body: fd});
      if (!pr.ok) throw new Error(`HTTP ${pr.status}`);
    } catch (e) {
      console.error('Фото не загрузились:', e);
      $('notes').innerHTML = note('warn',
        `${donor.code} принят, но фото не загрузились. Добавьте их позже из карточки машины.`);
    }
  }

  $('status').textContent = `Принят: ${donor.code}. Можно вводить следующий.`;
  resetForm();
});

function resetForm(){
  $('vin').value = ''; $('vin').disabled = false; $('noVin').checked = false;
  paintRuler(''); $('readout').innerHTML = ''; $('notes').innerHTML = '';
  // Дату приёмки и филиал намеренно не сбрасываем: партию машин
  // заводят одним заходом, в одном месте и обычно одной датой
  ['year','color','mileage','plate','price','notes-field','pub-field'].forEach(id => $(id).value = '');
  ['model','generation','modification'].forEach(id => { $(id).innerHTML=''; $(id).disabled = true; });
  $('complectation').innerHTML = ''; $('complectationBox').hidden = true;
  $('brand').value = ''; clearAuto();
  files = []; $('photos').value = ''; $('thumbs').innerHTML = '';
  $('dropzone').textContent = 'Снять или выбрать фото — четыре угла, салон, подкапотное';
  decoded = null; refreshSave(); $('vin').focus();
}

$('reset').addEventListener('click', resetForm);

// --- старт ---------------------------------------------------------
paintRuler('');
// Будущей датой машину принять нельзя — верхняя граница всегда сегодня
$('acceptedAt').max = new Date().toISOString().slice(0, 10);

// Филиал сотрудника выбран заранее: приёмщик работает в одном месте,
// и переспрашивать при каждой машине незачем
(async () => {
  const el = $('branch');
  const rows = await (await fetch('/api/branches')).json();
  rows.forEach(b => el.add(new Option(b.label, b.id)));
  if (document.body.dataset.branchId) el.value = document.body.dataset.branchId;
})();

fill('brand', '/api/brands', r => r.name);
$('vin').focus();
