// Скрипт шаблона templates/home.html.

const $ = id => document.getElementById(id);

$('vin').addEventListener('input', e => {
  const v = e.target.value.toUpperCase().replace(/[^A-HJ-NPR-Z0-9]/g,'');
  e.target.value = v;
  $('vinGo').disabled = v.length !== 17;
});
$('vin').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !$('vinGo').disabled) go();
});
$('vinGo').onclick = go;

// Разбор VIN живёт в каталоге — туда и ведём, чтобы не дублировать логику
function go(){ location.href = '/catalog?vin=' + encodeURIComponent($('vin').value); }
