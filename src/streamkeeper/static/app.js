(() => {
  const localDateFormats = {
    date: new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }),
    datetime: new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }),
    time: new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' }),
  };

  document.querySelectorAll('time[data-local]').forEach((element) => {
    const timestamp = new Date(element.dateTime);
    const formatter = localDateFormats[element.dataset.local];
    if (!formatter || Number.isNaN(timestamp.getTime())) return;
    element.textContent = formatter.format(timestamp);
    element.title = `Stored as ${timestamp.toISOString()}`;
  });

  const allPaths = [...document.querySelectorAll('.path-toggle')];
  let fullPaths = false;
  const toggleButton = document.querySelector('#toggle-paths');

  function renderPaths() {
    allPaths.forEach((path) => {
      path.textContent = fullPaths ? path.dataset.full : path.dataset.relative;
      path.title = fullPaths ? 'Show paths relative to the library' : 'Show full paths';
    });
    if (toggleButton) toggleButton.textContent = fullPaths ? 'Show relative paths' : 'Show full paths';
  }

  allPaths.forEach((path) => path.addEventListener('click', () => {
    fullPaths = !fullPaths;
    renderPaths();
  }));
  toggleButton?.addEventListener('click', () => {
    fullPaths = !fullPaths;
    renderPaths();
  });

  document.querySelectorAll('[data-href]').forEach((row) => {
    row.addEventListener('click', () => { window.location.href = row.dataset.href; });
    row.tabIndex = 0;
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') window.location.href = row.dataset.href;
    });
  });

  document.querySelectorAll('[data-dialog-open]').forEach((button) => {
    button.addEventListener('click', () => {
      if (button.dataset.dialogOpen === 'library-dialog') {
        document.querySelector('#library-form')?.reset();
        document.querySelector('#library-id').value = '';
        document.querySelector('#library-enabled').checked = true;
        document.querySelector('#library-dialog-title').textContent = 'Add library';
        document.querySelector('#library-error').textContent = '';
      }
      document.querySelector(`#${button.dataset.dialogOpen}`)?.showModal();
    });
  });
  document.querySelectorAll('[data-dialog-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });

  document.querySelectorAll('[data-library-edit]').forEach((button) => {
    button.addEventListener('click', () => {
      const library = JSON.parse(button.dataset.library);
      document.querySelector('#library-id').value = library.id;
      document.querySelector('#library-name').value = library.name;
      document.querySelector('#library-path').value = library.path;
      document.querySelector('#library-type').value = library.library_type;
      document.querySelector('#library-enabled').checked = Boolean(library.enabled);
      document.querySelector('#library-dialog-title').textContent = 'Edit library';
      document.querySelector('#library-error').textContent = '';
      document.querySelector('#library-dialog').showModal();
    });
  });

  document.querySelector('#library-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const id = document.querySelector('#library-id').value;
    const error = document.querySelector('#library-error');
    const payload = {
      name: document.querySelector('#library-name').value.trim(),
      path: document.querySelector('#library-path').value.trim(),
      library_type: document.querySelector('#library-type').value,
      enabled: document.querySelector('#library-enabled').checked ? 1 : 0,
    };
    const response = await fetch(id ? `/api/libraries/${id}` : '/api/libraries', {
      method: id ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const detail = await response.json();
      error.textContent = detail.detail || 'The library could not be saved.';
      return;
    }
    window.location.href = '/settings#libraries';
  });

  document.querySelector('#report-kind')?.addEventListener('change', (event) => {
    event.currentTarget.form.requestSubmit();
  });

  document.querySelector('#report-library-scope')?.addEventListener('change', (event) => {
    event.currentTarget.form.requestSubmit();
  });

  document.querySelector('#library-scope')?.addEventListener('change', (event) => {
    event.currentTarget.form.requestSubmit();
  });

  document.querySelector('#finding-library-scope')?.addEventListener('change', (event) => {
    event.currentTarget.form.requestSubmit();
  });

  document.querySelector('#start-scan')?.addEventListener('click', async () => {
    const error = document.querySelector('#scan-error');
    const libraryId = document.querySelector('#scan-library')?.value;
    if (!libraryId) {
      error.textContent = 'Add a library in Settings before starting a scan.';
      return;
    }
    const response = await fetch('/api/scans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ library_id: Number(libraryId), deep: document.querySelector('#scan-deep').checked }),
    });
    if (!response.ok) {
      const detail = await response.json();
      error.textContent = detail.detail || 'The scan could not start.';
      return;
    }
    window.location.href = '/scans';
  });

  document.querySelector('.finding-status')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const response = await fetch(`/api/findings/${button.dataset.finding}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: button.dataset.status }),
    });
    if (response.ok) window.location.href = button.dataset.return || '/findings';
  });

  const inspectorTabs = [...document.querySelectorAll('[data-inspector-tab]')];
  function selectInspectorTab(tab) {
    inspectorTabs.forEach((button) => {
      const selected = button === tab;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      const panel = document.querySelector(`#${button.dataset.inspectorTab}`);
      if (panel) panel.hidden = !selected;
    });
  }
  inspectorTabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectInspectorTab(tab));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const direction = event.key === 'ArrowRight' ? 1 : -1;
      const next = inspectorTabs[(index + direction + inspectorTabs.length) % inspectorTabs.length];
      selectInspectorTab(next);
      next.focus();
    });
  });

  if (document.querySelector('.active-scan')) {
    window.setTimeout(() => window.location.reload(), 4000);
  }
})();
