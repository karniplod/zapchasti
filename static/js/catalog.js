// Скрипт шаблона templates/catalog.html.

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
// Деталь внутри узла, выбранная в меню шапки: {id, name, cnt}
let pinnedCat = null;
const state = {generation_id:null, modification_id:null, category_id:null,
  condition:[], price_min:null, price_max:null, q:'', city:null, sort:'new', page:1,
  donor:null};   // код машины: детали с одной машины (/catalog?donor=D-0014)

// Адрес повторяет выборку: машину и узел. Обновили страницу или отправили
// ссылку — открылось то же самое
function syncUrl(){
  const p = new URLSearchParams();
  if (state.donor) p.set('donor', state.donor);
  if (state.category_id) p.set('category', state.category_id);
  history.replaceState(null, '', '/catalog' + (p.toString() ? '?' + p : ''));
}

// ── VIN ──────────────────────────────────────────────────
$('vin').addEventListener('input', e => {
  const v = e.target.value.toUpperCase().replace(/[^A-HJ-NPR-Z0-9]/g,'');
  e.target.value = v; e.target.classList.remove('bad');
  $('vinGo').disabled = v.length !== 17;
});
$('vin').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !$('vinGo').disabled) findByVin(); });
$('vinGo').onclick = findByVin;

async function findByVin(){
  $('vinGo').disabled = true; $('vinGo').textContent = 'Ищу…';
  try {
    const d = await (await fetch('/api/catalog/vin', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({vin: $('vin').value})})).json();
    showResolution(d);
  } finally { $('vinGo').disabled = false; $('vinGo').textContent = 'Подобрать'; }
}

// Предупреждение о комплектации висит всегда: подбор по VIN даёт
// поколение кузова, но не исполнение
const WARN = `<div class="notice notice-warn">
  Подбор по VIN определяет поколение кузова, но не точную комплектацию.
  Сверьте каталожный номер со своей деталью — это исключит возврат.</div>`;

function showResolution(d){
  const box = $('resolved');

  if (d.resolution === 'invalid'){
    $('vin').classList.add('bad');
    box.innerHTML = `<div class="panel"><h3>Не похоже на VIN</h3>
      <p class="hint">${d.errors.join('. ')}.
      Номер есть в ПТС и на табличке под капотом.</p></div>`;
    return;
  }

  if (d.resolution === 'exact'){
    box.innerHTML = `<div class="notice notice-accent">
      <h3>${d.brand} ${d.model} ${d.generation}${d.body_type ? ', '+d.body_type : ''}</h3>
      <span class="hint">${d.year_from}–${d.year_to || 'н.в.'}${d.year ? ' · ваш год: '+d.year : ''}
        · подходящих деталей: ${d.results_count}</span></div>${WARN}`;
    state.generation_id = d.generation_id;
    state.modification_id = d.modification_id;
    openCatalog(); return;
  }

  if (d.resolution === 'brand_year'){
    box.innerHTML = `<div class="panel">
      <h3>${d.manufacturer}${d.year ? ', '+d.year+' год' : ''}</h3>
      <p class="hint">Точную модель по этому VIN определить
        не удалось. Выберите свою:</p>
      <div class="cands">${d.candidates.map(c => `
        <button data-gen="${c.generation_id}"><b>${c.brand} ${c.model}</b>
          <small>${c.generation}${c.body_type ? ', '+c.body_type : ''} ·
            ${c.year_from}–${c.year_to || 'н.в.'} · ${c.parts_count} дет.</small>
        </button>`).join('')}</div></div>${WARN}`;
    box.querySelectorAll('.cands button').forEach(b => b.onclick = () => {
      state.generation_id = +b.dataset.gen; state.modification_id = null;
      box.querySelector('.cands').remove();
      box.querySelector('h3').textContent = b.querySelector('b').textContent;
      openCatalog();
    });
    return;
  }

  box.innerHTML = `<div class="panel"><h3>Такую машину мы пока не разбирали</h3>
    <p class="hint">${d.country ? d.country+', ' : ''}${d.year ? d.year+' год' : ''} —
      модель определить не смогли. Запрос записан: появится такая машина на разборе,
      детали будут в каталоге. Пока посмотрите общий каталог.</p></div>`;
  openCatalog();
}

