import { t } from './i18n.js';
import { esc, icon, clockText } from './format.js';
export async function openImporter(ui) {
  const b = ui.modal(
    t("CODEX HANDOFF", "Codex 会话"),
    t("Continue from Codex", "从 Codex 继续"),
    `<p class="lead">${t("Choose a conversation to preview on this device. The selected messages go to OpenAI when you send your next question.", "选择一个会话在本机预览。发送下一个问题时，选中的消息将一并发送给 OpenAI。")}</p>
    <div class="tabs"><button data-tab="local" class="active">${t("Local sessions", "本地会话")}</button><button data-tab="file">${t("JSONL file", "JSONL 文件")}</button><button data-tab="paste">${t("Paste text", "粘贴文本")}</button></div><div id="import-pane"></div>`,
    true,
  );
  const pane = b.querySelector('#import-pane');
  let list = null,
    tab = 'local';
  const fail = (e) => (pane.innerHTML = `<div class="callout">${esc(e.message)}</div>`);
  async function preview(messages, title) {
    if (!messages.length) {
      pane.innerHTML =
        `<div class="empty-box">${t("This file has no user messages or assistant replies to import.", "此文件没有可导入的用户消息或助手回复。")}</div><button class="hint-btn" id="reset-import">${t("Back", "返回")}</button>`;
      pane.querySelector('button').onclick = () => switchTab(tab);
      return;
    }
    const chosen = new Set(
      messages.map((_, i) => i).slice(-Math.min(32, ui.state.status.settings.context || 32)),
    );
    const body = ui.modal(
      t("IMPORT PREVIEW", "导入预览"),
      t("Choose messages to keep", "选择要保留的消息"),
      `<p class="lead">${t("Select messages to keep. Expand a row to read it in full. The preview contains user messages and assistant replies.", "选择需要保留的用户消息和助手回复。展开一行即可阅读全文。")}</p>
      <label class="form-row">${t("New chat title", "新聊天标题")}<input class="field" id="import-title" value="${esc(title.slice(0, 100))}" maxlength="100"></label>
      <div class="mini-tools"><button id="last">${t("Last 16", "最近 16 条")}</button><button id="all">${t("Select all", "全选")}</button><button id="none">${t("Clear", "清空")}</button><span id="total"></span></div><div class="preview-list" id="preview-list"></div>
      <div class="callout">${t("Your context setting controls how many of these messages accompany the next request.", "下次请求会携带多少条消息，取决于你的上下文设置。")}</div><div class="modal-actions"><button class="primary" id="commit">${t("Import and continue", "导入并继续")}</button><button class="secondary" id="back">${t("Back", "返回")}</button></div>`,
      true,
    );
    const listEl = body.querySelector('#preview-list'),
      commit = body.querySelector('#commit');
    listEl.innerHTML = messages
      .map(
        (m, i) =>
          `<div class="preview-msg"><input type="checkbox" aria-label="${t('Select message {n}', '选择第 {n} 条消息', { n: i + 1 })}" data-idx="${i}" ${chosen.has(i) ? 'checked' : ''}><details><summary><b>${m.role === 'user' ? t("You", "你") : t("Assistant", "助手")}</b>${esc(m.text.replace(/\s+/g, ' ').slice(0, 85))}</summary></details></div>`,
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
      body.querySelector('#total').textContent = t('{selected} of {total} selected', '已选择 {selected} / {total} 条', { selected: chosen.size, total: messages.length });
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
        ui.toast(t("Conversation imported. Send a question to continue.", "会话已导入，可以继续提问。"));
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
      pane.innerHTML = `<div class="muted">${t("Reading local sessions…", "正在读取本地会话…")}</div>`;
      try {
        list = list || (await ui.api('/api/codex'));
        if (tab !== 'local') return;
        pane.innerHTML =
          `<input id="find-session" type="search" placeholder="${t("Search session titles…", "搜索会话标题…")}" aria-label="${t("Search Codex sessions", "搜索 Codex 会话")}"><div class="session-list"></div><p class="form-help">${t("Relay reads the local Codex sessions and archived_sessions folders. To continue a chat from another device, use Paste text.", "Relay 会读取本机 Codex 的 sessions 和 archived_sessions 文件夹。要从其他设备继续聊天，请使用「粘贴文本」。")}</p>`;
        const draw = () => {
          const search = pane.querySelector('input').value.toLowerCase();
          const area = pane.querySelector('.session-list');
          const filtered = list.filter((x) => x.title.toLowerCase().includes(search));
          area.innerHTML = filtered.length
            ? filtered
                .map(
                  (x) =>
                    `<button class="session" data-id="${esc(x.id)}">${icon('branch')}<span>${esc(x.title)}<small>${x.archived ? t("Archived · ", "已归档 · ") : ''}${esc(clockText(x.updated))}</small></span>${icon('chevron')}</button>`,
                )
                .join('')
            : `<div class="empty-box">${t("No matching sessions. Choose a JSONL file or paste a conversation.", "没有匹配的会话。可以选择 JSONL 文件，或粘贴聊天记录。")}</div>`;
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
        `<div class="empty-box"><p>${t("Choose a Codex rollout-*.jsonl file", "选择 Codex rollout-*.jsonl 文件")}</p><p class="form-help">${t("Files are read on this device. Maximum size 24 MB.", "文件在本机读取，最大 24 MB。")}</p><input type="file" id="jsonl" accept=".jsonl,.ndjson" aria-label="${t("Choose a JSONL file", "选择 JSONL 文件")}" class="field"></div>`;
      pane.querySelector('input').onchange = async (e) => {
        const f = e.target.files[0];
        if (!f) return;
        if (f.size > 24 * 1024 * 1024)
          return ui.toast(t("This file exceeds 24 MB. Use Local sessions or paste an excerpt.", "文件超过 24 MB，请选择本地会话或粘贴片段。"));
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
        `<label class="form-row">${t("Handoff notes or conversation excerpt", "交接笔记或聊天片段")}<textarea class="field" rows="6" id="paste-source" placeholder="${t("What did you finish? Include decisions, open questions, or relevant code.", "记录已完成的工作、关键决定、待解决的问题或相关代码。")}"></textarea></label><button class="primary" id="preview-paste">${t("Preview", "预览")}</button>`;
      pane.querySelector('button').onclick = () => {
        const v = pane.querySelector('textarea').value.trim();
        if (v) preview([{ role: 'user', text: v }], t("Handoff · ", "交接记录 · ") + v.slice(0, 24));
        else ui.toast(t("Paste some text first.", "请先粘贴文字。"));
      };
    }
  }
  b.querySelectorAll('[data-tab]').forEach((x) => (x.onclick = () => switchTab(x.dataset.tab)));
  await switchTab('local');
}
