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
             points: [], init: null};
  const val = id => $(id) ? $(id).value.trim() : null;
  const digits = s => String(s || '').replace(/\D/g, '').slice(-10);
  const ADDR = ['oeCity', 'oeStreet', 'oeHouse', 'oeBlock', 'oeFlat', 'oePost'];
  const qtyOf = r => r.classList.contains('is-gone') ? 0 : +r.querySelector('.qty-in').value;

  const snapshot = () => ({
    name: val('oeName'), phone: digits(val('oePhone')), comment: val('oeComment'),
    branch: val('oeBranch'), point: D.point,
    addr: ADDR.map(val).join('|'),
    items: [...form.querySelectorAll('.oe-item')].map(r => r.dataset.item + ':' + qtyOf(r)).join(','),
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
      b.items = [...form.querySelectorAll('.oe-item')].map(r => ({id: +r.dataset.item, qty: qtyOf(r)}));
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
               ? `<small>доставка ${rub(d.old_delivery)} → ${rub(d.delivery)}${d.days ? ', срок ' + esc(d.days) : ''}</small>` : '');
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
  form.querySelectorAll('.oe-item').forEach(r => {
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
