// The header's "Data and methods" menu is a details element, so it opens and closes with no script at all. This adds
// what people expect of a menu: Escape closes it and returns to its button, and it closes after one of its links is
// followed on the same page, when a click or tap lands elsewhere, or when focus moves on to something outside it.
for (const menu of document.querySelectorAll('.nav-menu')) {
  const button = menu.querySelector('summary');
  const close = () => { menu.open = false; };
  menu.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !menu.open) return;
    close();
    button.focus();
  });
  menu.addEventListener('click', e => { if (e.target.closest('a')) close(); });
  // Only when focus lands on something else: Safari does not focus a link it is clicking, so a null target is a click
  // in progress inside the menu.
  menu.addEventListener('focusout', e => { if (e.relatedTarget && !menu.contains(e.relatedTarget)) close(); });
  document.addEventListener('click', e => { if (menu.open && !menu.contains(e.target)) close(); });
}
