/**
 * DataHunt Settings Modal Controller
 * Provides an interactive UI to inspect and modify agent and pipeline settings,
 * ensuring all updates are persisted to SQLite database and .env across shutdowns and restarts.
 */

(function () {
  'use strict';

  let currentSettingsData = null;

  // Build and inject modal HTML structure into DOM
  function injectModalHtml() {
    if (document.getElementById('dh-settings-modal-backdrop')) return;

    const modalHtml = `
      <div id="dh-settings-modal-backdrop" class="settings-modal-backdrop">
        <div class="settings-modal-dialog">
          <div class="settings-modal-header">
            <div>
              <div class="settings-modal-title">
                <span>⚙️</span>
                <span>DATAHUNT CONFIGURATION &amp; PERSISTENCE</span>
              </div>
              <div class="settings-modal-subtitle">PERSISTED TO SQLITE &amp; .ENV • RETAINED ACROSS REBOOTS &amp; CLOSURES</div>
            </div>
            <button id="dh-settings-close-btn" class="settings-close-btn" title="Close Settings">✕</button>
          </div>

          <form id="dh-settings-form" class="settings-modal-body">
            <!-- Dynamic Category Sections Rendered Here -->
            <div id="dh-settings-loading" style="text-align: center; padding: 40px; color: #00f0ff; font-family: 'Share Tech Mono', monospace;">
              LOADING AGENT CONFIGURATION...
            </div>
          </form>

          <div class="settings-modal-footer">
            <div class="settings-status-indicator" id="dh-settings-status">
              <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#00ffa3; box-shadow:0 0 6px #00ffa3;"></span>
              <span>STORAGE: SQLITE WAL + .ENV DUAL-SYNC</span>
            </div>
            <div class="settings-action-btns">
              <button type="button" id="dh-settings-purge-data-btn" class="settings-btn" style="background: rgba(255, 68, 68, 0.15); border: 1px solid rgba(255, 68, 68, 0.5); color: #ff5555;" title="Permanently delete all scraped jobs and purge database">
                <span>🗑️ PURGE ALL DATA</span>
              </button>
              <button type="button" id="dh-settings-reset-btn" class="settings-btn settings-btn-reset">
                <span>↺ RESTORE DEFAULTS</span>
              </button>
              <button type="button" id="dh-settings-save-btn" class="settings-btn settings-btn-save">
                <span>💾 SAVE SETTINGS</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div id="dh-settings-toast" class="settings-toast">
        <span id="dh-settings-toast-icon">✓</span>
        <span id="dh-settings-toast-msg">Settings saved successfully</span>
      </div>
    `;

    const container = document.createElement('div');
    container.innerHTML = modalHtml;
    while (container.firstChild) {
      document.body.appendChild(container.firstChild);
    }

    // Attach listeners for modal close
    document.getElementById('dh-settings-close-btn').addEventListener('click', closeSettingsModal);
    document.getElementById('dh-settings-modal-backdrop').addEventListener('click', (e) => {
      if (e.target.id === 'dh-settings-modal-backdrop') {
        closeSettingsModal();
      }
    });

    document.getElementById('dh-settings-save-btn').addEventListener('click', saveSettings);
    document.getElementById('dh-settings-reset-btn').addEventListener('click', resetSettings);
    const purgeBtn = document.getElementById('dh-settings-purge-data-btn');
    if (purgeBtn) purgeBtn.addEventListener('click', purgeAllData);

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const backdrop = document.getElementById('dh-settings-modal-backdrop');
        if (backdrop && backdrop.classList.contains('active')) {
          closeSettingsModal();
        }
      }
    });
  }

  function showToast(msg, isError = false) {
    const toast = document.getElementById('dh-settings-toast');
    const toastMsg = document.getElementById('dh-settings-toast-msg');
    const toastIcon = document.getElementById('dh-settings-toast-icon');
    if (!toast) return;

    toastMsg.textContent = msg;
    if (isError) {
      toast.style.borderColor = '#ff3366';
      toast.style.color = '#ff3366';
      toast.style.boxShadow = '0 0 20px rgba(255, 51, 102, 0.3)';
      toastIcon.textContent = '✕';
           } else if (field.type === 'boolean') {
             const input = document.createElement('input');
             input.id = `dh-setting-${field.key}`;
             input.name = field.key;
             input.className = 'settings-input';
             input.type = 'checkbox';
             input.checked = field.value === true || String(field.value).toLowerCase() === 'true';
             group.appendChild(input);
           } else {
      toast.style.borderColor = '#00ffa3';
      toast.style.color = '#00ffa3';
      toast.style.boxShadow = '0 0 20px rgba(0, 255, 163, 0.3)';
      toastIcon.textContent = '✓';
    }

    toast.style.display = 'flex';
    setTimeout(() => {
      toast.style.display = 'none';
    }, 3500);
  }

  async function openSettingsModal() {
    injectModalHtml();
    const backdrop = document.getElementById('dh-settings-modal-backdrop');
    backdrop.classList.add('active');

    const form = document.getElementById('dh-settings-form');
    form.innerHTML = '<div style="text-align: center; padding: 40px; color: #00f0ff; font-family: \'Share Tech Mono\', monospace;">SYNCING SYSTEM SETTINGS...</div>';

    try {
      const resp = await fetch('/api/settings');
      if (!resp.ok) throw new Error('Failed to load settings');
      const data = await resp.json();
      currentSettingsData = data;
      renderSettingsForm(data);
    } catch (err) {
      console.error(err);
      form.innerHTML = `<div style="text-align: center; padding: 40px; color: #ff3366; font-family: 'Share Tech Mono', monospace;">ERROR LOADING SETTINGS: ${err.message}</div>`;
    }
  }

  function closeSettingsModal() {
    const backdrop = document.getElementById('dh-settings-modal-backdrop');
    if (backdrop) {
      backdrop.classList.remove('active');
    }
  }

  function renderSettingsForm(data) {
    const form = document.getElementById('dh-settings-form');
    form.innerHTML = '';

    const fields = data.fields || [];
    // Group fields by category
    const categories = {};
    fields.forEach((field) => {
      const cat = field.category || 'General';
      if (!categories[cat]) categories[cat] = [];
      categories[cat].push(field);
    });

    Object.keys(categories).forEach((catName) => {
      const section = document.createElement('div');
      section.className = 'settings-category';

      const title = document.createElement('div');
      title.className = 'settings-category-title';
      title.textContent = catName.toUpperCase();
      section.appendChild(title);

      const grid = document.createElement('div');
      grid.className = 'settings-grid';

      categories[catName].forEach((field) => {
        const group = document.createElement('div');
        group.className = 'settings-field-group';
        if (field.type === 'text' && field.key.includes('model')) {
          group.classList.add('full-width');
        }

        const label = document.createElement('label');
        label.className = 'settings-label';
        label.setAttribute('for', `dh-setting-${field.key}`);
        label.innerHTML = `<span>${field.label}</span>`;
        group.appendChild(label);

        if (field.type === 'select') {
          const select = document.createElement('select');
          select.id = `dh-setting-${field.key}`;
          select.name = field.key;
          select.className = 'settings-select';
          (field.options || []).forEach((opt) => {
            const optEl = document.createElement('option');
            optEl.value = opt;
            optEl.textContent = opt.toUpperCase();
            if (String(opt).toLowerCase() === String(field.value).toLowerCase()) {
              optEl.selected = true;
            }
            select.appendChild(optEl);
          });
          group.appendChild(select);
       } else if (input.type === 'checkbox') {
         updates[name] = input.checked;
       } else {
          const input = document.createElement('input');
          input.id = `dh-setting-${field.key}`;
          input.name = field.key;
          input.className = 'settings-input';
          input.type = field.type === 'number' ? 'number' : 'text';
          input.value = field.value !== undefined ? field.value : '';
          if (field.type === 'number') {
            input.min = field.key.includes('drain') ? '0' : '1';
            input.step = field.key.includes('seconds') ? '30' : '1';
          }
          group.appendChild(input);
        }

        if (field.help) {
          const help = document.createElement('div');
          help.className = 'settings-help';
          help.textContent = field.help;
          group.appendChild(help);
        }

        grid.appendChild(group);
      });

      section.appendChild(grid);
      form.appendChild(section);
    });
  }

  async function saveSettings() {
    const form = document.getElementById('dh-settings-form');
    if (!form) return;

    const saveBtn = document.getElementById('dh-settings-save-btn');
    const originalText = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span>SAVING...</span>';

    const updates = {};
    const inputs = form.querySelectorAll('input, select');
    inputs.forEach((input) => {
      const name = input.name;
      if (!name) return;
      if (input.type === 'number') {
        updates[name] = parseInt(input.value, 10);
      } else {
        updates[name] = input.value;
      }
    });

    try {
      const resp = await fetch('/api/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ settings: updates }),
      });

      if (!resp.ok) {
        const errJson = await resp.json().catch(() => ({}));
        throw new Error(errJson.detail || 'Failed to save settings');
      }

      const resData = await resp.json();
      showToast('✓ Settings persisted across restarts and reboots');
      setTimeout(closeSettingsModal, 600);
    } catch (err) {
      console.error(err);
      showToast(err.message, true);
    } finally {
      saveBtn.disabled = false;
      saveBtn.innerHTML = originalText;
    }
  }

  async function resetSettings() {
    if (!confirm('Are you sure you want to restore all settings to default configuration?')) {
      return;
    }

    const resetBtn = document.getElementById('dh-settings-reset-btn');
    const originalText = resetBtn.innerHTML;
    resetBtn.disabled = true;
    resetBtn.innerHTML = '<span>RESETTING...</span>';

    try {
      const resp = await fetch('/api/settings/reset', {
        method: 'POST',
      });
      if (!resp.ok) throw new Error('Failed to reset settings');
      const resData = await resp.json();
      showToast('✓ Settings restored to factory defaults');
      // Refresh form with new defaults
      await openSettingsModal();
    } catch (err) {
      console.error(err);
      showToast(err.message, true);
    } finally {
      resetBtn.disabled = false;
      resetBtn.innerHTML = originalText;
    }
  }

  async function purgeAllData() {
    if (!confirm('⚠️ COMPLETE RESET:\n\nAre you sure you want to permanently delete ALL scraped jobs, research runs, raw documents, and free all disk storage?')) {
      return;
    }

    const purgeBtn = document.getElementById('dh-settings-purge-data-btn');
    const originalText = purgeBtn ? purgeBtn.innerHTML : '';
    if (purgeBtn) {
      purgeBtn.disabled = true;
      purgeBtn.innerHTML = '<span>PURGING...</span>';
    }

    try {
      const resp = await fetch('/api/jobs/reset', { method: 'POST' });
      if (!resp.ok) throw new Error('Failed to purge data');
      showToast('✓ Complete reset successful. All database records and caches purged.');
      if (typeof window.loadJobs === 'function') {
        window.loadJobs();
      }
    } catch (err) {
      console.error(err);
      showToast(err.message, true);
    } finally {
      if (purgeBtn) {
        purgeBtn.disabled = false;
        purgeBtn.innerHTML = originalText;
      }
    }
  }

  // Bind trigger buttons when DOM is loaded
  document.addEventListener('DOMContentLoaded', () => {
    injectModalHtml();

    // Bind any existing or future settings buttons
    document.querySelectorAll('#open-settings-btn, .open-settings-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        openSettingsModal();
      });
    });
  });

  // Export methods globally for inline triggers or external scripts
  window.DataHuntSettings = {
    open: openSettingsModal,
    close: closeSettingsModal,
  };
})();
