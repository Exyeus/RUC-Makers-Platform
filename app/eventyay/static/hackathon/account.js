// Progressive enhancement: the server independently validates both registration paths.
for (const form of document.querySelectorAll('[data-unified-signup]')) {
  const identifier = form.querySelector('[name="identifier"]');
  const email = form.querySelector('[name="email"]');
  const field = email?.closest('.form-field');
  if (!identifier || !email || !field) continue;
  const update = () => {
    const emailIdentifier = identifier.value.includes('@');
    field.hidden = emailIdentifier;
    email.required = !emailIdentifier;
    email.disabled = emailIdentifier;
  };
  identifier.addEventListener('input', update);
  update();
}
