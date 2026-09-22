// Скрипт шаблона templates/admin/dismantle.html.

const DONOR_ID = +document.body.dataset.donorId;
const $ = id => document.getElementById(id);

let category = null, condition = null, files = [], lastLoc = '';
let origin = 'original';   // снятая с машины деталь чаще всего оригинал

// --- подсказка каталожного номера ------------------------------------
// Ядро подсказки живёт на сервере (app/services/oem.py): оно опрашивает
// источники и решает, можно ли подставить номер само. Здесь только показ
let oemSource = null;   // какую подсказку нажали; пусто — набрали руками
// Номер, который подставили сами. Пока человек его не тронул, он наш:
// сменили деталь — убираем, иначе номер двери уедет вместе с фарой
let oemAuto = null;
let oemSeq = 0;         // ответ на старый запрос не должен лечь поверх нового

function clearOemHints(){
  $('oemHints').hidden = true;
  $('oemHints').innerHTML = '';
  $('oemWarn').hidden = true;
  if (oemAuto && $('oem').value === oemAuto) $('oem').value = '';
  oemAuto = null;
  oemSource = null;
}

async function loadOemHints(categoryId){
  clearOemHints();
  const seq = ++oemSeq;
  try {
    const d = await (await fetch(
      `/api/oem/suggest?category_id=${categoryId}&donor_id=${DONOR_ID}`)).json();
    if (seq !== oemSeq || !d.candidates.length) return;

    $('oemHints').innerHTML = '<span class="lbl">Было раньше:</span>' +
      d.candidates.map(c => `
        <button type="button" data-code="${c.code}" data-src="${c.sources[0]}"
                class="${c.code === d.confident ? 'sure' : ''}"
                title="${c.notes.join('; ')}">
          ${c.code}<small>${c.sources.length > 1 ? c.sources.length + ' источника' : c.notes[0]}</small>
        </button>`).join('');

    $('oemHints').querySelectorAll('button').forEach(b => b.onclick = () => {
      $('oem').value = b.dataset.code;
      oemSource = b.dataset.src;
      oemAuto = null;   // выбрал человек — теперь это его номер
      $('oemHints').querySelectorAll('button').forEach(x => x.classList.remove('sure'));
      b.classList.add('sure');
    });

    // Уверенный номер (сошлись два независимых источника) ставим сами,
    // но только в пустое поле: набранное человеком не перезаписываем
    const sure = d.candidates.find(c => c.code === d.confident);
    if (sure && !$('oem').value.trim()){
      $('oem').value = sure.code;
      oemAuto = sure.code;
      oemSource = sure.sources[0];
      $('oemHints').insertAdjacentHTML('beforeend',
        '<span class="lbl">подставлен сам — проверьте</span>');
    }
    $('oemHints').hidden = false;
  } catch { /* без подсказки приёмка работает как раньше */ }
}

// Набрал руками — источник больше не подсказка
$('oem').addEventListener('input', () => { oemSource = null; oemAuto = null; });

// --- поиск категории -------------------------------------------------
let searchTimer;
$('catSearch').addEventListener('input', e => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(() => searchCats(q), 200);
});
$('catSearch').addEventListener('focus', () => searchCats($('catSearch').value.trim()));


// Путь до родителя: имя узла и так в заголовке строки
function parentPath(path){
  const parts = (path || '').split(' / ');
  return parts.length > 1 ? parts.slice(0, -1).join(' / ') : '';
}

async function searchCats(q){
  const rows = await (await fetch('/api/part-categories' + (q ? `?q=${encodeURIComponent(q)}` : ''))).json();
  const box = $('catResults');
  box.innerHTML = '';
  rows.forEach(r => {
    const b = document.createElement('button');
    b.type = 'button';
    b.innerHTML = `${r.name}<span class="path">${parentPath(r.path)}</span>`;
    b.onclick = () => pick(r);
    box.appendChild(b);
  });
  box.classList.toggle('open', rows.length > 0);
}

function pick(r){
  category = r;
  loadOemHints(r.id);
  $('catBox').hidden = true;
  $('catPicked').hidden = false;
  $('pickedName').textContent = r.name;
  $('pickedPath').textContent = parentPath(r.path);
  if (!$('name').value) $('name').value = r.name;
  $('catResults').classList.remove('open');
  refresh();
}

