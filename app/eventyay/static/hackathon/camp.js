document.querySelectorAll('.hour-calendar').forEach((calendar) => {
  const detail = calendar.querySelector('[data-calendar-detail]');
  calendar.querySelectorAll('[data-hour-detail]').forEach((cell) => {
    const show = () => { detail.textContent = cell.dataset.hourDetail; };
    cell.addEventListener('mouseenter', show);
    cell.addEventListener('focus', show);
    cell.addEventListener('click', show);
  });
});

document.querySelectorAll('[data-hour-picker]').forEach((picker) => {
  const inputs = [...picker.querySelectorAll('input[type=checkbox]')];
  const count = picker.querySelector('[data-hour-count]');
  const refresh = () => {
    count.textContent = `已选择 ${inputs.filter((input) => input.checked).length} 个无法到场的小时`;
  };
  let selection = null;
  picker.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.pointerType === 'touch') return;
    const label = event.target.closest('.attendance-hour');
    if (!label) return;
    const input = label.querySelector('input');
    const index = inputs.indexOf(input);
    selection = { start: index, checked: !input.checked, original: inputs.map((item) => item.checked), moved: false };
    if (event.target !== input) event.preventDefault();
  });
  picker.addEventListener('pointerover', (event) => {
    if (!selection || !event.buttons) return;
    const label = event.target.closest('.attendance-hour');
    if (!label) return;
    const end = inputs.indexOf(label.querySelector('input'));
    if (end === selection.start && !selection.moved) return;
    selection.moved = true;
    inputs.forEach((input, index) => {
      input.checked = index >= Math.min(selection.start, end) && index <= Math.max(selection.start, end)
        ? selection.checked : selection.original[index];
    });
    refresh();
  });
  picker.addEventListener('click', (event) => {
    if (!selection) return;
    const moved = selection.moved;
    selection = null;
    if (moved) event.preventDefault();
    refresh();
  });
  document.addEventListener('pointerup', () => {
    if (selection?.moved) picker.dispatchEvent(new Event('change', { bubbles: true }));
    // The click event follows pointerup; retain the flag until the click has been suppressed.
    window.setTimeout(() => { selection = null; }, 0);
  });
  picker.addEventListener('change', refresh);
  picker.querySelector('[data-hour-clear]').addEventListener('click', () => {
    inputs.forEach((input) => { input.checked = false; });
    picker.dispatchEvent(new Event('change', { bubbles: true }));
  });
  refresh();
});
