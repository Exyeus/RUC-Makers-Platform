// Live countdowns for the Swiss pages. Server time is the reference: the page
// carries the server's "now", so a wrong device clock cannot move a deadline.
const blocks = [...document.querySelectorAll('[data-sw-countdown]')];

function pad(value) {
  return String(value).padStart(2, '0');
}

function render(block, offset) {
  const target = Date.parse(block.dataset.swCountdown);
  if (Number.isNaN(target)) return false;
  const left = Math.max(0, Math.floor((target - (Date.now() + offset)) / 1000));
  const main = block.querySelector('[data-sw-main]');
  const tail = block.querySelector('[data-sw-tail]');
  if (left === 0) {
    if (main) main.textContent = block.dataset.swDoneText || '已截止';
    if (tail) tail.textContent = '';
    return false;
  }
  const hours = Math.floor(left / 3600);
  const minutes = Math.floor((left % 3600) / 60);
  const seconds = left % 60;
  if (main) main.textContent = hours >= 100 ? `${Math.floor(hours / 24)} 天` : `${pad(hours)}:${pad(minutes)}`;
  if (tail) tail.textContent = hours >= 100 ? ` ${pad(hours % 24)} 时` : `:${pad(seconds)}`;
  return true;
}

if (blocks.length) {
  const serverNow = Date.parse(document.documentElement.dataset.swServerNow || '');
  const offset = Number.isNaN(serverNow) ? 0 : serverNow - Date.now();
  const tick = () => {
    const alive = blocks.filter((block) => render(block, offset));
    if (alive.length) window.setTimeout(tick, 1000 - (Date.now() % 1000) + 5);
  };
  tick();
}
