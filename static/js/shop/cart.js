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

const dropGone = $('dropGone');
if (dropGone) dropGone.onclick = () => drop(
  [...document.querySelectorAll('.cart-row[data-gone="1"]')].map(r => r.dataset.part));

// Без входа на странице только товары — оформлять нечего
const form = $('orderForm');
if (form && $('cname')){
  const picked = name => form.querySelector(`input[name=${name}]:checked`);
  const val = name => (picked(name) || {}).value;

  // Самовывоз — филиал, доставка — адрес; лишнее прячем, а не отключаем:
  // поле, которое не нужно заполнять, не должно попадаться на глаза.
  // Сводка справа повторяет выбор — его видно рядом с итогом и кнопкой
  const sync = () => {
    const ship = val('dm') === 'shipping';
    $('pickupBox').hidden = ship;
    $('addrBox').hidden = !ship;
    $('shipLine').textContent = ship ? 'по тарифу ТК' : 'бесплатно';
    const br = $('branch').selectedOptions[0];
    $('sumShip').textContent = ship ? 'Доставка ТК' : 'Самовывоз' + (br ? ' — ' + br.text : '');
    $('sumPay').textContent = picked('pm') ? picked('pm').dataset.label : '';
    $('place').textContent = val('pm') && val('pm') !== 'on_receipt'
      ? 'Оформить и оплатить' : 'Оформить заказ';
  };
  form.querySelectorAll('input[type=radio]').forEach(r => r.onchange = sync);
  $('branch').onchange = sync;
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
  live(rules);
  $('agree').onchange = () => { if ($('agree').checked) fieldError($('agree'), ''); };

  form.onsubmit = async e => {
    e.preventDefault();
    let ok = validate(rules);
    if (!$('agree').checked){
      fieldError($('agree'), 'Без согласия мы не можем принять заказ');
      if (ok) $('agree').focus();
      ok = false;
    }
    if (!ok) return;

    $('place').disabled = true;
    try {
      const ship = val('dm') === 'shipping';
      const pm = val('pm');
      const r = await fetch('/api/orders', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          contact_name: $('cname').value.trim(),
          contact_phone: $('cphone').value.trim(),
          delivery_method: val('dm'),
          pickup_branch_id: ship ? null : +$('branch').value,
          delivery_address: ship ? $('addr').value.trim() : null,
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
