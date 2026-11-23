import { esc, icon } from './format.js';
import { t } from './i18n.js';
import { systemMessage } from './system-messages.js';

export function applyAgentActivity(message, event) {
  const log = message.agent_activity ??= {};
  const entries = log[event.name] ??= {};
  const item = entries[event.id] ??= { kind: 'message' };
  if (event.delta !== undefined) item.text = (item.text || '') + event.delta;
  else for (const key of ['kind', 'text', 'tool', 'target', 'stage', 'done']) {
    if (key in event) item[key] = event[key];
  }
}

function buttons(message, index, compact) {
  return (message.subagents || []).map(name => `<button type="button" data-agent-name="${esc(name)}" data-agent-turn="${index}"${compact ? '' : ' class="agent-nav-item"'} title="${esc(name)}">${compact ? '' : icon('branch')}<span>${esc(name)}</span></button>`).join('');
}

export function agentLinks(message, index) {
  return message.subagents?.length ? `<div class="subagents">${icon('branch')}${t('Subagents', '子智能体')} · ${buttons(message, index, true)}</div>` : '';
}

function entryLabel(item) {
  switch (item.kind) {
    case 'commentary': return t('Progress', '进展');
    case 'final_answer': return t('Answer', '答复');
    case 'spawn': return t('Agent started', '已启动子智能体');
    case 'tool': {
      const stage = {
        requested: t('Requested', '已请求'), in_progress: t('In progress', '进行中'),
        searching: t('Searching', '搜索中'), done: t('Completed', '已完成'), completed: t('Completed', '已完成'),
        failed: t('Failed', '失败'), error: t('Failed', '失败'), stopped: t('Stopped', '已停止'), denied: t('Declined', '已拒绝'),
      }[item.stage];
      return [item.tool, stage].filter(Boolean).join(' · ');
    }
    case 'delivery': return t('Message sent to {name}', '已向 {name} 发送消息', { name: item.target });
    case 'received': return t('Message from {name}', '收到来自 {name} 的消息', { name: item.target });
    case 'reasoning': return t('Reasoning', '推理');
    default: return t('Message', '消息');
  }
}

function activityStatus(message, entries) {
  const lastMessage = entries.findLast(item => ['commentary', 'message', 'final_answer'].includes(item.kind));
  if (lastMessage?.kind === 'final_answer' && lastMessage.done) return t('Answered', '已答复');
  if (message.status === 'pending' && message.waitingForCapacity) return t('Waiting for API capacity', '等待 API 容量');
  return ({
    pending: t('Live', '实时'), stopped: t('Turn stopped', '本轮已停止'),
    error: t('Turn failed', '本轮失败'), incomplete: t('Turn incomplete', '本轮未完成'),
  })[message.status] ?? t('Turn ended', '本轮已结束');
}

export function setupAgentPanel(getChat) {
  const nav = document.querySelector('#agent-nav');
  const panel = document.querySelector('#agent-panel');
  const stream = panel.querySelector('.agent-stream');
  const picker = panel.querySelector('select');
  const close = panel.querySelector('[data-close-agents]');
  let selection = null, navHtml = '', frame = 0;
  const rows = new Map();

  function dismiss(restoreFocus = false) {
    if (!selection) return;
    const { name, index } = selection;
    selection = null;
    rows.clear();
    stream.replaceChildren();
    picker.replaceChildren();
    panel.hidden = true;
    document.querySelector('#layout').classList.remove('agent-open');
    if (restoreFocus) {
      const selector = `[data-agent-turn="${index}"][data-agent-name="${CSS.escape(name)}"]`;
      const trigger = [...document.querySelectorAll(selector)].find(el => el.getBoundingClientRect().width && el.getBoundingClientRect().right > 0);
      (trigger || document.querySelector('#draft')).focus({ preventScroll: true });
    }
  }
  function updatePanel() {
    if (!selection) return;
    const { chat, message, name } = selection;
    if (chat !== getChat() || !chat.messages.includes(message)) return dismiss();
    const options = message.subagents || [];
    if (picker.options.length !== options.length) {
      picker.replaceChildren(...options.map(value => new Option(value, value)));
    }
    picker.value = name;
    const log = message.agent_activity?.[name] || {};
    const entries = Object.values(log);
    panel.querySelector('.agent-state').textContent = activityStatus(message, entries);
    const empty = panel.querySelector('.agent-empty');
    empty.hidden = !!entries.length;
    empty.textContent = message.status === 'pending' ? t('Waiting for activity…', '正在等待活动…') : t('No activity was recorded for this agent.', '没有此子智能体的活动记录。');
    const follow = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 45;
    for (const [id, item] of Object.entries(log)) {
      let row = rows.get(id);
      if (!row) {
        row = document.createElement('div');
        row.append(document.createElement('strong'), document.createElement('p'));
        rows.set(id, row);
        stream.append(row);
      }
      const label = entryLabel(item);
      if (row.firstChild.textContent !== label) row.firstChild.textContent = label;
      const text = item.kind === 'tool'
        ? [item.target, systemMessage(item.text || '')].filter(Boolean).join('\n')
        : (item.text || '');
      if (row.lastChild.textContent !== text) row.lastChild.textContent = text;
    }
    if (follow) stream.scrollTop = stream.scrollHeight;
  }
  function refresh() {
    const chat = getChat();
    const index = chat.messages.findLastIndex(message => message.subagents?.length);
    nav.hidden = index < 0;
    const html = index < 0 ? '' : buttons(chat.messages[index], index, false);
    if (navHtml !== html) {
      nav.querySelector('.agent-nav-list').innerHTML = navHtml = html;
    }
    updatePanel();
  }
  function open(index, name) {
    const chat = getChat(), message = chat.messages[index];
    if (!message?.subagents?.includes(name)) return;
    selection = { chat, message, index, name };
    stream.replaceChildren();
    rows.clear();
    picker.replaceChildren();
    document.querySelector('#layout').classList.remove('mobile-nav');
    document.querySelector('#layout').classList.add('agent-open');
    panel.hidden = false;
    updatePanel();
  }
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-agent-name]');
    if (trigger) {
      open(Number(trigger.dataset.agentTurn), trigger.dataset.agentName);
      close.focus({ preventScroll: true });
    }
  });
  picker.onchange = () => open(selection.index, picker.value);
  close.onclick = () => dismiss(true);
  document.addEventListener('keydown', event => {
    if (selection && event.key === 'Escape' && !document.querySelector('#dialog').open) dismiss(true);
  });
  return { refresh, tick() {
    if (!selection || frame) return;
    frame = requestAnimationFrame(() => { frame = 0; updatePanel(); });
  } };
}
