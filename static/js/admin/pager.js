// Постраничный вывод для списков бэкенда («Машины», «Детали»).
//
// Раньше список приходил целиком — сотни карточек с фото и полями
// правки разом. Теперь по 20, с выбором 50 или 100: размер страницы
// запоминается в браузере отдельно для каждого списка.
//
//   const pager = makePager($('pager'), 'parts', load);
//   fetch('/api/…?' + pager.query())  → pager.show(total)
//   pager.reset() — при смене фильтра вернуться на первую страницу

const PAGE_SIZES = [20, 50, 100];

function makePager(box, key, onChange){
  const store = 'pager:' + key;
  let size = 20, page = 0, total = 0;
  try {
    const saved = +localStorage.getItem(store);
    if (PAGE_SIZES.includes(saved)) size = saved;
  } catch (e) {}

  const pages = () => Math.max(1, Math.ceil(total / size));

  // Номера страниц: первая, последняя и соседи текущей, остальное — «…»
  function numbers(){
    const n = pages(), out = [];
    for (let i = 0; i < n; i++){
      if (i === 0 || i === n - 1 || Math.abs(i - page) <= 1) out.push(i);
      else if (out[out.length - 1] !== '…') out.push('…');
    }
    return out;
  }

  function render(){
    if (!total){ box.innerHTML = ''; return; }
    const from = page * size + 1, to = Math.min(total, (page + 1) * size);
    box.innerHTML = `
      <span class="pg-info">${from}–${to} из ${total}</span>
      <span class="pg-pages">${pages() > 1 ? `
        <button data-p="${page - 1}" ${page === 0 ? 'disabled' : ''} aria-label="Назад">‹</button>
        ${numbers().map(i => i === '…' ? '<span class="pg-gap">…</span>'
          : `<button data-p="${i}" class="${i === page ? 'on' : ''}">${i + 1}</button>`).join('')}
        <button data-p="${page + 1}" ${page >= pages() - 1 ? 'disabled' : ''} aria-label="Вперёд">›</button>`
        : ''}</span>
      <span class="pg-size">по ${PAGE_SIZES.map(s =>
        `<button data-s="${s}" class="${s === size ? 'on' : ''}">${s}</button>`).join('')}</span>`;
  }

  box.onclick = e => {
    const b = e.target.closest('button'); if (!b || b.disabled) return;
    if (b.dataset.s){
      // Первая строка текущей страницы остаётся на экране и после смены размера
      const first = page * size;
      size = +b.dataset.s;
      page = Math.floor(first / size);
      try { localStorage.setItem(store, size); } catch (e) {}
    } else {
      page = +b.dataset.p;
    }
    onChange();
    // Новая страница начинается сверху, а не там, где был переключатель
    box.parentElement.scrollIntoView({block: 'start', behavior: 'smooth'});
  };

  return {
    query: () => `limit=${size}&offset=${page * size}`,
    reset(){ page = 0; },
    show(n){
      total = n;
      // Удалили последнюю строку на последней странице — шагаем назад
      if (page > 0 && page >= pages()){ page = pages() - 1; onChange(); return; }
      render();
    },
  };
}

// Общее число строк — в заголовке ответа, тело остаётся простым списком
const totalOf = r => +(r.headers.get('X-Total-Count') || 0);