// Выбор машины из справочника — запасной путь для тех, у кого нет VIN
$('byModel').onclick = async () => {
  const box = $('picker');
  box.hidden = !box.hidden;
  $('byModel').setAttribute('aria-expanded', String(!box.hidden));
  $('byModel').textContent = box.hidden
    ? 'Выбрать по марке и модели' : 'Свернуть';
  if (box.hidden) return;
  if ($('pBrand').options.length) return;

  const brands = await (await fetch('/api/catalog/cars')).json();
  if (!brands.length){
    box.innerHTML = '<p class="hint">Пока нет ни одной машины с деталями в наличии.</p>';
    return;
  }
  $('pBrand').innerHTML = '<option value="">Выберите марку</option>';
  brands.forEach(b => $('pBrand').add(new Option(`${b.name} · ${b.parts}`, b.id)));
};

$('pBrand').onchange = async e => {
  reset$('pModel'); reset$('pGen');
  if (!e.target.value) return checkGo();
  const rows = await (await fetch(`/api/catalog/cars/models?brand_id=${e.target.value}`)).json();
  rows.forEach(r => $('pModel').add(new Option(`${r.name} · ${r.parts}`, r.id)));
  $('pModel').disabled = false;
  checkGo();
};

$('pModel').onchange = async e => {
  reset$('pGen');
  if (!e.target.value) return checkGo();
  const rows = await (await fetch(`/api/catalog/cars/generations?model_id=${e.target.value}`)).json();
  rows.forEach(r => $('pGen').add(new Option(
    `${r.name}${r.body_type ? ', '+r.body_type : ''} (${r.year_from}–${r.year_to || 'н.в.'}) · ${r.parts}`,
    r.id)));
  $('pGen').disabled = false;
  // Поколение одно — выбираем сразу, не заставляя тыкать
  if (rows.length === 1) $('pGen').selectedIndex = 1;
  checkGo();
};

$('pGen').onchange = checkGo;

// Сброс подбора: показываем весь склад целиком
$('pReset').onclick = () => {
  state.generation_id = null;
  state.modification_id = null;
  $('pBrand').selectedIndex = 0;
  reset$('pModel'); reset$('pGen');
  checkGo();
  $('resolved').innerHTML = '';
  $('vin').value = '';
  $('vinGo').disabled = true;
  openCatalog();
};
function checkGo(){ $('pGo').disabled = !$('pGen').value; }

function reset$(id){
  const el = $(id);
  el.innerHTML = '<option value="">—</option>';
  el.disabled = true;
}

$('pGo').onclick = () => {
  state.generation_id = +$('pGen').value;
  state.modification_id = null;
  const car = `${$('pBrand').selectedOptions[0].text.split(' · ')[0]} `
            + `${$('pModel').selectedOptions[0].text.split(' · ')[0]}`;
  $('resolved').innerHTML = `<div class="notice notice-accent">
    <h3>${car}</h3>
    <span class="hint">Показаны детали для выбранной машины</span></div>`;
  $('picker').hidden = true;
  openCatalog();
};

function openCatalog(){
  $('side').hidden = false;
  state.page = 1; state.category_id = null; state.condition = [];
  // Подпись тоже: иначе над выдачей новой машины висело бы имя прежней
  if (state.donor){ state.donor = null; syncUrl(); $('donorNote').innerHTML = ''; }
  // Ждём город: иначе при быстром выборе машины первая выдача уйдёт
  // без фильтра и тут же перерисуется — заметное мигание
  loadFacets(); loadParts();
  document.querySelector('.layout').scrollIntoView({behavior:'smooth', block:'start'});
}

