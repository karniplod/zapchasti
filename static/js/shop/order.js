// Скрипт шаблона templates/shop/order.html.

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3200); }

document.querySelectorAll('.pay').forEach(b => b.onclick = async () => {
  b.disabled = true;
  try {
    const r = await fetch(`/api/orders/${b.dataset.order}/pay`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({method: b.dataset.method})});
    const d = await r.json().catch(() => ({}));
    // Провайдер вернёт ссылку на оплату — тогда уходим на неё
    if (r.ok && d.redirect_url){ location.href = d.redirect_url; return; }
    toast(d.detail || 'Оплата недоступна');
  } catch { toast('Нет связи с сервером'); }
  b.disabled = false;
});

// Вернулись от банка, а платёж ещё «ожидает» — подтверждение приходит
// через секунды. Перезагружаем страницу несколько раз: сервер при
// каждом открытии сам спрашивает банк
const wait = $('payWait');
if (wait){
  const n = +(sessionStorage.getItem('payWait') || 0);
  if (n < 5){
    sessionStorage.setItem('payWait', n + 1);
    setTimeout(() => location.reload(), 3000);
  } else {
    sessionStorage.removeItem('payWait');
    wait.textContent = 'Банк ещё не подтвердил оплату. Если деньги списались, '
      + 'статус обновится сам в течение нескольких минут.';
  }
} else {
  sessionStorage.removeItem('payWait');
}

