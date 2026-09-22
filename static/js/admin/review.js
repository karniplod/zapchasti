// Скрипт шаблона templates/admin/review.html.

const $ = id => document.getElementById(id);

async function load(){
  const rows = await (await fetch('/api/reference/review')).json();
  if (!rows.length){
    $('list').innerHTML = '<p class="blank">Всё проверено — заведённого вручную нет</p>';
    return;
  }

  // Блок .merge — объединение: машины и модификации переезжают
  // в правильное поколение, дубль удаляется. Выбор ограничен той же
  // моделью — слить Гранту в Камри нельзя
  $('list').innerHTML = rows.map(g => `
    <div class="gen" data-id="${g.id}">
      <div class="h">
        <span class="nm">${g.brand} ${g.model}</span>
        <span class="src">${g.source === 'manual' ? 'завели руками' : g.source}</span>
        <span class="cnt">машин: ${g.donors}</span>
      </div>
      <div class="meta">${g.generation}${g.body_type ? ', ' + g.body_type : ''}
        ${g.year_from ? ' · ' + g.year_from + '—' + (g.year_to || 'н.в.') : ''}</div>
      <div class="actions-row">
        <button class="btn btn-accent ok">Всё верно</button>
        <button class="btn dup">Это дубль</button>
        <a class="btn" href="/donors">Посмотреть машины</a>
      </div>

      <div class="merge" hidden>
        <label>Слить в поколение той же модели</label>
        <div class="row">
          <select class="f-into"></select>
          <button class="btn btn-accent go">Объединить</button>
        </div>
      </div>
    </div>`).join('');

  document.querySelectorAll('.gen').forEach(el => {
    const g = rows.find(x => x.id === +el.dataset.id);

    el.querySelector('.ok').onclick = async () => {
      const b = el.querySelector('.ok');
      b.disabled = true;
      const r = await fetch(`/api/reference/generations/${el.dataset.id}/approve`,
                            {method: 'POST'});
      if (r.ok){ toast('Проверено', 'ok'); load(); }
      else { toast('Не удалось сохранить', 'err'); b.disabled = false; }
    };

    el.querySelector('.dup').onclick = () => openMerge(el, g);
    el.querySelector('.go').onclick = () => doMerge(el);
  });
}

// Список поколений той же модели, кроме самого дубля: сливать можно
// только внутри модели, иначе применимость деталей разъедется
async function openMerge(el, g){
  const box = el.querySelector('.merge');
  if (!box.hidden){ box.hidden = true; return; }

  const sel = el.querySelector('.f-into');
  sel.innerHTML = '<option value="">Загружаю…</option>';
  box.hidden = false;

  // Именно /api/generations: витринная ручка отдаёт только поколения
  // с деталями в наличии, а сливать нужно любое
  const list = await (await fetch(`/api/generations?model_id=${g.model_id}`)).json();
  const others = list.filter(x => x.id !== g.id);

  if (!others.length){
    sel.innerHTML = '<option value="">У этой модели других поколений нет</option>';
    el.querySelector('.go').disabled = true;
    return;
  }
  sel.innerHTML = others.map(x =>
    `<option value="${x.id}">${x.name}${x.body_type ? ', ' + x.body_type : ''}` +
    `${x.year_from ? ' · ' + x.year_from + '—' + (x.year_to || 'н.в.') : ''}</option>`).join('');
  el.querySelector('.go').disabled = false;
}

async function doMerge(el){
  const into = el.querySelector('.f-into').value;
  if (!into) return;
  if (!confirm('Машины и модификации переедут в выбранное поколение, '
             + 'а это будет удалено. Отменить будет нельзя.')) return;

  const b = el.querySelector('.go');
  b.disabled = true;
  try {
    const r = await fetch(
      `/api/reference/generations/${el.dataset.id}/merge?into_id=${into}`,
      {method: 'POST'});
    const d = await r.json().catch(() => ({}));
    if (r.ok){
      toast(d.donors_moved ? `Объединено, машин перенесено: ${d.donors_moved}`
                           : 'Объединено', 'ok');
      load();
    }
    else { toast(d.detail || 'Не удалось объединить', 'err'); b.disabled = false; }
  } catch { toast('Нет связи с сервером', 'err'); b.disabled = false; }
}

let tt;
function toast(m, k=''){ const el = $('toast'); el.textContent = m;
  el.className = `toast show ${k}`;
  clearTimeout(tt); tt = setTimeout(() => el.className = 'toast', 2400); }

load();
