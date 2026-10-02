// Скрипт шаблона templates/shop/cart.html.

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3200); }

async function drop(ids){
  const r = await Promise.all(ids.map(id => fetch('/api/cart/' + id, {method: 'DELETE'})));
  // Перезагружаем, а не убираем строку руками: сумма, предупреждения
  // и счётчик в шапке считаются на сервере
  if (r.every(x => x.ok)) location.reload(); else toast('Не получилось убрать');
}

document.querySelectorAll('.cart-row .drop').forEach(b => b.onclick = () => {
  b.disabled = true;
  drop([b.closest('.cart-row').dataset.part]);
});

// Количество: − и + сохраняются сразу, страница перезагружается — сумма
// строки, итог и счётчик в шапке считаются на сервере. Больше остатка
// сервер не даст и скажет, сколько есть
document.querySelectorAll('.cart-row .qty').forEach(box => {
  const row = box.closest('.cart-row');
  qtyStepper(box, async n => {
    box.querySelectorAll('button,input').forEach(x => x.disabled = true);
    try {
      const r = await fetch('/api/cart/' + row.dataset.part, {method: 'PATCH',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({qty: n})});
      const d = await r.json().catch(() => ({}));
      if (r.ok){ location.reload(); return; }
      toast(typeof d.detail === 'string' ? d.detail : 'Не получилось изменить количество');
    } catch { toast('Нет связи с сервером'); }
    box.querySelectorAll('button,input').forEach(x => x.disabled = false);
  });
});

const dropGone = $('dropGone');
if (dropGone) dropGone.onclick = () => drop(
  [...document.querySelectorAll('.cart-row[data-gone="1"]')].map(r => r.dataset.part));

