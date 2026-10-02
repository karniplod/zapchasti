// Скрипт шаблона templates/admin/reports.html.
//
// Вкладки VIN, «Поиск по названию» и «Каталог» — журналы запросов:
// грузятся отсюда, листаются по страницам, фильтруются по периоду
// и результату. «Спрос на закупку» и «Номера деталей» отрисованы
// сервером — вкладка их только показывает.

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const num = n => Number(n || 0).toLocaleString('ru');
const when = iso => new Date(iso).toLocaleString('ru', {day: '2-digit',
  month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit'});
const COND = {A: 'отличное', B: 'рабочее', C: 'с дефектом', D: 'под восстановление'};

// Нашлось или нет — первым делом, цветом: ради этого журнал и смотрят
const res = n => n > 0
  ? `<span class="res ok">нашлось ${num(n)}</span>`
  : '<span class="res no">не нашлось</span>';
const who = r => r.customer ? esc(r.customer) : '<span class="muted">аноним</span>';

const price = r => r.price_min == null && r.price_max == null ? '—'
  : r.price_min != null && r.price_max != null ? `${num(r.price_min)}–${num(r.price_max)} ₽`
  : r.price_min != null ? `от ${num(r.price_min)} ₽` : `до ${num(r.price_max)} ₽`;

const stat = (label, v, cls = '') =>
  `<div class="stat ${cls}"><b>${num(v)}</b><span>${label}</span></div>`;

const JOURNALS = {
  vin: {
    title: 'Запросы по VIN',
    why: 'Каждый введённый VIN целиком: что по нему определилось и нашлись ли детали. '
       + '«Раз» — сколько всего раз вводили этот VIN: повтор значит, что человек ждёт деталь.',
    stats: s => stat('запросов', s.total) + stat('нашлось', s.found, 'ok')
      + stat('не нашлось', s.not_found, 'no') + stat('разных VIN', s.unique_vins),
    head: '<th>Когда</th><th>VIN</th><th>Что определилось</th><th>Результат</th>'
        + '<th class="num">Раз</th><th>Кто</th>',
    row: r => `<td class="nowrap muted">${when(r.created_at)}</td>
      <td class="vin">${esc(r.vin)}</td>
      <td>${r.car ? esc(r.car) : `<span class="muted">${esc(r.resolution_label)}</span>`}
        <div class="sub">${esc(r.wmi)}${r.manufacturer ? ' · ' + esc(r.manufacturer) : ''}
          ${r.car ? ' · ' + esc(r.resolution_label) : ''}</div></td>
      <td>${res(r.results_count)}</td>
      <td class="num">${r.times}</td>
      <td>${who(r)}</td>`,
  },
  searches: {
    title: 'Поиск по названию и номеру',
    why: 'Что вводили в строку поиска — в каталоге и в шапке сайта. Пишется, когда '
       + 'человек нажал «Искать» или Enter, а не на каждую букву.',
    stats: s => stat('поисков', s.total) + stat('нашлось', s.found, 'ok')
      + stat('не нашлось', s.not_found, 'no') + stat('разных запросов', s.unique_queries),
    top: d => d.top.length ? `<h4>Чаще всего искали</h4><table class="top">
        <tr><th>Запрос</th><th class="num">Раз</th><th class="num">Пусто</th>
            <th class="num">Лучший результат</th></tr>
        ${d.top.map(t => `<tr><td>${esc(t.query)}</td><td class="num">${t.times}</td>
          <td class="num ${t.empty ? 'no' : ''}">${t.empty}</td>
          <td class="num">${num(t.best)}</td></tr>`).join('')}</table>` : '',
    head: '<th>Когда</th><th>Запрос</th><th>Результат</th><th>Город</th><th>Кто</th>',
    row: r => `<td class="nowrap muted">${when(r.created_at)}</td>
      <td class="q">${esc(r.query)}</td>
      <td>${res(r.results_count)}</td>
      <td>${r.city ? esc(r.city) : '<span class="muted">все</span>'}</td>
      <td>${who(r)}</td>`,
  },
  browses: {
    title: 'Подбор через каталог',
    why: 'Какой узел открыли, какое состояние отметили, цена, машина и город — '
       + 'и что нашлось. Записывается, когда выбор устоялся, а не на каждый щелчок.',
    stats: s => stat('подборов', s.total) + stat('нашлось', s.found, 'ok')
      + stat('не нашлось', s.not_found, 'no') + stat('с выбранным узлом', s.with_node),
    top: d => (d.top.length ? `<h4>Какие узлы открывают</h4><table class="top">
        <tr><th>Узел</th><th class="num">Раз</th><th class="num">Пусто</th></tr>
        ${d.top.map(t => `<tr><td>${esc(t.node)}</td><td class="num">${t.times}</td>
          <td class="num ${t.empty ? 'no' : ''}">${t.empty}</td></tr>`).join('')}</table>` : '')
      + (d.conditions.length ? `<p class="conds">Состояние отмечали: ${d.conditions.map(c =>
          `<span class="chip">${COND[c.condition] || c.condition} — ${c.times}</span>`).join(' ')}</p>` : ''),
    head: '<th>Когда</th><th>Узел</th><th>Состояние</th><th>Цена</th><th>Машина</th>'
        + '<th>Город</th><th>Результат</th><th>Кто</th>',
    row: r => `<td class="nowrap muted">${when(r.created_at)}</td>
      <td>${r.category ? (r.node ? `<span class="muted">${esc(r.node)} /</span> ` : '')
          + esc(r.category) : '<span class="muted">не выбран</span>'}</td>
      <td>${r.conditions ? r.conditions.map(c => COND[c] || c).join(', ')
          : '<span class="muted">любое</span>'}</td>
      <td class="nowrap">${price(r)}</td>
      <td>${r.car ? esc(r.car) : '<span class="muted">—</span>'}</td>
      <td>${r.city ? esc(r.city) : '<span class="muted">все</span>'}</td>
      <td>${res(r.results_count)}</td>
      <td>${who(r)}</td>`,
  },
};

let tab = 'vin', run = 0;
const pager = makePager($('pager'), 'reports', () => load());

async function load(){
  const j = JOURNALS[tab], my = ++run;
  const p = new URLSearchParams({days: $('jDays').value, found: $('jFound').value});
  const r = await fetch(`/api/reports/${tab}?${p}&${pager.query()}`);
  if (my !== run) return;   // пока ждали, переключили вкладку или фильтр
  if (!r.ok){ $('jTable').innerHTML = '<tr><td class="blank">Не удалось загрузить</td></tr>'; return; }
  const d = await r.json();

  $('jTitle').textContent = j.title;
  $('jWhy').textContent = j.why;
  $('jStats').innerHTML = j.stats(d.summary);
  $('jTop').innerHTML = j.top ? j.top(d) : '';
  $('jTable').innerHTML = d.rows.length
    ? `<tr>${j.head}</tr>` + d.rows.map(x => `<tr>${j.row(x)}</tr>`).join('')
    : '<tr><td class="blank">За этот период записей нет</td></tr>';
  pager.show(d.total);
}

function show(t){
  tab = t;
  [...$('repTabs').querySelectorAll('button')].forEach(b =>
    b.classList.toggle('on', b.dataset.t === t));
  const journal = t in JOURNALS;
  $('journal').hidden = !journal;
  document.querySelectorAll('[data-panel]').forEach(s => s.hidden = s.dataset.panel !== t);
  // Вкладка — в адресе: обновили страницу или прислали ссылку — та же вкладка
  history.replaceState(null, '', '#' + t);
  if (journal){ pager.reset(); load(); }
}

$('repTabs').onclick = e => {
  const b = e.target.closest('button'); if (b) show(b.dataset.t);
};
$('jDays').onchange = $('jFound').onchange = () => { pager.reset(); load(); };

const start = location.hash.slice(1);
show(start in JOURNALS || ['demand', 'accuracy'].includes(start) ? start : 'vin');