// ── Фильтры ──────────────────────────────────────────────
async function loadFacets(){
  const p = new URLSearchParams();
  if (state.generation_id) p.set('generation_id', state.generation_id);
  if (state.modification_id) p.set('modification_id', state.modification_id);
  if (state.donor) p.set('donor', state.donor);
  const f = await (await fetch('/api/catalog/facets?'+p)).json();
  if (state.donor) showDonor(f.donor);

  // Деталь внутри узла (пришли из меню «Все категории» с ?category=)
  // в списке узлов не значится — ставим её первой строкой, иначе не видно,
  // почему выдача сужена
  const pinned = pinnedCat && pinnedCat.id === state.category_id
      && !f.categories.some(c => c.id === pinnedCat.id)
    ? `<label><input type="radio" name="cat" value="${pinnedCat.id}">${esc(pinnedCat.name)}
         <span class="n">${pinnedCat.cnt}</span></label>` : '';
  $('cats').innerHTML = pinned + f.categories.map(c =>
    `<label><input type="radio" name="cat" value="${c.id}">${c.name}
      <span class="n">${c.cnt}</span></label>`).join('')
    || '<p class="hint">Нет деталей</p>';
  $('cats').querySelectorAll('input').forEach(i => i.onchange = () => {
    state.category_id = +i.value; state.page = 1; loadParts();
    // Адрес следит за выбором: обновили страницу — фильтр тот же
    syncUrl();
  });
  // Выбор переживает перерисовку счётчиков — например, после VIN
  const on = state.category_id &&
    $('cats').querySelector(`input[value="${state.category_id}"]`);
  if (on) on.checked = true;

  $('conds').innerHTML = f.conditions.map(c =>
    `<label><input type="checkbox" value="${c.condition}">${c.condition} — ${c.label}
      <span class="n">${c.cnt}</span></label>`).join('');
  $('conds').querySelectorAll('input').forEach(i => i.onchange = () => {
    state.condition = [...$('conds').querySelectorAll('input:checked')].map(x => x.value);
    state.page = 1; loadParts(); });

  if (f.price.min != null){
    $('pmin').placeholder = 'от '+f.price.min;
    $('pmax').placeholder = 'до '+f.price.max;
  }
}

let debounce;
['pmin','pmax'].forEach(id => $(id).addEventListener('input', () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    state.price_min = $('pmin').value || null;
    state.price_max = $('pmax').value || null;
    state.page = 1; loadParts(); }, 450);
}));
// Набор текста только подсказывает: выдача обновляется, история — нет.
// Иначе от слова «дверь» в ней оседало бы «д», «дв», «две», «двер»
$('q').addEventListener('input', () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => { state.q = $('q').value.trim(); state.page = 1; loadParts(); }, 350);
});

// ── Что считается поиском ────────────────────────────────────────
// Три намерения: нажал Enter, нажал «Искать», выбрал деталь из того,
// что предложил поиск. Всё остальное — ещё не поиск, а печатание
let lastLogged = '';

function rememberSearch(keepalive = false){
  const q = $('q').value.trim();
  // Повтор подряд не пишем: сервер тоже схлопывает такое за 10 минут,
  // но незачем гонять запрос
  if (q.length < 2 || q === lastLogged) return;
  lastLogged = q;
  fetch('/api/catalog/searches', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: q, results_count: lastTotal}),
    // keepalive — когда уходим со страницы по клику на деталь:
    // обычный fetch браузер оборвёт вместе с выгрузкой страницы
    keepalive,
  }).catch(() => {});
}

$('searchForm').onsubmit = e => {
  e.preventDefault();
  clearTimeout(debounce);
  state.q = $('q').value.trim();
  state.page = 1;
  // Сначала выдача, потом запись: в историю идёт то, что человек увидел
  loadParts().then(() => rememberSearch());
};

// Выбор детали из предложенного — тоже поиск, и самый ценный:
// он говорит, что по этому запросу нашлось нужное
$('grid').addEventListener('click', e => {
  if (e.target.closest('.tag-card')) rememberSearch(true);
});
$('sort').onchange = () => { state.sort = $('sort').value; state.page = 1; loadParts(); };
$('reset').onclick = () => {
  // Город намеренно не сбрасываем: это не фильтр подбора, а место,
  // куда человек готов приехать — и радиокнопки остались бы отмеченными
  Object.assign(state, {category_id:null, condition:[], price_min:null,
    price_max:null, q:'', page:1});
  $('q').value = ''; $('pmin').value = ''; $('pmax').value = '';
  syncUrl();
  loadFacets(); loadParts();
};

// ── Выдача ───────────────────────────────────────────────
let partsRun = 0;
let lastTotal = 0;   // сколько нашлось — уходит в историю поиска

async function loadParts(){
  const run = ++partsRun;
  const p = new URLSearchParams();
  Object.entries(state).forEach(([k,v]) => {
    if (v === null || v === '' || (Array.isArray(v) && !v.length)) return;
    p.set(k, Array.isArray(v) ? v.join(',') : v);
  });

  const d = await (await fetch('/api/catalog/parts?'+p)).json();
  if (run !== partsRun) return;   // пока ждали, фильтры успели поменяться

  lastTotal = d.total;
  $('count').textContent = d.total
    ? `${d.total} ${plural(d.total,'деталь','детали','деталей')}` : '';

  if (!d.items.length){
    $('grid').innerHTML = `<div class="empty">
      <h3>Ничего не нашлось</h3>
      <p>Уберите часть фильтров или напишите нам — деталь может лежать
         на складе, но ещё не быть выложенной.</p>
      ${state.city ? `<p>Сейчас показан только
        <b>${state.city}</b>. <button class="linkish" id="allCities">Искать
        во всех городах</button></p>` : ''}</div>`;
    const all = $('allCities');
    // Город меняем через шапку, чтобы подпись там не разошлась с выдачей
    if (all) all.onclick = () => {
      document.cookie = 'city=;path=/;max-age=31536000';
      const nm = document.getElementById('cityName');
      if (nm) nm.textContent = 'все города';
      window.onCityChange('');
    };
    $('pager').innerHTML = ''; return;
  }

  $('grid').innerHTML = d.items.map(card).join('');
  renderPager(d);
}

