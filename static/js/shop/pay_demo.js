// Скрипт шаблона templates/shop/pay_demo.html — учебная оплата без банка.

const box = document.getElementById('demoPay');
box.querySelectorAll('button[data-r]').forEach(b => b.onclick = async () => {
  box.querySelectorAll('button').forEach(x => x.disabled = true);
  const r = await fetch('/pay/demo/' + encodeURIComponent(box.dataset.id), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({result: b.dataset.r})});
  const d = await r.json().catch(() => ({}));
  if (r.ok) location.href = d.redirect_url;
  else box.querySelectorAll('button').forEach(x => x.disabled = false);
});
