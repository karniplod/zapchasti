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