// Одна карточка — одна разметка на весь каталог
function card(i){
  {
    // Откуда деталь: с машины или поступила отдельно
    const fit = i.brand ? `${i.brand} ${i.model}${i.year ? ', '+i.year : ''}`
      : i.fits_first ? `${i.fits_first}${i.fits_count > 1 ? ' и ещё '+(i.fits_count-1) : ''}`
      : 'применимость уточняется';
    return `<a class="tag-card" href="/p/${i.sku}">
      <div class="shot">
        ${i.photo ? `<img src="${i.photo}" alt="${i.name}" loading="lazy">`
                  : '<div class="none">фото готовится</div>'}
        <span class="grade" data-g="${i.condition}">${i.condition}</span>
      </div>
      <div class="body">
        <span class="sku">${i.sku}</span>
        <span class="name">${i.name}</span>
        <span class="fit">${fit}${!state.city && i.city ? ' · '+i.city : ''}</span>
        <span class="price">${i.price ? Number(i.price).toLocaleString('ru')+' ₽'
                                      : 'по запросу'}<small>${i.node || ''}</small></span>
      </div></a>`;
  }
}

function renderPager(d){
  if (d.pages <= 1){ $('pager').innerHTML = ''; return; }
  let h = `<button ${d.page===1?'disabled':''} data-p="${d.page-1}">Назад</button>`;
  for (let i = 1; i <= d.pages; i++){
    if (i===1 || i===d.pages || Math.abs(i-d.page) <= 1)
      h += `<button class="${i===d.page?'on':''}" data-p="${i}">${i}</button>`;
    else if (Math.abs(i-d.page) === 2) h += '<span class="gap">…</span>';
  }
  h += `<button ${d.page===d.pages?'disabled':''} data-p="${d.page+1}">Вперёд</button>`;
  $('pager').innerHTML = h;
  $('pager').querySelectorAll('button[data-p]').forEach(b => b.onclick = () => {
    state.page = +b.dataset.p; loadParts();
    window.scrollTo({top: document.querySelector('.layout').offsetTop - 20, behavior:'smooth'});
  });
}

const plural = (n,one,few,many) => {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
};

// Свежие поступления: человек видит товар сразу, а не пустой экран
let freshRun = 0;

async function loadFresh(){
  const run = ++freshRun;

  const ask = async city => {
    const p = new URLSearchParams({sort:'new', page:1});
    if (city) p.set('city', city);
    return (await fetch('/api/catalog/parts?'+p)).json();
  };

  try {
    let d = await ask(state.city);
    let wider = false;

    // В выбранном городе может не быть вообще ничего — например,
    // филиал только открыли. Прятать блок нельзя: человек решит, что
    // магазин пустой. Показываем весь склад и объясняем, почему
    if (!d.items.length && state.city){
      d = await ask(null);
      wider = true;
    }
    if (run !== freshRun) return;

    // Выбрана машина (VIN, марка, машина с главной) или узел из меню —
    // свежие со всего склада только заслонили бы результат. Проверяем
    // здесь, после ответа: загрузка стартует раньше, чем каталог разберёт
    // адрес, и раньше, чем ответит подбор по VIN
    if (state.donor || state.category_id || state.generation_id){
      $('fresh').hidden = true;
      return;
    }
    $('fresh').hidden = !d.items.length;
    if (!d.items.length) return;

    $('freshNote').hidden = !wider;
    if (wider){
      $('freshNote').textContent =
        `В городе ${state.city} пока ничего не выложено — показываем склад целиком.`;
    }
    $('freshGrid').innerHTML = d.items.slice(0, 3).map(card).join('');
  } catch { /* каталог ниже всё равно загрузится */ }
}