// Без входа на странице только товары — оформлять нечего
const form = $('orderForm');
if (form && $('cname')){
  const picked = name => form.querySelector(`input[name=${name}]:checked`);
  const val = name => (picked(name) || {}).value;
  const rub = n => Math.round(+n).toLocaleString('ru') + ' ₽';
  const esc = s => String(s ?? '').replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  // ── Доставка службой ─────────────────────────────────────────
  // Город → варианты с ценой → пункт выдачи или адрес. Цена здесь —
  // только чтобы показать; при оформлении сервер считает её заново
  const shipBox = $('shipBox');
  const D = {city: '', cdek_code: null, options: [], point: null, points: []};
  const opt = () => D.options.find(o => `${o.carrier}:${o.mode}` === val('dopt')) || null;
  const postcode = () => $('dPost') && $('dPost').value.length === 6 ? $('dPost').value : null;

  // Сводка справа повторяет выбор — его видно рядом с итогом и кнопкой
  const sync = () => {
    const ship = val('dm') === 'shipping';
    const o = ship && shipBox ? opt() : null;
    $('pickupBox').hidden = ship;
    if (shipBox) shipBox.hidden = !ship;
    // Адрес — курьеру и Почте; без служб — как раньше, для ТК
    $('addrBox').hidden = !ship || (shipBox && (!o || o.mode === 'pvz'));
    if (shipBox) $('dPointBox').hidden = !(o && o.mode === 'pvz');
    const br = $('branch').selectedOptions[0];
    $('sumShip').textContent = !ship ? 'Самовывоз' + (br ? ' — ' + br.text : '')
      : o ? o.title : shipBox ? 'Доставка — выберите вариант' : 'Доставка ТК';
    $('shipLine').textContent = !ship ? 'бесплатно'
      : o ? (o.from && !D.point ? 'от ' : '') + rub(o.price) : shipBox ? '—' : 'по тарифу ТК';
    const goods = +$('grandTotal').dataset.goods;
    $('grandTotal').textContent = rub(goods + (o ? +o.price : 0));
    $('sumPay').textContent = picked('pm') ? picked('pm').dataset.label : '';
    $('place').textContent = val('pm') && val('pm') !== 'on_receipt'
      ? 'Оформить и оплатить' : 'Оформить заказ';
  };
  form.querySelectorAll('input[name=dm], input[name=pm]').forEach(r => r.onchange = sync);
  $('branch').onchange = sync;

  if (shipBox){
    // Подсказка города — справочник СДЭК: у города там код для тарифов
    let ct, seq = 0;
    $('dCity').addEventListener('input', () => {
      D.cdek_code = null;
      clearTimeout(ct);
      const q = $('dCity').value.trim();
      if (q.length < 2){ $('dCityList').hidden = true; return; }
      ct = setTimeout(async () => {
        const my = ++seq;
        const rows = await (await fetch('/api/delivery/cities?q=' + encodeURIComponent(q))).json();
        if (my !== seq) return;
        $('dCityList').innerHTML = rows.map((c, i) =>
          `<button type="button" role="option" data-i="${i}">${esc(c.full_name)}</button>`).join('');
        $('dCityList').hidden = !rows.length;
        $('dCityList').querySelectorAll('button').forEach(b => b.onclick = () => {
          const c = rows[+b.dataset.i];
          $('dCity').value = c.name; D.cdek_code = c.cdek_code;
          $('dCityList').hidden = true;
          loadQuotes();
        });
      }, 250);
    });
    // Город набрали без подсказки — тоже считаем: Почта и Яндекс
    // справятся и без кода СДЭК
    $('dCity').addEventListener('change', () => setTimeout(() => {
      if ($('dCityList').hidden) loadQuotes();
    }, 200));
    document.addEventListener('click', e => {
      if (!e.target.closest('.city-fld')) $('dCityList').hidden = true;
    });
    if ($('dPost')) $('dPost').addEventListener('input', () => {
      $('dPost').value = $('dPost').value.replace(/\D/g, '').slice(0, 6);
      if ($('dPost').value.length === 6 && $('dCity').value.trim()) loadQuotes();
    });

    let qseq = 0;
    async function loadQuotes(point){
      const city = $('dCity').value.trim();
      if (city.length < 2) return;
      const my = ++qseq;
      if (!point){ $('dState').textContent = 'Считаем доставку…'; $('dOptions').innerHTML = ''; }
      const r = await fetch('/api/delivery/quotes', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({city, cdek_code: D.cdek_code, postcode: postcode(),
                              point: point || null})});
      const d = await r.json().catch(() => ({}));
      if (my !== qseq) return;
      if (point){
        // Пересчёт по выбранному пункту Яндекса: цена у него своя
        const y = (d.options || []).find(o => o.carrier === 'yandex');
        const o = D.options.find(o => o.carrier === 'yandex');
        if (o && y){ o.price = y.price; o.days = y.days; o.from = false; $('dPointErr').hidden = true; }
        else {
          $('dPointErr').textContent = 'В этот пункт Яндекс не доставляет — выберите другой';
          $('dPointErr').hidden = false; D.point = null;
        }
        drawOptions(); sync(); return;
      }
      D.city = city; D.options = d.options || []; D.point = null;
      const msg = D.options.length
        ? `Отправим из филиала: ${d.from}.`
          + (d.need_postcode ? ' Почта России тоже посчитает, если указать индекс.' : '')
          + ' Выберите вариант:'
        : 'Служба не посчитала доставку в этот город. Проверьте название'
          + (d.need_postcode ? ' или укажите индекс' : '') + ' — или выберите самовывоз.';
      $('dState').textContent = msg;
      drawOptions(); sync();
    }

    function drawOptions(){
      const cur = val('dopt');
      $('dOptions').innerHTML = D.options.map(o => {
        const k = `${o.carrier}:${o.mode}`;
        return `<label class="tile"><input type="radio" name="dopt" value="${k}" ${k === cur ? 'checked' : ''}>
          <span><b>${esc(o.title)}</b>
          <small>${o.from && !D.point ? 'от ' : ''}${rub(o.price)}${o.days ? ' · ' + esc(o.days) : ''}</small></span></label>`;
      }).join('');
      $('dOptions').querySelectorAll('input').forEach(i => i.onchange = () => {
        D.point = null; loadPoints(); sync();
      });
    }

    // Пункты выдачи — списком с поиском. Карте нужен свой ключ
    // Яндекс Карт; список работает и без него
    async function loadPoints(){
      const o = opt();
      if (!o || o.mode !== 'pvz'){ sync(); return; }
      $('dPoints').innerHTML = '<p class="hint">Загружаем пункты…</p>';
      const p = new URLSearchParams({carrier: o.carrier, city: D.city});
      if (D.cdek_code) p.set('cdek_code', D.cdek_code);
      D.points = await (await fetch('/api/delivery/points?' + p)).json();
      $('dPointQ').value = '';
      drawPoints();
    }
    function drawPoints(){
      const q = $('dPointQ').value.trim().toLowerCase();
      const list = D.points.filter(p => !q || (p.name + ' ' + p.address).toLowerCase().includes(q))
                           .slice(0, 60);
      $('dPoints').innerHTML = list.length ? list.map(p => `
        <label class="pt"><input type="radio" name="dpoint" value="${esc(p.code)}"
               ${p.code === D.point ? 'checked' : ''}>
          <span><b>${esc(p.address)}</b><small>${esc(p.name)}${p.hours ? ' · ' + esc(p.hours) : ''}</small></span></label>`).join('')
        : '<p class="hint">Пунктов не нашлось</p>';
      $('dPoints').querySelectorAll('input').forEach(i => i.onchange = () => {
        D.point = i.value; $('dPointErr').hidden = true;
        if (opt() && opt().carrier === 'yandex') loadQuotes(D.point); else sync();
      });
    }
    $('dPointQ').addEventListener('input', drawPoints);
  }
  sync();

  phoneMask($('cphone'));
  if ($('cphone').value) $('cphone').dispatchEvent(new Event('input'));

  const rules = [
    [$('cname'), v => Check.name(v)],
    [$('cphone'), v => Check.phoneRule(v)],
    [$('branch'), v => v ? '' : 'Выберите филиал'],
    [$('addr'), v => Check.text(v, {min: 10, max: 500, what: 'Адрес'})],
    [$('cmt'), v => Check.text(v, {max: 1000, what: 'Комментарий'})],
  ];
  if (shipBox) rules.push([$('dCity'), v => Check.text(v, {min: 2, max: 120, what: 'Город'})]);
  live(rules);
  $('agree').onchange = () => { if ($('agree').checked) fieldError($('agree'), ''); };

  form.onsubmit = async e => {
    e.preventDefault();
    let ok = validate(rules);
    const ship = val('dm') === 'shipping';
    const o = ship && shipBox ? opt() : null;
    if (ship && shipBox && ok){
      if (!o){ toast('Выберите вариант доставки'); ok = false; }
      else if (o.mode === 'pvz' && !D.point){
        $('dPointErr').textContent = 'Выберите пункт выдачи'; $('dPointErr').hidden = false;
        $('dPointQ').focus(); ok = false;
      } else if (o.mode === 'post' && !postcode()){
        toast('Для Почты России нужен индекс'); $('dPost').focus(); ok = false;
      }
    }
    if (!$('agree').checked){
      fieldError($('agree'), 'Без согласия мы не можем принять заказ');
      if (ok) $('agree').focus();
      ok = false;
    }
    if (!ok) return;

    $('place').disabled = true;
    try {
      const pm = val('pm');
      const r = await fetch('/api/orders', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          contact_name: $('cname').value.trim(),
          contact_phone: $('cphone').value.trim(),
          delivery_method: val('dm'),
          pickup_branch_id: ship ? null : +$('branch').value,
          delivery_address: ship && (!o || o.mode !== 'pvz') ? $('addr').value.trim() : null,
          delivery_carrier: o ? o.carrier : null,
          delivery_mode: o ? o.mode : null,
          delivery_city: o ? D.city : null,
          delivery_cdek_code: o ? D.cdek_code : null,
          delivery_postcode: o ? postcode() : null,
          delivery_point: o && o.mode === 'pvz' ? D.point : null,
          payment_method: pm === 'on_receipt' ? 'on_receipt' : 'online',
          pay_with: pm === 'on_receipt' ? null : pm,
          comment: $('cmt').value.trim() || null,
          agree: true,
        })});
      const d = await r.json().catch(() => ({}));
      if (r.ok){
        // Онлайн — сразу к оплате; банк не ответил — на страницу заказа,
        // оплатить можно оттуда
        location.href = d.redirect_url || ('/account/orders/' + d.number
          + (d.payment_error ? '?payerr=1' : ''));
        return;
      }
      if (!serverErrors(d, {contact_name: $('cname'), contact_phone: $('cphone'),
                            delivery_address: $('addr'), comment: $('cmt')}))
        toast(typeof d.detail === 'string' ? d.detail : 'Не получилось оформить');
    } catch { toast('Нет связи с сервером'); }
    $('place').disabled = false;
  };
}
