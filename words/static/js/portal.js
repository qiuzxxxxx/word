document.addEventListener('submit', async function (event) {
  const button = event.submitter;
  const form = event.target;
  if (!button?.dataset.confirm) return;
  if (form.dataset.confirmed === 'true') {
    delete form.dataset.confirmed;
    return;
  }
  event.preventDefault();
  if (form.dataset.confirming === 'true') return;
  form.dataset.confirming = 'true';
  try {
    if (await appConfirm(button.dataset.confirm)) {
      form.dataset.confirmed = 'true';
      form.requestSubmit(button);
    }
  } finally {
    delete form.dataset.confirming;
    delete form.dataset.confirmed;
  }
});

const editor = document.querySelector('[data-announcement-editor]');
if (editor) {
  const toggle = editor.querySelector('[data-preview-toggle]');
  const input = editor.querySelector('textarea');
  const preview = document.getElementById(toggle.getAttribute('aria-controls'));
  const token = editor.closest('form').querySelector('[name="csrfmiddlewaretoken"]').value;
  let pendingRequest;
  let previewDelay;

  async function refreshPreview() {
    pendingRequest?.abort();
    const request = new AbortController();
    pendingRequest = request;
    if (!input.value.trim()) {
      preview.removeAttribute('aria-busy');
      preview.textContent = '输入公告正文后，这里会显示预览。';
      return;
    }
    preview.setAttribute('aria-busy', 'true');
    try {
      const result = await fetch(editor.dataset.previewUrl, {
        method: 'POST', headers: { 'X-CSRFToken': token },
        body: new URLSearchParams({ body: input.value }), signal: request.signal,
      });
      const payload = await result.json();
      if (request.signal.aborted) return;
      if (result.ok) {
        preview.innerHTML = payload.html;
      } else {
        preview.textContent = payload.error || '预览暂时不可用，请稍后重试。';
      }
    } catch (error) {
      if (!request.signal.aborted) preview.textContent = '预览暂时不可用，请稍后重试。';
    } finally {
      if (pendingRequest === request) preview.removeAttribute('aria-busy');
    }
  }

  toggle.addEventListener('click', function () {
    const open = preview.hidden;
    preview.hidden = !open;
    editor.toggleAttribute('data-preview-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.querySelector('span').textContent = open ? '关闭预览' : '预览';
    if (open) refreshPreview();
    else {
      clearTimeout(previewDelay);
      pendingRequest?.abort();
      preview.removeAttribute('aria-busy');
    }
  });

  input.addEventListener('input', function () {
    if (preview.hidden) return;
    clearTimeout(previewDelay);
    pendingRequest?.abort();
    previewDelay = setTimeout(refreshPreview, 200);
  });
}
