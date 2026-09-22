// Скрипт шаблона templates/shop/cart.html.

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 2600); }

document.querySelectorAll('.cart-row .drop').forEach(b => b.onclick = async () => {
  const row = b.closest('.cart-row');
  b.disabled = true;
  const r = await fetch('/api/cart/' + row.dataset.part, {method: 'DELETE'});
  // Перезагружаем, а не убираем строку руками: сумма, предупреждения
  // и счётчик в шапке считаются на сервере
  if (r.ok) location.reload(); else { toast('Не получилось убрать'); b.disabled = false; }
});

const form = $('orderForm');
if (form){
  $('dm').onchange = () => { $('addrBox').hidden = $('dm').value !== 'shipping'; };

  form.onsubmit = async e => {
    e.preventDefault();
    $('place').disabled = true;
    try {
      const r = await fetch('/api/orders', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          delivery_method: $('dm').value,
          delivery_address: $('addr').value.trim() || null,
          comment: $('cmt').value.trim() || null,
        })});
      const d = await r.json().catch(() => ({}));
      if (r.ok){ location.href = '/account/orders/' + d.number; return; }
      toast(d.detail || 'Не получилось оформить');
    } catch { toast('Нет связи с сервером'); }
    $('place').disabled = false;
  };
}
