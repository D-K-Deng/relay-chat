import { systemMessage } from './system-messages.js';
import { t } from './i18n.js';
import { esc, icon } from './format.js';

function allFiles(chat) {
  return chat.messages.flatMap((message, index) => message.artifacts ?? (message.workspace_activity || [])
    .filter(step => step.status === 'done' && step.diff)
    .map((step, change) => ({
      run: `saved-change-${index}-${change}`, name: step.path, source: t("Saved change", "已保存的更改"), kind: 'patch', diff: step.diff,
    })));
}
const fileKey = file => file.run + '/' + file.name;
const bytes = size => size < 1024 ? size + ' B' : size < 1024 * 1024
  ? Math.ceil(size / 1024) + ' KB' : (size / (1024 * 1024)).toFixed(1) + ' MB';

export function artifactCards(files) {
  if (!files?.length) return '';
  return '<div class="artifact-cards">' + files.map(file => file.error
    ? `<div class="artifact-error">${icon('file')}<span><strong>${esc(file.name)}</strong><small>${esc(systemMessage(file.error))}</small></span></div>`
    : `<button type="button" class="artifact-card" data-artifact="${esc(fileKey(file))}" title="${t('View', '查看')} ${esc(file.name)}">${icon('file')}<span><strong>${esc(file.name)}</strong><small>${esc(file.source === 'Data analysis' ? t('Data analysis', '数据分析') : file.source)}${file.size !== undefined ? ' · ' + bytes(file.size) : ''}</small></span>${icon('chevron')}</button>`
  ).join('') + '</div>';
}

export function drawArtifacts(chat) {
  const files = document.querySelector('[data-chat-files]');
  const count = allFiles(chat).length;
  files.title = count ? t('Chat files ({count})', '聊天文件（{count}）', { count }) : t("Files created or edited in this chat", "此聊天创建或编辑的文件");
  files.setAttribute('aria-label', files.title);
}

function localUrl(kind, chat, file) {
  return `/api/artifacts/${kind}?` + new URLSearchParams({ chat: chat.id, run: file.run, name: file.name });
}

async function fileBlob(chat, file) {
  if (file.kind === 'patch') return new Blob([file.diff], { type: 'text/plain' });
  if (file.data !== undefined) {
    const decoded = Uint8Array.from(atob(file.data), letter => letter.charCodeAt(0));
    return new Blob([decoded], { type: file.mime });
  }
  const response = await fetch(localUrl('content', chat, file));
  if (!response.ok) throw new Error((await response.json()).error);
  return new Blob([await response.arrayBuffer()], { type: file.mime });
}

function list(ui) {
  const files = allFiles(ui.state.chat).slice().reverse();
  ui.modal(t("FILES", "文件"), t("Chat files", "聊天文件"), artifactCards(files) ||
    `<p class="form-help">${t("Files Relay creates or edits appear here.", "Relay 创建或编辑的文件会显示在这里。")}</p>`);
}

async function showFile(ui, chat, file) {
  const body = ui.modal(t("CHAT FILES", "聊天文件"), file.name, `
    <div class="artifact-preview" aria-live="polite">${t("Loading preview…", "正在加载预览…")}</div>
    <div class="artifact-actions">
      <button type="button" class="primary" data-save-output>${icon('download')} ${file.kind === 'patch' ? t("Save diff…", "保存差异…") : t("Save as…", "另存为…")}</button>
      ${chat.id && file.kind !== 'patch' && /\.(xlsx|csv|pdf|txt|png|jpe?g|gif|webp)$/i.test(file.name)
        ? `<button type="button" class="secondary" data-open-output>${t("Open in app", "用本机应用打开")}</button>` : ''}
      ${chat.id && file.kind !== 'patch' ? `<button type="button" class="secondary" data-reveal-output>${t("Show in folder", "在文件夹中显示")}</button>` : ''}
      <button type="button" class="hint-btn" data-chat-files>${t("All chat files", "所有聊天文件")}</button>
    </div>`, true);
  const preview = body.querySelector('.artifact-preview');
  body.querySelector('[data-save-output]').onclick = async () => {
    try {
      const blob = await fileBlob(chat, file);
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = file.kind === 'patch' ? file.name.split(/[\\/]/).pop() + '.diff' : file.name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 60000);
    } catch (error) { ui.toast(error.message); }
  };
  for (const [selector, action] of [['[data-open-output]', 'open'], ['[data-reveal-output]', 'reveal']]) {
    const button = body.querySelector(selector);
    if (button) button.onclick = () =>
      ui.api('/api/artifacts/open', { chat: chat.id, run: file.run, name: file.name, action }).catch(error => ui.toast(error.message));
  }
  try {
    if (file.kind === 'image') {
      const blob = await fileBlob(chat, file);
      const reader = new FileReader();
      reader.onload = () => {
        const image = new Image();
        image.alt = file.name;
        image.src = reader.result;
        preview.replaceChildren(image);
      };
      reader.readAsDataURL(blob);
    } else if (file.kind === 'patch') {
      const pre = document.createElement('pre');
      pre.textContent = file.diff;
      preview.replaceChildren(pre);
    } else if (file.kind === 'table' || file.kind === 'text') {
      const result = file.data !== undefined ? await ui.api('/api/artifacts/preview', { name: file.name, data: file.data })
        : await ui.api(localUrl('preview', chat, file));
      if (result.rows) {
        preview.innerHTML = `<small>${result.sheet ? esc(result.sheet) + ' · ' : ''}${t('First 25 rows and 12 columns', '前 25 行、12 列')}</small>
          <table><tbody>${result.rows.map(row => '<tr>' + row.map(cell => `<td>${esc(cell)}</td>`).join('') + '</tr>').join('')}</tbody></table>`;
      } else {
        const pre = document.createElement('pre');
        pre.textContent = result.text;
        preview.replaceChildren(pre);
      }
    } else {
      preview.textContent = t("Save a copy to open this file in its desktop app.", "保存副本后，即可使用本机应用打开此文件。");
    }
  } catch (error) { preview.textContent = systemMessage(error.message); }
}

export function setupArtifacts(ui) {
  document.addEventListener('click', event => {
    if (event.target.closest('[data-chat-files]')) return list(ui);
    const card = event.target.closest('[data-artifact]');
    if (!card) return;
    const chat = ui.state.chat;
    const file = allFiles(chat).find(item => fileKey(item) === card.dataset.artifact);
    if (file) void showFile(ui, chat, file);
  });
}
