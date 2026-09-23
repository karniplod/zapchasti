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

// Машины на разборе: сервер отдаёт восемь строчек, а показываем ровно
// столько, чтобы ряды были полными — иначе на широком мониторе вторая
// строка оставалась наполовину пустой. Сколько строчек влезает в ряд,
// не вычисляем по формуле, а смотрим по факту: у строчек одного ряда
// одинаковый верх. Больше двух рядов не показываем: блок на главной —
// напоминание, что машины есть, а не их витрина.
// Прячем атрибутом hidden — так лишние ссылки не ловят фокус и не
// читаются вслух программой чтения с экрана.
(function(){
  const row = document.getElementById('carsRow');
  if (!row) return;
  const cars = [...row.children];
  const ROWS = 2;

  function fit(){
    cars.forEach(c => { c.hidden = false; });
    const top = cars[0].getBoundingClientRect().top;
    const inRow = cars.filter(c => Math.abs(c.getBoundingClientRect().top - top) < 4).length;
    // Полных рядов столько, сколько наберётся из имеющихся машин
    const show = Math.min(cars.length, inRow * Math.min(ROWS, Math.floor(cars.length / inRow)))
                 || cars.length;
    cars.forEach((c, i) => { c.hidden = i >= show; });
  }

  fit();
  let t;
  addEventListener('resize', () => { clearTimeout(t); t = setTimeout(fit, 120); });
})();