// ── Правка заказа покупателем ──────────────────────────────────
// Пока заказ новый и не оплачен. На сервер уходит только то, что
// поменялось; перед сохранением сервер считает новую сумму — с
// доставкой по новому адресу и составу — и ничего не записывает
// (/preview), так что «станет» на экране и в заказе совпадают.
(function(){
  const form = $('editForm');
  if (!form) return;
  const N = form.dataset.number, pvz = form.dataset.mode === 'pvz';
  const rub = n => Math.round(+n).toLocaleString('ru') + ' ₽';
  const esc = s => String(s ?? '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const D = {cdek: form.dataset.cdek ? +form.dataset.cdek : null, point: form.dataset.point || null,
             points: [], init: null, added: []};
  const val = id => $(id) ? $(id).value.trim() : null;
  const digits = s => String(s || '').replace(/\D/g, '').slice(-10);
  const ADDR = ['oeCity', 'oeStreet', 'oeHouse', 'oeBlock', 'oeFlat', 'oePost'];
  const qtyOf = r => r.classList.contains('is-gone') ? 0 : +r.querySelector('.qty-in').value;

  const snapshot = () => ({
    name: val('oeName'), phone: digits(val('oePhone')), comment: val('oeComment'),
    branch: val('oeBranch'), point: D.point,
    addr: ADDR.map(val).join('|'),
    items: [...form.querySelectorAll('.oe-item[data-item]')].map(r => r.dataset.item + ':' + qtyOf(r)).join(','),
    added: D.added.map(a => a.part_id + ':' + a.qty).join(','),
  });

  // Только изменённое — иначе сервер зря пересчитывал бы доставку
  function body(){
    const now = snapshot(), b = {};
    if (now.name !== D.init.name) b.contact_name = now.name;
    if (now.phone !== D.init.phone) b.contact_phone = val('oePhone');
    if (now.comment !== D.init.comment) b.comment = now.comment;
    if (now.branch !== D.init.branch) b.pickup_branch_id = +now.branch;
    if (now.addr !== D.init.addr || now.point !== D.init.point){
      Object.assign(b, {delivery_city: val('oeCity'), delivery_cdek_code: D.cdek});
      if (pvz) b.delivery_point = D.point;
      else if ($('oeStreet')) Object.assign(b, {
        delivery_country: form.dataset.country, delivery_street: val('oeStreet'),
        delivery_house: val('oeHouse'), delivery_block: val('oeBlock') || null,
        delivery_flat: val('oeFlat') || null, delivery_postcode: val('oePost') || null});
    }
    if (now.items !== D.init.items)
      b.items = [...form.querySelectorAll('.oe-item[data-item]')].map(r => ({id: +r.dataset.item, qty: qtyOf(r)}));
    if (D.added.length) b.add = D.added.map(a => ({part_id: a.part_id, qty: a.qty}));
    return b;
  }

  // «Было → станет»: сервер правит заказ и тут же откатывает правку
  let pt, pseq = 0;
  function changed(){
    clearTimeout(pt);
    const b = body();
    $('oeSave').disabled = true;
    $('oeSum').className = 'oe-sum';
    if (!Object.keys(b).length){ $('oeSum').textContent = 'Изменений пока нет'; return; }
    $('oeSum').textContent = 'Считаем новую сумму…';
    pt = setTimeout(async () => {
      const my = ++pseq;
      try {
        const r = await fetch(`/api/account/orders/${N}/preview`, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
        const d = await r.json().catch(() => ({}));
        if (my !== pseq) return;
        if (!r.ok){
          $('oeSum').className = 'oe-sum is-err';
          $('oeSum').textContent = typeof d.detail === 'string' ? d.detail : 'Проверьте поля';
          return;
        }
        $('oeSum').innerHTML = +d.total === +d.old_total
          ? `Сумма заказа не изменится: <b>${rub(d.total)}</b>`
          : `Сумма заказа: ${rub(d.old_total)} → <b>${rub(d.total)}</b>`
            + (+d.delivery !== +d.old_delivery
               ? `<small>доставка ${rub(d.old_delivery)} → ${rub(d.delivery)}${d.days ? ', срок ' + esc(d.days) : ''}</small>` : '')
            + (d.parcels > d.old_parcels ? '<small>Добавленная деталь лежит в другом городе — приедет отдельной посылкой</small>' : '');
        $('oeSave').disabled = false;
      } catch {
        if (my === pseq){ $('oeSum').className = 'oe-sum is-err'; $('oeSum').textContent = 'Нет связи с сервером'; }
      }
    }, 450);
  }

  // Открыть и закрыть
  $('editOpen').onclick = () => {
    form.hidden = false; $('editBar').hidden = true;
    D.init = snapshot();
    if (pvz) loadPoints();
    form.scrollIntoView({behavior: 'smooth', block: 'start'});
  };
  $('oeClose').onclick = () => location.reload();

  $('orderCancel').onclick = async () => {
    if (!await askConfirm('Детали вернутся в продажу. Если вы начали оплату, ссылка перестанет действовать.',
                          {title: 'Отменить заказ?', ok: 'Отменить заказ', cancel: 'Не отменять',
                           danger: true})) return;
    try {
      const r = await fetch(`/api/account/orders/${N}/cancel`, {method: 'POST'});
      const d = await r.json().catch(() => ({}));
      if (r.ok){ location.reload(); return; }
      toast(d.detail || 'Не получилось отменить');
    } catch { toast('Нет связи с сервером'); }
  };

  // Количество и «Убрать»
  form.querySelectorAll('.oe-item[data-item]').forEach(r => {
    qtyStepper(r.querySelector('.qty'), changed);
    r.querySelector('.oe-drop').onclick = () => {
      const gone = r.classList.toggle('is-gone');
      r.querySelector('.oe-drop').textContent = gone ? 'Вернуть' : 'Убрать';
      r.querySelectorAll('.qty button, .qty input').forEach(x => { x.disabled = gone; });
      changed();
    };
  });
  ['oeName', 'oePhone', 'oeComment', 'oeBranch', ...ADDR].forEach(id => {
    if ($(id)) $(id).addEventListener(id === 'oeBranch' ? 'change' : 'input', changed);
  });
  phoneMask($('oePhone'));

  // Подсказки улицы и дома (static/js/addr_suggest.js)
  addressSuggest({city: $('oeCity'), street: $('oeStreet'), house: $('oeHouse'), block: $('oeBlock'),
                  post: $('oePost'), streetList: $('oeStreetList'), houseList: $('oeHouseList'),
                  country: () => form.dataset.country, onChange: changed});

  // ── Добавить деталь ──
  // Ищем по каталогу (/api/catalog/parts): то же, что видит покупатель
  // на витрине, только в наличии. Добавленная деталь — новой строкой
  // в списке; в заказ она попадёт вместе с остальной правкой
  const inOrder = sku => !!form.querySelector(`.oe-item[data-sku="${CSS.escape(sku)}"]`);
  let fq, fseq = 0;
  $('oeAddQ').addEventListener('input', () => {
    clearTimeout(fq);
    const q = $('oeAddQ').value.trim();
    if (q.length < 2){ $('oeFound').hidden = true; return; }
    fq = setTimeout(async () => {
      const my = ++fseq;
      let d = {items: []};
      try { d = await (await fetch('/api/catalog/parts?' + new URLSearchParams({q, page: 1}))).json(); } catch {}
      if (my !== fseq) return;
      const rows = (d.items || []).slice(0, 8);
      $('oeFound').innerHTML = rows.length ? rows.map((p, i) => {
        const car = [p.brand, p.model, p.year].filter(Boolean).join(' ');
        const why = inOrder(p.sku) || D.added.some(a => a.sku === p.sku) ? 'уже в заказе'
                  : !p.price ? 'цена по запросу' : '';
        return `<div class="f-row">
          <span class="f-pic">${p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy">` : ''}</span>
          <span class="f-nm"><b>${esc(p.name)}</b>
            <small>${esc([p.sku, car, p.city].filter(Boolean).join(' · '))}</small></span>
          <span class="f-pr">${p.price ? rub(p.price) : ''}</span>
          ${why ? `<span class="f-why">${why}</span>`
                : `<button type="button" class="btn btn-ghost btn-sm" data-i="${i}">Добавить</button>`}
        </div>`;
      }).join('') : '<p class="hint">Ничего не нашлось — попробуйте артикул или каталожный номер</p>';
      $('oeFound').hidden = false;
      $('oeFound').querySelectorAll('button[data-i]').forEach(b => b.onclick = () => {
        addPart(rows[+b.dataset.i]);
        $('oeFound').hidden = true; $('oeAddQ').value = '';
      });
    }, 300);
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.oe-add')) $('oeFound').hidden = true;
  });

  function addPart(p){
    const a = {part_id: p.id, sku: p.sku, name: p.name, price: p.price, qty: 1};
    D.added.push(a);
    // В группу своего филиала; такого ещё нет — новая группа: оттуда
    // заказ поедет ещё одной посылкой
    const branch = [p.city, p.branch_name].filter(Boolean).join(', ');
    let group = [...form.querySelectorAll('.oe-group')].find(g => g.dataset.branch === branch);
    if (!group){
      group = document.createElement('div');
      group.className = 'oe-group is-new';
      group.dataset.branch = branch;
      group.innerHTML = `<p class="oe-city-h"><b>${esc(p.city || 'Склад не указан')}</b><small>${esc(p.branch_name || '')}`
        + (form.dataset.method === 'shipping' ? ' · новая посылка' : '') + '</small></p>';
      form.querySelector('.oe-items').append(group);
    }
    const row = document.createElement('div');
    row.className = 'oe-item is-new';
    row.dataset.sku = p.sku;
    row.innerHTML = `<span class="nm">${esc(p.name)}<small class="mono">${esc(p.sku)} · <span class="src">будет добавлена</span></small></span>
      <div class="qty qty-sm" data-max="${Math.max(1, +p.quantity || 1)}">
        <button type="button" class="qty-btn" data-d="-1" aria-label="Меньше">−</button>
        <input class="qty-in" type="number" inputmode="numeric" min="1" value="1" aria-label="Количество">
        <button type="button" class="qty-btn" data-d="1" aria-label="Больше">+</button>
      </div>
      <span class="pr">${rub(p.price)} / шт</span>
      <button type="button" class="linkish oe-drop">Убрать</button>`;
    group.append(row);
    qtyStepper(row.querySelector('.qty'), n => { a.qty = n; changed(); });
    row.querySelector('.oe-drop').onclick = () => {
      D.added.splice(D.added.indexOf(a), 1);
      row.remove();
      if (group.classList.contains('is-new') && !group.querySelector('.oe-item')) group.remove();
      changed();
    };
    changed();
  }
  if ($('oePost')) $('oePost').addEventListener('input', () => {
    $('oePost').value = $('oePost').value.replace(/\D/g, '').slice(0, 6);
  });

  // Город — подсказка из справочника СДЭК: у города там код для тарифов
  if ($('oeCity')){
    let ct, seq = 0;
    $('oeCity').addEventListener('input', () => {
      D.cdek = null;
      clearTimeout(ct);
      const q = $('oeCity').value.trim();
      if (q.length < 2){ $('oeCityList').hidden = true; return; }
      ct = setTimeout(async () => {
        const my = ++seq;
        const rows = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(q))).json();
        if (my !== seq) return;
        $('oeCityList').innerHTML = rows.map((c, i) =>
          `<button type="button" role="option" data-i="${i}">${esc(c.full_name)}</button>`).join('');
        $('oeCityList').hidden = !rows.length;
        $('oeCityList').querySelectorAll('button').forEach(b => b.onclick = () => {
          const c = rows[+b.dataset.i];
          $('oeCity').value = c.name; D.cdek = c.cdek_code; $('oeCityList').hidden = true;
          fieldError($('oeCity'), '');
          if (pvz){ D.point = null; loadPoints(); }
          changed();
        });
      }, 250);
    });
    document.addEventListener('click', e => {
      if (!e.target.closest('.oe-city')) $('oeCityList').hidden = true;
    });
  }

  // Пункты выдачи той же службы в выбранном городе
  async function loadPoints(){
    const city = val('oeCity');
    if (!city || city.length < 2) return;
    $('oePoints').innerHTML = '<p class="hint">Загружаем пункты…</p>';
    if (!D.cdek && form.dataset.carrier === 'cdek'){
      const c = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(city))).json();
      D.cdek = c.length ? c[0].cdek_code : null;
    }
    const p = new URLSearchParams({carrier: form.dataset.carrier, city});
    if (D.cdek) p.set('cdek_code', D.cdek);
    D.points = await (await fetch('/api/delivery/points?' + p)).json();
    drawPoints();
  }
  function drawPoints(){
    const q = $('oePointQ').value.trim().toLowerCase();
    const cur = D.points.find(p => p.code === D.point);
    const list = D.points.filter(p => p !== cur
      && (!q || (p.code + ' ' + p.name + ' ' + p.address).toLowerCase().includes(q))).slice(0, 60);
    // Выбранный пункт — первым: видно, что выбрано, без прокрутки
    const rows = cur ? [cur, ...list] : list;
    $('oePoints').innerHTML = rows.length ? rows.map(p => `
      <label class="pt"><input type="radio" name="oePoint" value="${esc(p.code)}" ${p.code === D.point ? 'checked' : ''}>
        <span><b>${esc(p.address)}</b><small>${esc(p.name)}${p.hours ? ' · ' + esc(p.hours) : ''}</small></span></label>`).join('')
      : '<p class="hint">Пунктов не нашлось — проверьте город</p>';
    $('oePoints').querySelectorAll('input').forEach(i => i.onchange = () => {
      D.point = i.value; $('oePointErr').hidden = true; changed();
    });
  }
  if ($('oePointQ')) $('oePointQ').addEventListener('input', drawPoints);

  // Проверка и сохранение
  const SHORT = new RegExp(`^[${LETTERS}\\d/ .-]{1,20}$`);
  const rules = [
    [$('oeName'), v => Check.name(v)],
    [$('oePhone'), v => Check.phoneRule(v)],
    [$('oeCity'), v => Check.text(v, {min: 2, max: 120, what: 'Город'})],
    [$('oeStreet'), v => v.trim().length >= 2 ? '' : 'Укажите улицу'],
    [$('oeHouse'), v => /\d/.test(v) && SHORT.test(v.trim()) ? '' : 'Дом: номер, например 10 или 10/2'],
    [$('oePost'), v => !v ? (form.dataset.mode === 'post' ? 'Для Почты России нужен индекс' : '')
                          : /^\d{6}$/.test(v) ? '' : 'Индекс — шесть цифр'],
  ].filter(r => r[0]);
  live(rules);

  form.onsubmit = async e => {
    e.preventDefault();
    const b = body();
    // Улицу и дом проверяем, только если адрес меняют: у старых заказов
    // адрес хранится одной строкой, и поля могут быть пустыми
    const addrChanged = 'delivery_city' in b;
    if (!validate(rules.filter(([inp]) => addrChanged || !ADDR.includes(inp.id)))) return;
    if (pvz && addrChanged && !D.point){
      $('oePointErr').textContent = 'Выберите пункт выдачи'; $('oePointErr').hidden = false; return;
    }
    $('oeSave').disabled = true;
    try {
      const r = await fetch(`/api/account/orders/${N}`, {method: 'PATCH',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
      const d = await r.json().catch(() => ({}));
      if (r.ok){ location.href = location.pathname + '?edited=1'; return; }
      toast(typeof d.detail === 'string' ? d.detail : 'Не получилось сохранить');
    } catch { toast('Нет связи с сервером'); }
    $('oeSave').disabled = false;
  };
})();

if (new URLSearchParams(location.search).get('edited') === '1'){
  toast('Заказ изменён');
  history.replaceState(null, '', location.pathname);
}
