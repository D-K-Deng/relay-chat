import { esc, icon, paint, markdown } from './format.js';
import { openGuide, openSettings } from './guide.js';
import { openBalance, showBalanceState } from './balance.js';
import { openImporter } from './importer.js';

const $ = (s) => document.querySelector(s);
const blank = (temporary = false) => ({
  id: null,
  title: temporary ? 'Temporary chat' : 'New chat',
  source: 'local',
  temporary,
  messages: [],
});
const S = {
  chat: blank(),
  temp: blank(true),
  files: [],
  busy: false,
  run: null,
  status: {
    configured: false,
    settings: { model: 'gpt-5.4-mini', context: 32, max_output: 4096, instructions: '' },
  },
  list: [],
};
const drafts = new Map();
let toastTimer,
  searchTimer,
  listVersion = 0,
  frame = 0,
  wantStop = false;
const dkey = () => (S.chat.temporary ? 'temporary' : S.chat.id || 'new');
function toast(message) {
  const t = $('#toast');
  t.textContent = message;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 3500);
}
async function api(path, data) {
  const r = await fetch(
    path,
    data === undefined
      ? { cache: 'no-store' }
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        },
  );
  const result = await r.json();
  if (!r.ok) {
    const e = new Error(result.error || 'The local request failed.');
    e.code = result.code;
    throw e;
  }
  return result;
}
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied to clipboard.');
  } catch {
    toast('Select the text and copy it manually. Clipboard access is unavailable.');
  }
}
function modal(kicker, title, html, wide = false) {
  const d = $('#dialog');
  d.classList.toggle('wide', wide);
  $('#modal-kicker').textContent = kicker;
  $('#modal-title').textContent = title;
  const b = $('#modal-body');
  b.innerHTML = html;
  b.scrollTop = 0;
  if (!d.open) d.showModal();
  return b;
}
function close() {
  if ($('#dialog').open) $('#dialog').close();
}
function theme() {
  const setting = localStorage.getItem('relay.theme') || 'system';
  document.documentElement.dataset.theme =
    setting === 'system'
      ? matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : setting;
}
const creditChannel = 'BroadcastChannel' in window ? new BroadcastChannel('relay-credits') : null;
async function refreshBalance() {
  const state = await api('/api/balance');
  S.balance = state;
  showBalanceState(state);
  return state;
}
async function balanceChanged() {
  await refreshBalance();
  creditChannel?.postMessage('changed');
}
if (creditChannel) creditChannel.onmessage = () => refreshBalance().catch(() => {});
setInterval(() => {
  if (!document.hidden && S.balance?.meter) refreshBalance().catch(() => {});
}, 15000);
async function refresh() {
  S.status = await api('/api/status');
  const el = $('#connection');
  el.classList.toggle('ready', S.status.configured);
  el.querySelector('b').textContent = S.status.configured ? 'Key found' : 'Set up API';
  el.dataset.action = S.status.configured ? 'settings' : 'guide';
  $('#model span').textContent = S.status.settings.model;
  note();
  await refreshBalance();
}
async function listChats() {
  const v = ++listVersion;
  const q = $('#history-search').value;
  const rows = await api('/api/chats?q=' + encodeURIComponent(q));
  if (v !== listVersion) return;
  S.list = rows;
  drawList();
}
function drawList() {
  $('#history').innerHTML = S.list.length
    ? S.list
        .map(
          (c) =>
            `<button class="history-item ${c.id === S.chat.id ? 'active' : ''}" data-chat="${esc(c.id)}" title="${esc(c.title)}">${icon(c.pinned ? 'pin' : c.source === 'codex' ? 'branch' : 'chat')}<span>${esc(c.title)}</span></button>`,
        )
        .join('')
    : `<p class="history-empty">${$('#history-search').value ? 'No matching chats' : 'No conversations yet'}<span>${$('#history-search').value ? 'Try another search term.' : 'Your saved chats will appear here.'}</span></p>`;
}
function note() {
  const limit = S.status.settings.context;
  const msg = S.chat.temporary
    ? 'Temporary chat · Clears when you refresh or close this tab'
    : S.chat.messages.length
      ? `Saved locally · Next request includes ${limit ? 'up to ' + limit : 'all'} messages`
      : 'Saved on this device · Uses your OpenAI API credits';
  $('#compose-note span').textContent = msg;
}
function safeFiles(files) {
  if (!files?.length) return '';
  return (
    '<div class="tag-row">' +
    files
      .map(
        (f) =>
          `<span class="file-tag">${f.kind === 'image' && /^data:image\/(png|jpeg|webp|gif);base64,/.test(f.data) ? `<img src="${esc(f.data)}" alt="${esc(f.name)}">` : icon('file')}${esc(f.name)}</span>`,
      )
      .join('') +
    '</div>'
  );
}
function msgHtml(m, i) {
  if (m.role === 'user')
    return `<article class="msg user"><div class="bubble">${esc(m.text)}${safeFiles(m.files)}</div>${!S.busy && i === S.chat.messages.length - 1 ? '<div class="meta"><button data-action="retry">' + icon('refresh') + ' Answer this message</button></div>' : ''}</article>`;
  const pending = m.status === 'pending';
  const units = m.usage
    ? `<span>${Number(m.usage.input_tokens || 0).toLocaleString('en-US')} input · ${Number(m.usage.output_tokens || 0).toLocaleString('en-US')} output tokens</span>`
    : '';
  const allowRetry =
    !S.busy && i === S.chat.messages.length - 1 && S.chat.messages[i - 1]?.role === 'user';
  return `<article class="msg assistant"><div class="speaker"><img src="/mark.svg" alt="">Relay <small>${esc(m.model || '')}</small></div><div class="answer">${markdown(m.text)}${pending ? '<span class="typing" aria-label="Generating"></span>' : ''}</div>${m.note ? `<p class="notice ${m.status === 'error' ? 'bad' : ''}">${esc(m.note)}</p>` : ''}<div class="meta">${m.text && !pending ? `<button data-copy="${i}" title="Copy response" aria-label="Copy response">${icon('copy')}</button>` : ''}${allowRetry ? `<button data-action="retry">${icon('refresh')} Regenerate</button>` : ''}${units}</div></article>`;
}
function draw(scroll = false) {
  const c = S.chat,
    empty = !c.messages.length;
  $('#stage').classList.toggle('empty', empty);
  $('#landing').hidden = $('#shortcuts').hidden = !empty;
  $('#thread').hidden = empty;
  $('#hero-title').innerHTML = c.temporary
    ? 'A chat for now<span>.</span>'
    : 'Where shall we start<span>?</span>';
  $('#hero-copy').textContent = c.temporary
    ? 'Ask follow-up questions here. Refresh or close the tab to clear this conversation.'
    : 'Start a new conversation or pick up where you left off in Codex.';
  $('#hero-label').textContent = c.temporary ? 'Temporary workspace' : 'Personal workspace';
  $('#second-card').dataset.action = c.temporary ? 'new' : 'temp';
  $('#second-card strong').textContent = c.temporary ? 'Saved chat' : 'Temporary chat';
  $('#second-card small').textContent = c.temporary ? 'Keep a local history' : 'Clears when you close this tab';
  $('#crumb').textContent = c.title;
  $('#more').hidden = !c.id && !(c.temporary && c.messages.length);
  $('#nav-temp').classList.toggle('active', !!c.temporary);
  $('#messages').innerHTML = c.messages.map(msgHtml).join('');
  $('#draft').rows = empty ? 2 : 1;
  resize();
  drawList();
  drawChips();
  note();
  button();
  if (scroll) $('#thread').scrollTop = $('#thread').scrollHeight;
}
function resize() {
  const el = $('#draft');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 220) + 'px';
}
function drawChips() {
  $('#chips').innerHTML = S.files
    .map(
      (f, i) =>
        `<span class="chip">${icon('file')}${esc(f.name)}<button data-remove="${i}" aria-label="Remove attachment ${esc(f.name)}">${icon('x')}</button></span>`,
    )
    .join('');
}
function button() {
  const el = $('#send');
  el.innerHTML = icon(S.busy ? 'stop' : 'up');
  el.disabled = !S.busy && !($('#draft').value.trim() || S.files.length);
  el.title = S.busy ? 'Stop generating' : 'Send message';
  el.setAttribute('aria-label', el.title);
}
function busyGuard() {
  if (!S.busy) return false;
  toast('Stop the current response or wait for it to finish.');
  return true;
}
function swap(c) {
  drafts.set(dkey(), { value: $('#draft').value, files: S.files });
  S.chat = c;
  const draft = drafts.get(dkey()) || { value: '', files: [] };
  S.files = draft.files;
  $('#draft').value = draft.value;
  history.replaceState(
    null,
    '',
    c.id ? '#chat/' + c.id : c.temporary ? '#temporary' : location.pathname,
  );
  $('#layout').classList.remove('mobile-nav');
  draw(true);
  $('#draft').focus({ preventScroll: true });
}
async function openChat(id) {
  if (busyGuard()) return;
  const c = await api('/api/chats/' + encodeURIComponent(id));
  swap(c);
}
const ui = { state: S, api, modal, close, toast, copy, refresh, refreshBalance, balanceChanged, theme, openChat, listChats };
function updateAnswer() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    if (!S.busy) return;
    const m = S.chat.messages.at(-1),
      el = $('#messages .msg:last-child .answer');
    if (!el || m?.status !== 'pending') return;
    const t = $('#thread'),
      near = t.scrollHeight - t.clientHeight - t.scrollTop < 110;
    el.innerHTML = markdown(m.text) + '<span class="typing" aria-label="Generating"></span>';
    if (near) t.scrollTop = t.scrollHeight;
  });
}
async function stop() {
  wantStop = true;
  if (!S.run) return;
  try {
    await api('/api/stop', { run: S.run });
  } catch (err) {
    toast(err.message);
  }
}
async function send(retry = false) {
  if (S.busy) {
    if (!retry) await stop();
    return;
  }
  const text = $('#draft').value.trim(),
    files = S.files;
  if (!retry && !text && !files.length) return;
  const c = S.chat;
  const limit = S.status.settings.context;
  const previous = limit ? c.messages.slice(-(retry ? limit + 1 : limit - 1)) : c.messages;
  const history = c.temporary
    ? previous.map(({ role, text, files }) => ({ role, text, files }))
    : undefined;
  const payload = { id: c.id, temporary: !!c.temporary, history, retry, message: { text, files } };
  S.busy = true;
  S.run = null;
  wantStop = false;
  button();
  $('#draft').readOnly = true;
  $('#attach').disabled = true;
  let started = false,
    finished = false;
  function handle(e) {
    if (e.type === 'start') {
      started = true;
      S.run = e.run;
      if (wantStop) void stop();
      if (retry && c.messages.at(-1)?.role === 'assistant') c.messages.pop();
      if (!retry) {
        const oldKey = dkey();
        c.messages.push({ role: 'user', text, files });
        drafts.delete(oldKey);
        $('#draft').value = '';
        S.files = [];
      }
      if (!c.id && e.id) {
        c.id = e.id;
        c.title = (text || files[0]?.name || 'New chat').split('\n')[0].slice(0, 36);
        window.history.replaceState(null, '', '#chat/' + c.id);
      }
      c.messages.push({ role: 'assistant', text: '', model: e.model, status: 'pending' });
      draw(true);
    } else if (e.type === 'delta') {
      c.messages.at(-1).text += e.text;
      updateAnswer();
    } else if (e.type === 'finish') {
      finished = true;
      Object.assign(c.messages.at(-1), { status: e.status, usage: e.usage, note: e.note });
    }
  }
  try {
    if (!S.status.configured) {
      await refresh();
      if (!S.status.configured) {
        openGuide(ui);
        toast('Follow the setup guide to add your API key. Your draft is saved in this tab.');
        return;
      }
    }
    const r = await fetch('/api/respond', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const detail = await r.json();
      if (detail.code === 'no_key') {
        await refresh();
        openGuide(ui);
      }
      throw new Error(detail.error || 'Could not send the message.');
    }
    const reader = r.body.getReader(),
      dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let p;
      while ((p = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, p);
        buf = buf.slice(p + 1);
        if (line) handle(JSON.parse(line));
      }
    }
    if (!finished) throw new Error('The connection to Relay closed before the response finished.');
  } catch (e) {
    if (started && !finished)
      Object.assign(c.messages.at(-1), { status: 'error', note: e.message });
    else toast(e.message);
  } finally {
    S.busy = false;
    S.run = null;
    $('#draft').readOnly = false;
    $('#attach').disabled = false;
    const t = $('#thread'),
      near = t.scrollHeight - t.clientHeight - t.scrollTop < 120;
    draw(near);
    if (started) balanceChanged().catch((e) => toast(e.message));
    if (started && c.id) listChats().catch((e) => toast(e.message));
    if (!$('#dialog').open) $('#draft').focus({ preventScroll: true });
  }
}
async function addFiles(incoming) {
  if (S.busy) return;
  const textExt =
    /\.(txt|md|csv|tsv|json|jsonl|yaml|yml|xml|html|css|js|mjs|jsx|ts|tsx|py|rs|go|java|c|h|cpp|hpp|cs|sql|sh|ps1|toml|ini|log|tex|r|kt|swift|vue|svelte|ipynb)$/i;
  for (const f of incoming) {
    if (S.files.length === 4) {
      toast('You can attach up to four files per message.');
      break;
    }
    const img = /^image\/(png|jpeg|webp|gif)$/.test(f.type);
    if (!img && !f.type.startsWith('text/') && !textExt.test(f.name)) {
      toast('Choose a text file, source file, or supported image.');
      continue;
    }
    if (f.size > (img ? 3 * 1024 * 1024 : 400000)) {
      toast(img ? 'Each image can be up to 3 MB.' : 'Each text file can contain up to 100,000 characters.');
      continue;
    }
    const data = img
      ? await new Promise((ok, no) => {
          const r = new FileReader();
          r.onload = () => ok(r.result);
          r.onerror = () => no(new Error('Could not read the attachment.'));
          r.readAsDataURL(f);
        })
      : await f.text();
    if (!img && data.length > 100000) {
      toast('Each text file can contain up to 100,000 characters.');
      continue;
    }
    S.files.push({ name: f.name || 'Pasted image', kind: img ? 'image' : 'text', data });
  }
  drawChips();
  button();
}
function exportChat() {
  const c = S.chat,
    t = c.messages
      .map(
        (m) =>
          '## ' +
          (m.role === 'user' ? 'You' : 'Relay') +
          '\n\n' +
          m.text +
          (m.files?.length ? '\n\nAttachment: ' + m.files.map((f) => f.name).join(', ') : '') +
          (m.note ? '\n\n> ' + m.note : ''),
      )
      .join('\n\n');
  const url = URL.createObjectURL(
    new Blob(['# ' + c.title + '\n\n' + t + '\n'], { type: 'text/markdown;charset=utf-8' }),
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = (c.title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 70) || 'Relay') + '.md';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  toast('Markdown exported.');
}
function manage() {
  if (busyGuard()) return;
  const c = S.chat;
  const b = modal(
    'CONVERSATION',
    'Manage chat',
    `<div class="management">
    ${c.id ? `<label class="form-row">Chat title<input class="field" id="rename-value" maxlength="100" value="${esc(c.title)}"></label><button class="secondary" id="rename">${icon('edit')} Save title</button><button class="secondary" id="pin">${icon('pin')} ${c.pinned ? 'Unpin chat' : 'Pin chat'}</button>` : ''}
    <button class="secondary" id="export">${icon('download')} Export Markdown</button><button class="danger" id="delete">${icon('trash')} ${c.temporary ? 'Clear temporary chat' : 'Delete chat'}</button></div>`,
  );
  b.querySelector('#export').onclick = exportChat;
  if (c.id) {
    const change = async (data) => {
      try {
        await api('/api/chat/edit', { id: c.id, ...data });
        Object.assign(c, data);
        await listChats();
        draw();
        close();
      } catch (e) {
        toast(e.message);
      }
    };
    b.querySelector('#rename').onclick = () =>
      change({ title: b.querySelector('#rename-value').value.trim() });
    b.querySelector('#pin').onclick = () => change({ pinned: !c.pinned });
  }
  b.querySelector('#delete').onclick = async () => {
    if (!confirm('Permanently delete this conversation?')) return;
    try {
      if (c.id) await api('/api/chat/delete', { id: c.id });
      if (c.temporary) S.temp = blank(true);
      swap(c.temporary ? S.temp : blank());
      drafts.delete(c.id || 'temporary');
      if (c.temporary) {
        $('#draft').value = '';
        S.files = [];
        draw();
      }
      await listChats();
      close();
    } catch (e) {
      toast(e.message);
    }
  };
}
function search() {
  if (matchMedia('(max-width:760px)').matches) $('#layout').classList.add('mobile-nav');
  else $('#layout').classList.remove('side-hidden');
  $('#search-wrap').hidden = false;
  $('#history-search').focus();
}
function action(name) {
  if (['new', 'temp', 'import'].includes(name) && busyGuard()) return;
  if (name === 'new') {
    swap(blank());
  } else if (name === 'temp') swap(S.temp);
  else if (name === 'balance') openBalance(ui).catch((e) => toast(e.message));
  else if (name === 'guide') openGuide(ui);
  else if (name === 'settings') openSettings(ui);
  else if (name === 'import') openImporter(ui).catch((e) => toast(e.message));
  else if (name === 'retry') void send(true);
  else if (name === 'manage') manage();
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.action) action(b.dataset.action);
  else if (b.dataset.chat) openChat(b.dataset.chat).catch((e) => toast(e.message));
  else if ('copy' in b.dataset) void copy(S.chat.messages[Number(b.dataset.copy)].text);
  else if ('copycode' in b.dataset)
    void copy(b.closest('.codebox').querySelector('code').textContent);
  else if ('remove' in b.dataset && !S.busy) {
    S.files.splice(Number(b.dataset.remove), 1);
    drawChips();
    button();
  }
});
$('#send').onclick = () => void send();
$('#draft').oninput = () => {
  resize();
  button();
};
$('#draft').onkeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    if (!S.busy) void send();
  }
};
$('#attach').onclick = () => $('#files').click();
$('#files').onchange = (e) => {
  void addFiles([...e.target.files]).catch((err) => toast(err.message));
  e.target.value = '';
};
$('#draft').addEventListener('paste', (e) => {
  const files = [...e.clipboardData.files];
  if (files.length) {
    e.preventDefault();
    addFiles(files).catch((err) => toast(err.message));
  }
});
const form = $('#composer');
form.ondragover = (e) => {
  e.preventDefault();
  form.classList.add('drag');
};
form.ondragleave = () => form.classList.remove('drag');
form.ondrop = (e) => {
  e.preventDefault();
  form.classList.remove('drag');
  addFiles([...e.dataTransfer.files]).catch((err) => toast(err.message));
};
$('#toggle-side').onclick = () =>
  $('#layout').classList.toggle(
    matchMedia('(max-width:760px)').matches ? 'mobile-nav' : 'side-hidden',
  );
$('#scrim').onclick = () => $('#layout').classList.remove('mobile-nav');
$('#close-modal').onclick = close;
$('#dialog').onclick = (e) => {
  if (e.target === $('#dialog')) {
    const r = e.target.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
      close();
  }
};
$('#search-open').onclick = search;
$('#history-search').oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => listChats().catch((err) => toast(err.message)), 120);
};
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && !$('#dialog').open) {
    if (e.key.toLowerCase() === 'k') {
      e.preventDefault();
      search();
    }
    if (e.key.toLowerCase() === 'o') {
      e.preventDefault();
      action('new');
    }
  }
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', theme);
window.addEventListener('focus', () => refresh().catch(() => {}));
paint();
theme();
draw();
try {
  await Promise.all([refresh(), listChats()]);
  if (location.hash.startsWith('#chat/')) await openChat(location.hash.slice(6));
  else if (location.hash === '#temporary') swap(S.temp);
} catch (e) {
  toast(e.message);
}
