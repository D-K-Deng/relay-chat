import { esc, icon, clockText } from './format.js';
export async function openImporter(ui) {
  const b = ui.modal(
    'CODEX HANDOFF',
    'Continue from Codex',
    `<p class="lead">Choose a conversation to preview on this device. The selected messages go to OpenAI when you send your next question.</p>
    <div class="tabs"><button data-tab="local" class="active">Local sessions</button><button data-tab="file">JSONL file</button><button data-tab="paste">Paste text</button></div><div id="import-pane"></div>`,
    true,
  );
  const pane = b.querySelector('#import-pane');
  let list = null,
    tab = 'local';
  const fail = (e) => (pane.innerHTML = `<div class="callout">${esc(e.message)}</div>`);
  async function preview(messages, title) {
    if (!messages.length) {
      pane.innerHTML =
        '<div class="empty-box">This file has no user messages or assistant replies to import.</div><button class="hint-btn" id="reset-import">Back</button>';
      pane.querySelector('button').onclick = () => switchTab(tab);
      return;
    }
    const chosen = new Set(
      messages.map((_, i) => i).slice(-Math.min(32, ui.state.status.settings.context || 32)),
    );
    const body = ui.modal(
      'IMPORT PREVIEW',
      'Choose messages to keep',
      `<p class="lead">Select messages to keep. Expand a row to read it in full. The preview contains user messages and assistant replies.</p>
      <label class="form-row">New chat title<input class="field" id="import-title" value="${esc(title.slice(0, 100))}" maxlength="100"></label>
      <div class="mini-tools"><button id="last">Last 16</button><button id="all">Select all</button><button id="none">Clear</button><span id="total"></span></div><div class="preview-list" id="preview-list"></div>
      <div class="callout">Your context setting controls how many of these messages accompany the next request.</div><div class="modal-actions"><button class="primary" id="commit">Import and continue</button><button class="secondary" id="back">Back</button></div>`,
      true,
    );
    const listEl = body.querySelector('#preview-list'),
      commit = body.querySelector('#commit');
    listEl.innerHTML = messages
      .map(
        (m, i) =>
          `<div class="preview-msg"><input type="checkbox" aria-label="Select message ${i + 1}" data-idx="${i}" ${chosen.has(i) ? 'checked' : ''}><details><summary><b>${m.role === 'user' ? 'You' : 'Assistant'}</b>${esc(m.text.replace(/\s+/g, ' ').slice(0, 85))}</summary></details></div>`,
      )
      .join('');
    listEl.addEventListener(
      'toggle',
      (e) => {
        if (e.target.tagName === 'DETAILS' && e.target.open && !e.target.querySelector('pre')) {
          const pre = document.createElement('pre');
          pre.textContent =
            messages[Number(e.target.parentNode.querySelector('input').dataset.idx)].text;
          e.target.append(pre);
        }
      },
      true,
    );
    const count = () => {
      body.querySelector('#total').textContent = `${chosen.size} of ${messages.length} selected`;
      commit.disabled = !chosen.size;
    };
    count();
    listEl.onchange = (e) => {
      const i = Number(e.target.dataset.idx);
      if (e.target.checked) chosen.add(i);
      else chosen.delete(i);
      count();
    };
    const select = (mode) => {
      chosen.clear();
      listEl.querySelectorAll('input').forEach((el, i) => {
        el.checked = mode === 'all' || (mode === 'last' && i >= messages.length - 16);
        if (el.checked) chosen.add(i);
      });
      count();
    };
    ['last', 'all', 'none'].forEach((k) => (body.querySelector('#' + k).onclick = () => select(k)));
    body.querySelector('#back').onclick = () => openImporter(ui);
    commit.onclick = async () => {
      commit.disabled = true;
      try {
        const { id } = await ui.api('/api/import', {
          title: body.querySelector('#import-title').value.trim() || title,
          messages: messages.filter((_, i) => chosen.has(i)),
        });
        await ui.openChat(id);
        await ui.listChats();
        ui.close();
        ui.toast('Conversation imported. Send a question to continue.');
      } catch (e) {
        ui.toast(e.message);
        commit.disabled = false;
      }
    };
  }
  async function switchTab(next) {
    tab = next;
    b.querySelectorAll('[data-tab]').forEach((x) =>
      x.classList.toggle('active', x.dataset.tab === tab),
    );
    if (tab === 'local') {
      pane.innerHTML = '<div class="muted">Reading local sessions…</div>';
      try {
        list = list || (await ui.api('/api/codex'));
        if (tab !== 'local') return;
        pane.innerHTML =
          '<input id="find-session" type="search" placeholder="Search session titles…" aria-label="Search Codex sessions"><div class="session-list"></div><p class="form-help">Relay reads the local Codex sessions and archived_sessions folders. To continue a chat from another device, use Paste text.</p>';
        const draw = () => {
          const search = pane.querySelector('input').value.toLowerCase();
          const area = pane.querySelector('.session-list');
          const filtered = list.filter((x) => x.title.toLowerCase().includes(search));
          area.innerHTML = filtered.length
            ? filtered
                .map(
                  (x) =>
                    `<button class="session" data-id="${esc(x.id)}">${icon('branch')}<span>${esc(x.title)}<small>${x.archived ? 'Archived · ' : ''}${esc(clockText(x.updated))}</small></span>${icon('chevron')}</button>`,
                )
                .join('')
            : '<div class="empty-box">No matching sessions. Choose a JSONL file or paste a conversation.</div>';
        };
        draw();
        pane.querySelector('input').oninput = draw;
        pane.querySelector('.session-list').onclick = async (e) => {
          const btn = e.target.closest('[data-id]');
          if (!btn) return;
          const entry = list.find((x) => x.id === btn.dataset.id);
          btn.disabled = true;
          try {
            const result = await ui.api('/api/codex/' + entry.id);
            if (tab === 'local' && b.contains(btn) && document.querySelector('#dialog').open)
              preview(result.messages, entry.title);
          } catch (error) {
            ui.toast(error.message);
          } finally {
            btn.disabled = false;
          }
        };
      } catch (e) {
        if (tab === 'local') fail(e);
      }
    } else if (tab === 'file') {
      pane.innerHTML =
        '<div class="empty-box"><p>Choose a Codex rollout-*.jsonl file</p><p class="form-help">Files are read on this device. Maximum size 24 MB.</p><input type="file" id="jsonl" accept=".jsonl,.ndjson" aria-label="Choose a JSONL file" class="field"></div>';
      pane.querySelector('input').onchange = async (e) => {
        const f = e.target.files[0];
        if (!f) return;
        if (f.size > 24 * 1024 * 1024)
          return ui.toast('This file exceeds 24 MB. Use Local sessions or paste an excerpt.');
        try {
          const result = await ui.api('/api/codex/parse', { jsonl: await f.text() });
          if (tab === 'file' && document.querySelector('#dialog').open)
            preview(result.messages, 'Codex · ' + f.name.replace(/\.jsonl$/i, ''));
        } catch (err) {
          ui.toast(err.message);
        }
      };
    } else {
      pane.innerHTML =
        '<label class="form-row">Handoff notes or conversation excerpt<textarea class="field" rows="6" id="paste-source" placeholder="What did you finish? Include decisions, open questions, or relevant code."></textarea></label><button class="primary" id="preview-paste">Preview</button>';
      pane.querySelector('button').onclick = () => {
        const v = pane.querySelector('textarea').value.trim();
        if (v) preview([{ role: 'user', text: v }], 'Handoff · ' + v.slice(0, 24));
        else ui.toast('Paste some text first.');
      };
    }
  }
  b.querySelectorAll('[data-tab]').forEach((x) => (x.onclick = () => switchTab(x.dataset.tab)));
  await switchTab('local');
}