$('catClear').onclick = () => {
  category = null;
  clearOemHints();
  $('catBox').hidden = false;
  $('catPicked').hidden = true;
  $('catSearch').value = '';
  $('catSearch').focus();
  refresh();
};

// --- состояние -------------------------------------------------------
// Бренд нужен только там, где номер не автозаводской
$('origin').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  origin = b.dataset.o;
  [...$('origin').children].forEach(x => x.classList.toggle('on', x === b));
  $('brandBox').hidden = origin === 'original';
  if (origin === 'original') $('partBrand').value = '';
});

$('cond').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  condition = b.dataset.c;
  [...$('cond').children].forEach(x => x.classList.toggle('on', x === b));
  refresh();
});

// --- фото ------------------------------------------------------------
$('shoot').onclick = () => $('photos').click();
$('photos').addEventListener('change', async e => {
  // Кадрируем по одному: разборщик сам решает, что войдёт в карточку
  for (const f of e.target.files){
    const blob = await cropImage(f);
    if (blob) files.push(new File([blob], 'photo.jpg', {type:'image/jpeg'}));
  }
  e.target.value = '';
  drawThumbs();
  refresh();
});

function drawThumbs(){
  const box = $('thumbs');
  box.innerHTML = '';
  files.forEach((f, i) => {
    const fig = document.createElement('figure');
    const img = document.createElement('img');
    img.src = URL.createObjectURL(f);
    img.onload = () => URL.revokeObjectURL(img.src);
    const del = document.createElement('button');
    del.textContent = '×';
    del.setAttribute('aria-label', 'Убрать фото');
    del.onclick = () => { files.splice(i, 1); drawThumbs(); refresh(); };
    fig.append(img, del);
    box.appendChild(fig);
  });
  $('shoot').textContent = files.length ? `Добавить ещё фото (${files.length})` : 'Снять деталь';
  $('shoot').classList.toggle('empty-warn', files.length === 0);
}

// --- сохранение ------------------------------------------------------
function refresh(){
  $('save').disabled = !(category && condition);
  if (!category)        $('save').textContent = 'Выберите деталь';
  else if (!condition)  $('save').textContent = 'Укажите состояние';
  else if (!files.length) $('save').textContent = 'Добавить без фото (черновик)';
  else                  $('save').textContent = 'Добавить деталь';
}
$('name').addEventListener('input', refresh);

$('save').onclick = async () => {
  $('save').disabled = true;
  const fd = new FormData();
  fd.append('donor_id', DONOR_ID);
  fd.append('category_id', category.id);
  fd.append('name', $('name').value.trim() || category.name);
  fd.append('condition', condition);
  ['oem','price','loc','weight'].forEach(id => {
    const map = {oem:'oem_number', price:'price', loc:'location', weight:'weight_kg'};
    if ($(id).value) fd.append(map[id], $(id).value);
  });
  if ($('oem').value && oemSource) fd.append('oem_source', oemSource);
  fd.append('origin', origin);
  if ($('partBrand').value.trim()) fd.append('part_brand', $('partBrand').value.trim());
  if ($('note').value) fd.append('condition_note', $('note').value);
  files.forEach(f => fd.append('files', f));

  try {
    const r = await fetch('/api/parts', {method:'POST', body: fd});
    if (!r.ok){
      const e = await r.json().catch(() => ({}));
      toast(e.detail || 'Сохранить не удалось', 'err');
      $('save').disabled = false;
      return;
    }
    const p = await r.json();
    let msg = p.sku;
    if (p.status === 'draft') msg += ' — черновик, нужно фото';
    else if (p.applicability_rows) msg += ` — применимость: ${p.applicability_rows} модиф.`;
    toast(msg, p.status === 'draft' ? '' : 'ok');
    clearForm();
    loadParts();
  } catch {
    toast('Нет связи. Не закрывайте страницу', 'err');
    $('save').disabled = false;
  }
};

