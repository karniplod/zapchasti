// Скрипт шаблона templates/admin/_quickadd.html.

(function(){
  const modal = document.getElementById('qaModal');
  const name  = document.getElementById('qaName');
  const warn  = document.getElementById('qaWarn');
  let mode = null, parentId = null, onDone = null;

  // Типы кузова подгружаем один раз
  fetch('/api/reference/body-types').then(r => r.json()).then(list => {
    const sel = document.getElementById('qaBody');
    list.forEach(b => sel.add(new Option(b, b)));
  });

  const TITLES = {
    brand:      ['Новая марка', 'Название', ''],
    model:      ['Новая модель', 'Название модели', ''],
    generation: ['Новое поколение', 'Обозначение кузова', 'Например: XV70, E90, B7']
  };

  /**
   * openQuickAdd('generation', modelId, 'Toyota Camry', callback)
   * callback получает {id, name} созданной или найденной записи
   */
  window.openQuickAdd = function(kind, parent, contextText, done){
    mode = kind; parentId = parent; onDone = done;
    const [title, label, hint] = TITLES[kind];
    document.getElementById('qaTitle').textContent = title;
    document.getElementById('qaNameLabel').textContent = label;
    document.getElementById('qaCtx').textContent = contextText || hint;
    document.getElementById('qaGenFields').hidden = kind !== 'generation';
    name.value = ''; warn.className = 'warn';
    document.getElementById('qaFrom').value = '';
    document.getElementById('qaTo').value = '';
    document.getElementById('qaSave').disabled = true;
    modal.showModal();
    name.focus();
  };

  function valid(){
    if (!name.value.trim()) return false;
    if (mode === 'generation') return !!document.getElementById('qaFrom').value;
    return true;
  }
  ['qaName','qaFrom'].forEach(id =>
    document.getElementById(id).addEventListener('input', () => {
      document.getElementById('qaSave').disabled = !valid();
    }));

  name.addEventListener('keydown', e => {
    if (e.key === 'Enter' && valid()) document.getElementById('qaSave').click();
  });

  document.getElementById('qaCancel').onclick = () => modal.close();

  document.getElementById('qaSave').onclick = async () => {
    const btn = document.getElementById('qaSave');
    btn.disabled = true;

    const url = `/api/reference/${mode}s`;
    const body = {name: name.value.trim()};
    if (mode === 'model') body.brand_id = parentId;
    if (mode === 'generation'){
      body.model_id  = parentId;
      body.body_type = document.getElementById('qaBody').value || null;
      body.year_from = +document.getElementById('qaFrom').value;
      const to = document.getElementById('qaTo').value;
      body.year_to = to ? +to : null;
    }

    try {
      const r = await fetch(url, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      const d = await r.json();

      if (!r.ok){
        warn.textContent = d.detail || 'Не получилось сохранить';
        warn.className = 'warn show';
        btn.disabled = false;
        return;
      }

      // Пересечение годов — не ошибка, но приёмщик должен это увидеть
      if (d.warning){
        warn.textContent = d.warning + ' Запись добавлена — при ошибке скажите менеджеру.';
        warn.className = 'warn show';
        setTimeout(() => { modal.close(); onDone && onDone(d); }, 3200);
        return;
      }

      modal.close();
      onDone && onDone(d);
    } catch {
      warn.textContent = 'Нет связи с сервером';
      warn.className = 'warn show';
      btn.disabled = false;
    }
  };
})();
