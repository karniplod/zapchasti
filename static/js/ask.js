// Подтверждение действия — своё окно вместо системного confirm():
// системное выглядит по-разному в каждом браузере, подписано адресом
// сайта и кнопки в нём не назвать по делу («Отменить заказ», а не «ОК»).
//
//   if (!await askConfirm('Удалить фото?', {ok: 'Удалить', danger: true})) return;
//
// Подключается и на витрине (templates/_base.html), и в бэкенде
// (templates/admin/_nav.html); вид — из темы страницы: .ask в
// static/css/core.css и static/admin.css.
// Esc, клик мимо окна и «Отмена» — это «нет».

function askConfirm(text, {title = 'Подтвердите действие', ok = 'Да', cancel = 'Отмена',
                           danger = false} = {}){
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'ask';
    d.setAttribute('aria-labelledby', 'askTitle');
    d.innerHTML = `<h2 id="askTitle"></h2><p></p>
      <div class="ask-btns">
        <button type="button" class="ask-no"></button>
        <button type="button" class="ask-yes ${danger ? 'is-danger' : ''}"></button>
      </div>`;
    // Текст — только textContent: в нём бывают название детали и адрес
    d.querySelector('h2').textContent = title;
    d.querySelector('p').textContent = text;
    d.querySelector('.ask-no').textContent = cancel;
    d.querySelector('.ask-yes').textContent = ok;
    d.querySelector('.ask-no').onclick = () => d.close('no');
    d.querySelector('.ask-yes').onclick = () => d.close('yes');
    d.addEventListener('click', e => { if (e.target === d) d.close('no'); });
    d.addEventListener('close', () => { resolve(d.returnValue === 'yes'); d.remove(); });
    document.body.append(d);
    d.showModal();
    // Опасное действие не подтверждается случайным Enter
    d.querySelector(danger ? '.ask-no' : '.ask-yes').focus();
  });
}
