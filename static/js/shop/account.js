// Скрипт шаблона templates/shop/account.html.

const $ = id => document.getElementById(id);
let t;
function toast(m){ $('toast').textContent = m; $('toast').classList.add('show');
  clearTimeout(t); t = setTimeout(() => $('toast').classList.remove('show'), 3000); }

const clear = $('clearHist');
if (clear) clear.onclick = async () => {
  // Спрашиваем прямо: действие необратимое, а кнопка стоит у самого списка
  // Без переносов внутри литерала: в шаблоне их слишком легко получить
  // настоящими, и тогда весь скрипт перестаёт разбираться
  const question = 'Убрать все поиски из кабинета? Записи останутся '
      + 'в нашей статистике спроса, но уже без связи с вами, '
      + 'и вернуть их в кабинет будет нельзя.';
  if (!confirm(question)) return;

  clear.disabled = true;
  try {
    const r = await fetch('/api/account/searches', {method: 'DELETE'});
    if (r.ok){ location.reload(); return; }
    toast('Не получилось очистить');
  } catch { toast('Нет связи с сервером'); }
  clear.disabled = false;
};
