const sidebar = document.querySelector('#sidebar');
const workspace = document.querySelector('.workspace');
const quickNav = document.querySelector('.mobile-nav');
const backdrop = document.querySelector('.nav-backdrop');
const toggles = [...document.querySelectorAll('[data-nav-toggle]')];
const compact = window.matchMedia('(max-width: 1199px)');
let returnFocus = null;

if (sidebar && workspace && backdrop) {
  const focusable = () => [...sidebar.querySelectorAll('a[href], button:not(:disabled)')]
    .filter(element => element.getClientRects().length);
  const setOpen = (open, restoreFocus = true) => {
    open = open && compact.matches;
    sidebar.classList.toggle('open', open);
    sidebar.inert = compact.matches && !open;
    workspace.inert = open;
    if (quickNav) quickNav.inert = open;
    backdrop.hidden = !open;
    document.body.classList.toggle('nav-open', open);
    toggles.forEach(toggle => toggle.setAttribute('aria-expanded', String(open)));
    if (open) {
      sidebar.setAttribute('role', 'dialog');
      sidebar.setAttribute('aria-modal', 'true');
      sidebar.querySelector('[data-nav-close]').focus();
    } else {
      sidebar.removeAttribute('role');
      sidebar.removeAttribute('aria-modal');
      if (restoreFocus && returnFocus?.getClientRects().length) returnFocus.focus();
    }
  };
  toggles.forEach(toggle => toggle.addEventListener('click', () => {
    returnFocus = toggle;
    setOpen(!sidebar.classList.contains('open'));
  }));
  document.querySelectorAll('[data-nav-close]').forEach(button => {
    button.addEventListener('click', () => setOpen(false));
  });
  document.addEventListener('keydown', event => {
    if (!sidebar.classList.contains('open')) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === 'Tab') {
      const items = focusable();
      const first = items[0];
      const last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
  });
  compact.addEventListener('change', () => setOpen(false, false));
  window.addEventListener('pageshow', () => setOpen(false, false));
  setOpen(false, false);
}

// Keep the same cells, forms, and event handlers when displaying table rows as cards.
document.querySelectorAll('.table-panel table').forEach(table => {
  const headings = [...table.querySelectorAll('thead th')];
  if (!headings.length) return;
  table.classList.add('responsive-table');
  table.setAttribute('role', 'table');
  table.querySelectorAll('thead, tbody').forEach(section => section.setAttribute('role', 'rowgroup'));
  table.querySelectorAll('tr').forEach(row => row.setAttribute('role', 'row'));
  headings.forEach(heading => {
    heading.scope = 'col';
    heading.setAttribute('role', 'columnheader');
  });
  table.querySelectorAll('tbody tr').forEach(row => {
    [...row.cells].forEach((cell, index) => {
      cell.setAttribute('role', 'cell');
      if (cell.colSpan > 1 || !headings[index]) return;
      const content = document.createElement('div');
      content.className = 'table-cell-content';
      content.append(...cell.childNodes);
      const label = document.createElement('span');
      label.className = 'mobile-cell-label';
      label.setAttribute('aria-hidden', 'true');
      label.textContent = headings[index].textContent.trim();
      cell.append(label, content);
    });
  });
});
