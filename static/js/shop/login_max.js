// Скрипт шаблона templates/shop/login_max.html.
//
// Вход подтверждается в мессенджере, а не здесь, поэтому страница
// спрашивает сервер раз в две секунды. Подтвердили — сервер уже
// выдал куку, остаётся перейти в кабинет.

const box = document.getElementById('maxWait');
const state = document.getElementById('maxState');
const url = '/auth/max/status?token=' + encodeURIComponent(box.dataset.token);

async function poll(){
  try {
    const d = await (await fetch(url, {cache: 'no-store'})).json();
    if (d.state === 'ok'){
      state.textContent = 'Готово, открываем кабинет…';
      state.className = 'max-state ok';
      location.href = box.dataset.next;
      return;
    }
    if (d.state === 'blocked'){
      state.textContent = d.detail;
      state.className = 'max-state bad';
      return;
    }
    if (d.state === 'expired'){
      state.innerHTML = 'Код устарел. <a href="">Получить новый</a>';
      state.className = 'max-state bad';
      return;
    }
  } catch (e) { /* сеть моргнула — спросим ещё раз */ }
  setTimeout(poll, 2000);
}
setTimeout(poll, 2000);