function clearForm(){
  // Место хранения и состояние не сбрасываем: подряд снимают
  // однотипные детали и кладут на ту же полку
  lastLoc = $('loc').value;
  ['name','oem','price','note','weight','partBrand'].forEach(id => $(id).value = '');
  clearOemHints();   // подсказки прошлой детали к следующей не относятся
  origin = 'original';
  [...$('origin').children].forEach((x, i) => x.classList.toggle('on', i === 0));
  $('brandBox').hidden = true;
  category = null;
  $('catBox').hidden = false; $('catPicked').hidden = true;
  $('catSearch').value = '';
  files = []; drawThumbs();
  $('loc').value = lastLoc;
  refresh();
  $('catSearch').focus();
  window.scrollTo({top:0, behavior:'smooth'});
}

// --- список ----------------------------------------------------------
async function loadParts(){
  const rows = await (await fetch(`/api/donors/${DONOR_ID}/parts`)).json();
  $('counter').textContent = `${rows.length} дет.`;
  const list = $('list');
  if (!rows.length){
    list.innerHTML = '<p class="empty-note">Пока пусто. Первая снятая деталь появится здесь.</p>';
    return;
  }
  list.innerHTML = '';
  rows.forEach(p => {
    const el = document.createElement('div');
    el.className = 'part';
    el.innerHTML = `
      ${p.photo ? `<img src="${p.photo}" alt="">` : '<div class="noimg">нет фото</div>'}
      <div class="info">
        <div class="nm">${p.name}
          ${p.status === 'draft' ? '<span class="tag draft">черновик</span>' : ''}
          <span class="tag">${p.condition}</span>
        </div>
        <div class="sub">${p.sku}${p.location ? ' · ' + p.location : ''}</div>
      </div>
      <div class="pr">${p.price ? Number(p.price).toLocaleString('ru') + ' ₽' : '—'}</div>
      <!-- Правка живёт на странице деталей: там весь редактор целиком,
           тащить его в рабочее место разборщика незачем -->
      <a class="edit-link" href="/parts?q=${encodeURIComponent(p.sku)}"
         title="Изменить" aria-label="Изменить деталь">
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M13.4 2.9l3.7 3.7L7.3 16.4l-4.4.7.7-4.4z" fill="none"
                stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>
        </svg>
      </a>
      <button class="del" aria-label="Удалить деталь">×</button>`;
    el.querySelector('.del').onclick = async () => {
      if (!confirm(`Удалить ${p.sku}?`)) return;
      const r = await fetch(`/api/parts/${p.id}`, {method:'DELETE'});
      if (r.ok) loadParts();
      else toast((await r.json()).detail, 'err');
    };
    list.appendChild(el);
  });
}

// --- нижние кнопки ---------------------------------------------------
$('labels').onclick = () => window.open(`/donors/${DONOR_ID}/labels`, '_blank');

$('finish').onclick = async () => {
  if (!confirm('Закрыть разбор? Машина уйдёт в статус «разобрана».')) return;
  const r = await fetch(`/api/donors/${DONOR_ID}/finish`, {method:'POST'});
  const d = await r.json();
  if (!r.ok) toast(d.detail, 'err');
  else { toast('Разбор закрыт', 'ok'); setTimeout(() => location.href = '/admin', 900); }
};

// Вернуть в разбор: страница перезагружается уже с формой
$('reopen').onclick = async () => {
  if (!confirm('Вернуть машину в разбор? Она снова станет «в разборе», '
             + 'и её нужно будет закрыть заново.')) return;
  const r = await fetch(`/api/donors/${DONOR_ID}/reopen`, {method:'POST'});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) toast(d.detail || 'Не удалось', 'err');
  else location.reload();
};

// --- прочее ----------------------------------------------------------
let toastTimer;
function toast(msg, kind = ''){
  const t = $('toast');
  t.textContent = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.className = 'toast', 2600);
}

document.addEventListener('click', e => {
  if (!e.target.closest('#catBox')) $('catResults').classList.remove('open');
});

$('moreBtn').onclick = () => {
  const m = $('more');
  m.hidden = !m.hidden;
  $('moreBtn').textContent = m.hidden ? 'Ещё поля' : 'Скрыть';
};

drawThumbs();
loadParts();
