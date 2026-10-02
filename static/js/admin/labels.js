// Скрипт шаблона templates/admin/labels.html.

// Номера деталей — в data-атрибуте body: файл статический
const IDS = (document.body.dataset.ids || '').split(',').filter(Boolean).map(Number);

document.getElementById('print').onclick = () => window.print();

// Отметку «напечатано» ставим только после реальной печати,
// иначе после закрытого предпросмотра этикетки пропадут из очереди
window.addEventListener('afterprint', async () => {
  if (!IDS.length) return;
  await fetch('/api/parts/labels/printed', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ids: IDS})
  });
});