// При подборе по машине свежие убираем: они мешают увидеть результат
const _openCatalog = openCatalog;
openCatalog = function(){ $('fresh').hidden = true; _openCatalog(); };

// ── Город ───────────────────────────────────────────────
// Выбирается в шапке (templates/_city.html), сюда приходит готовым.
// Каталог только читает куку и слушает смену
const cityCookie = () => {
  const m = document.cookie.match(/(?:^|; )city=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : null;
};
state.city = cityCookie();

window.onCityChange = city => {
  state.city = city || null;
  state.page = 1;
  loadFresh();
  if (!$('side').hidden) loadParts();
};

loadFresh();

// Каталог открыт сразу, без выбора машины.
// Раньше страница каталога до подбора не показывала ничего: фильтры
// спрятаны, выдача пуста, и всё содержимое — три плитки «свежего».
// Стоило им не найтись, и каталог выглядел пустым сайтом
// Открыть каталог сразу на узле или детали. Название детали берём из того
// же источника, что и меню: в списке узлов её нет, а подписать фильтр надо
async function openCategory(id){
  state.category_id = id;
  try {
    const d = await (await fetch('/api/catalog/nodes')).json();
    for (const n of d.nodes){
      const leaf = n.items.find(i => i.id === id);
      if (leaf){ pinnedCat = leaf; break; }
    }
  } catch { /* без подписи фильтр всё равно работает */ }
  showEverything();
  document.querySelector('.layout').scrollIntoView({block: 'start'});
}

// Чьи детали показаны. Без подписи выдача из пяти деталей выглядит
// как пустой склад, а не как «всё, что сняли с этой машины»
function showDonor(car){
  const box = $('donorNote');
  if (!car){
    // Машины нет или она уже не на витрине — показываем весь склад
    state.donor = null; syncUrl(); loadFacets(); loadParts();
    box.innerHTML = '<div class="notice notice-warn">Этой машины нет на витрине — ' +
                    'показан весь каталог.</div>';
    return;
  }
  box.innerHTML = `<div class="notice notice-accent donor-note">
    <div><h3>Детали с машины ${esc(car.brand)} ${esc(car.model)}</h3>
    <span class="hint">${esc(car.generation)}${car.body_type ? ', ' + esc(car.body_type) : ''}` +
    `${car.year ? ' · ' + car.year + ' год' : ''} · ${esc(car.code)}. ` +
    `Возьмёте комплектом — отправим одной посылкой.</span></div>
    <button class="btn btn-ghost btn-sm" id="donorOff">Детали со всех машин</button></div>`;
  $('donorOff').onclick = () => {
    state.donor = null; state.page = 1; box.innerHTML = '';
    syncUrl(); loadFacets(); loadParts();
  };
}

function showEverything(){
  $('side').hidden = false;
  state.page = 1;
  loadFacets();
  loadParts();
}

// VIN может прийти с главной — тогда сразу подбираем под машину
const fromHome = new URLSearchParams(location.search).get('vin');
const fromSearch = new URLSearchParams(location.search).get('q');

// Узел или деталь из меню «Все категории»: /catalog?category=677
const fromMenu = +new URLSearchParams(location.search).get('category') || null;
// Машина с главной или из карточки детали: /catalog?donor=D-0014
const fromDonor = new URLSearchParams(location.search).get('donor');

if (fromHome){
  $('vin').value = fromHome;
  $('vinGo').disabled = fromHome.length !== 17;
  findByVin();
} else if (fromDonor){
  state.donor = fromDonor.trim().toUpperCase();
  if (fromMenu) state.category_id = fromMenu;
  showEverything();
  document.querySelector('.layout').scrollIntoView({block: 'start'});
} else if (fromMenu){
  openCategory(fromMenu);
} else {
  showEverything();
  // Запрос из истории поиска в кабинете: /catalog?q=дверь — его не пишем,
  // он там уже есть. Поиск из шапки (from=head) — новое намерение, как
  // Enter в поле каталога: пишем после выдачи, а метку убираем из адреса,
  // чтобы обновление страницы не записало его второй раз
  if (fromSearch){
    $('q').value = fromSearch;
    state.q = fromSearch;
    const fromHead = new URLSearchParams(location.search).get('from') === 'head';
    if (fromHead){
      history.replaceState(null, '', '/catalog?q=' + encodeURIComponent(fromSearch));
      loadParts().then(() => rememberSearch());
    } else {
      loadParts();
    }
  } else {
    $('vin').focus();
  }
}
