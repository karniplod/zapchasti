// Скрипт шаблона templates/admin/stock_new.html.

const $ = id => document.getElementById(id);
let category=null, condition=null, files=[], fits=[];
let oemSource=null;   // какую подсказку нажали; пусто — набрали руками
// Номер, подставленный самой системой: пока человек его не тронул,
// при смене детали или машины он убирается вместе с подсказкой
let oemAuto=null;
let oemSeq=0;         // ответ на старый запрос не должен лечь поверх нового

// Подсказка каталожного номера. Здесь машина известна не заранее,
// а из списка применимости: первое поколение в нём и есть та машина,
// с которой деталь снята
function clearOemHints(){
  $('oemHints').hidden = true; $('oemHints').innerHTML = '';
  if (oemAuto && $('oem').value === oemAuto) $('oem').value = '';
  oemAuto = null; oemSource = null;
}

async function loadOemHints(){
  clearOemHints();
  const seq = ++oemSeq;
  if (!category || !fits.length) return;
  try {
    const d = await (await fetch(
      `/api/oem/suggest?category_id=${category.id}&generation_id=${fits[0].id}`)).json();
    if (seq !== oemSeq || !d.candidates.length) return;

    $('oemHints').innerHTML = '<span class="lbl">Похоже на:</span>' +
      d.candidates.map(c => `
        <button type="button" data-code="${c.code}" data-src="${c.sources[0]}"
                class="${c.code === d.confident ? 'sure' : ''}"
                title="${c.notes.join('; ')}">
          ${c.code}<small>${c.notes[0]}</small></button>`).join('');

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
  } catch { /* без подсказки приём детали работает как раньше */ }
}

$('oem').addEventListener('input', () => { oemSource = null; oemAuto = null; });

// --- фото ---
$('shoot').onclick = () => $('photos').click();
$('photos').onchange = async e => {
  for (const f of e.target.files){
    const blob = await cropImage(f);
    if (blob) files.push(new File([blob], 'photo.jpg', {type:'image/jpeg'}));
  }
  e.target.value='';
  $('thumbs').innerHTML='';
  files.forEach(f=>{const i=document.createElement('img');i.src=URL.createObjectURL(f);
    i.onload=()=>URL.revokeObjectURL(i.src);$('thumbs').appendChild(i);});
  $('shoot').textContent = files.length ? `Добавить ещё фото (${files.length})` : '📷 Снять деталь';
  refresh();
};

// --- категория ---
let t;
$('catSearch').oninput = e => { clearTimeout(t);
  t=setTimeout(()=>searchCats(e.target.value.trim()),250); };
$('catSearch').onfocus = () => searchCats($('catSearch').value.trim());


// Путь до родителя: имя узла и так стоит в заголовке строки
function parentPath(path){
  const parts = (path || '').split(' / ');
  return parts.length > 1 ? parts.slice(0, -1).join(' / ') : '';
}

async function searchCats(q){
  const rows = await (await fetch('/api/part-categories'+(q?`?q=${encodeURIComponent(q)}`:''))).json();
  const box=$('catResults'); box.innerHTML='';
  rows.forEach(r=>{const b=document.createElement('button');b.type='button';
    b.innerHTML=`${r.name}<span class="path">${parentPath(r.path)}</span>`;
    b.onclick=()=>pick(r); box.appendChild(b);});
  box.classList.toggle('open', rows.length>0);
}
function pick(r){ category=r; $('catBox').hidden=true; $('catPicked').hidden=false;
  setTimeout(loadOemHints, 0);
  $('pickedName').textContent=r.name; $('pickedPath').textContent=parentPath(r.path);
  $('catResults').classList.remove('open'); refresh(); }
$('catClear').onclick=()=>{category=null;clearOemHints();$('catBox').hidden=false;$('catPicked').hidden=true;
  $('catSearch').value='';refresh();};

// --- состояние ---
$('cond').onclick = e => { const b=e.target.closest('button'); if(!b) return;
  condition=b.dataset.c;
  [...$('cond').children].forEach(x=>x.classList.toggle('on',x===b)); refresh(); };

// --- применимость ---
async function fill(sel,url,fmt){ const el=$(sel); el.innerHTML='<option value="">—</option>';
  el.disabled=true; if(!url) return;
  (await (await fetch(url)).json()).forEach(r=>el.add(new Option(fmt(r), r.id)));
  el.disabled=false; }

const fmtGen = g => `${g.name}${g.body_type?', '+g.body_type:''} (${g.year_from}–${g.year_to||'н.в.'})`;

$('brand').onchange = async e => {
  await fill('model', e.target.value?`/api/models?brand_id=${e.target.value}`:null, r=>r.name);
  await fill('generation', null, x=>x); checkFit(); };
$('model').onchange = async e => {
  await fill('generation', e.target.value?`/api/generations?model_id=${e.target.value}`:null, fmtGen);
  checkFit(); };
$('generation').onchange = checkFit;
function checkFit(){ $('addFit').disabled = !$('generation').value; }

$('addFit').onclick = () => {
  const id = +$('generation').value;
  if (fits.some(f=>f.id===id)) return toast('Эта модель уже в списке');
  const label = `${$('brand').selectedOptions[0].text} ${$('model').selectedOptions[0].text} `
              + `${$('generation').selectedOptions[0].text}`;
  fits.push({id, label}); drawFits(); refresh(); loadOemHints();
};

function drawFits(){
  $('fits').innerHTML='';
  fits.forEach((f,i)=>{const c=document.createElement('div');c.className='chip';
    c.innerHTML=`<span>${f.label}</span>`;
    const b=document.createElement('button');b.textContent='×';
    b.onclick=()=>{fits.splice(i,1);drawFits();refresh();};
    c.appendChild(b);$('fits').appendChild(c);});
  $('noFits').hidden = fits.length>0;
}

// --- машина, с которой снята деталь ---
// Выбрана машина — применимость и артикул берутся от неё, руками
// указывать нечего, поэтому весь блок применимости прячем
const fromDonor = () => !!$('donor').value;

function applyDonorMode(){
  const on = fromDonor();
  $('fitsPanel').hidden = on;
  $('donorHint').textContent = on
    ? 'Применимость и артикул возьмутся от машины — указывать вручную ничего не нужно.'
    : 'Не с нашей машины — применимость нужно будет указать вручную.';
  // Источник поступления при этом очевиден и меняться не должен
  $('source').value = on ? 'donor' : ($('source').value === 'donor' ? 'purchased' : $('source').value);
  $('source').disabled = on;
  refresh();
}
$('donor').onchange = applyDonorMode;

// --- сохранение ---
// Текста на кнопке мало: он говорит, чего не хватает, но не показывает
// где это на странице. Поэтому незаполненное обязательное поле ещё и
// обводится, с пометкой у заголовка
function markNeed(el, tagId, needed){
  el.classList.toggle('need', needed);
  $(tagId).hidden = !needed;
}

// Первое незаполненное поле — к нему прокручиваем по нажатию кнопки
function firstMissing(){
  if (!category)  return $('catSearch');
  if (!condition) return $('cond');
  if (!(fromDonor() || fits.length)) return $('donor');
  return null;
}

function refresh(){
  const fitsOk = fromDonor() || fits.length > 0;

  markNeed($('catBox'), 'needCat', !category);
  markNeed($('cond'), 'needCond', !condition);
  markNeed($('fitsPanel'), 'needFits', !fitsOk && !fromDonor());

  // Кнопку намеренно не гасим: серая кнопка не объясняет, чего ждёт.
  // Нажатие с незаполненным полем прокручивает к нему
  if (!category)        $('save').textContent='Выберите деталь';
  else if (!condition)  $('save').textContent='Укажите состояние';
  else if (!fitsOk)     $('save').textContent='Укажите машину или к чему подходит';
  else if (!files.length) $('save').textContent='Принять без фото (черновик)';
  else                  $('save').textContent='Принять запчасть';
}

$('save').onclick = async () => {
  const miss = firstMissing();
  if (miss){
    miss.scrollIntoView({behavior:'smooth', block:'center'});
    if (miss.focus) miss.focus();
    toast($('save').textContent);
    return;
  }

  $('save').disabled=true;
  const fd=new FormData();
  fd.append('category_id',category.id);
  if ($('oem').value && oemSource) fd.append('oem_source', oemSource);
  fd.append('name',category.name);
  fd.append('condition',condition);
  fd.append('source', fromDonor() ? 'donor' : $('source').value);
  if (fromDonor()) fd.append('donor_id', $('donor').value);
  else fd.append('generations', fits.map(f=>f.id).join(','));
  [['oem','oem_number'],['price','price'],['loc','location'],['note','condition_note']]
    .forEach(([id,key])=>{ if($(id).value) fd.append(key,$(id).value); });
  files.forEach(f=>fd.append('files',f));

  try {
    const r=await fetch('/api/stock/parts',{method:'POST',body:fd});
    const d=await r.json();
    if(!r.ok){ toast(d.detail||'Не удалось сохранить','err'); $('save').disabled=false; return; }
    toast(`${d.sku} — применимость: ${d.generations}`, 'ok');
    resetForm();
  } catch { toast('Нет связи','err'); $('save').disabled=false; }
};

function resetForm(){
  // Машину намеренно не сбрасываем: с одной машины детали снимают
  // подряд, переспрашивать каждый раз незачем
  category=null; condition=null; files=[]; fits=[];
  $('catBox').hidden=false; $('catPicked').hidden=true; $('catSearch').value='';
  ['price','oem','note'].forEach(id=>$(id).value='');
  clearOemHints();   // подсказки прошлой детали к следующей не относятся
  [...$('cond').children].forEach(x=>x.classList.remove('on'));
  $('thumbs').innerHTML=''; $('photos').value=''; $('shoot').textContent='📷 Снять деталь';
  drawFits(); refresh(); window.scrollTo({top:0,behavior:'smooth'});
}
$('reset').onclick=resetForm;

let tt;
function toast(m,k=''){ const el=$('toast'); el.textContent=m; el.className=`toast show ${k}`;
  clearTimeout(tt); tt=setTimeout(()=>el.className='toast',2600); }

document.addEventListener('click',e=>{ if(!e.target.closest('#catBox'))
  $('catResults').classList.remove('open'); });

const fmtDonor = d =>
  `${d.code} — ${d.brand} ${d.model}${d.year ? ', ' + d.year : ''}`;

(async () => {
  const el = $('donor');
  el.innerHTML = '<option value="">Деталь не с нашей машины</option>';
  const rows = await (await fetch('/api/stock/donors')).json();
  rows.forEach(d => el.add(new Option(fmtDonor(d), d.id)));
  applyDonorMode();
})();

fill('brand','/api/brands',r=>r.name);
drawFits(); refresh();
