import { esc, icon, paint, markdown, webCitations } from './format.js';
import { t, language, setLanguage } from './i18n.js';
import { rateLimitDiagnostic, systemMessage } from './system-messages.js';
import { openGuide, openSettings } from './guide.js';
import { openBalance, showBalanceState } from './balance.js';
import { openImporter } from './importer.js';
import { showWorkspace, usesProjectFolder, workspaceButton, workspaceHtml, updateWorkspaceEvent } from './workspace.js';
import { artifactCards, drawArtifacts, setupArtifacts } from './artifacts.js';
import { installDictation } from './dictation.js';
import { createDrafts } from './drafts.js';
import { agentLinks, applyAgentActivity, setupAgentPanel } from './agents.js';

const $ = (s) => document.querySelector(s);
const blank = (temporary = false) => ({
  id: null,
  title: temporary ? 'Temporary chat' : 'New chat',
  source: 'local',
  temporary,
  temporarySession: temporary ? crypto.randomUUID() : undefined,
  messages: [],
});
const runs = new Map();
const unread = new Map();
const readingFiles = new Map();
const pendingChats = new Map();
let nextPending = 0;
const S = {
  chat: blank(),
  temp: blank(true),
  files: [],
  get busy() { return runs.has(this.chat); },
  get anyBusy() { return runs.size > 0; },
  status: {
    configured: false,
    settings: {
      model: 'gpt-5.4-mini',
      effort: 'auto',
      service_tier: 'default',
      web_search: true,
      auto_wait_rate_limits: true,
      workspace_auto_review: true,
      workspace_terminal: true,
      context: 0,
      max_output: 0,
      instructions: '',
    },
  },
  list: [],
};
let newChat = S.chat;
const agentPanel = setupAgentPanel(() => S.chat);
let navigation = 0;
const effortLabels = {
  auto: 'Auto',
  none: 'None',
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'Extra high',
  max: 'Max',
  ultra: 'Ultra',
};
const speedLabels = {
  default: 'Standard',
  auto: 'Auto',
  fast: 'Fast',
  priority: 'Fast',
  ultrafast: 'Ultrafast',
  flex: 'Flex',
};
const speedDetails = { fast: 'Higher cost', priority: 'Higher cost', ultrafast: 'Higher cost', flex: 'Lower cost · slower' };
const speedIcons = { default: 'gauge', auto: 'sparkles', fast: 'bolt', priority: 'bolt', ultrafast: 'speedBolt', flex: 'leaf' };
const choiceChinese = {
  'Connect API': '连接 API', Auto: '自动', None: '无', Minimal: '最低', Low: '低', Medium: '中',
  High: '高', 'Extra high': '极高', Max: '最高', Ultra: 'Ultra', Standard: '标准',
  Fast: '快速', Ultrafast: '极速', Flex: '弹性', 'Higher cost': '费用较高', 'Lower cost · slower': '费用较低 · 速度较慢',
  'Can delegate to subagents · higher token usage': '可分派给子智能体 · 消耗更多 token',
};
const modelControls = {
  model: { trigger: $('#model'), title: () => t('Model', '模型'), labels: { '': 'Connect API' } },
  effort: { trigger: $('#effort'), title: () => t('Reasoning effort', '推理强度'), labels: effortLabels },
  service_tier: { trigger: $('#service-tier'), title: () => t('Speed', '速度'), labels: speedLabels },
};
const modelFields = Object.keys(modelControls);
const choiceMenu = $('#choice-menu');
const modelSearch = $('#model-search');
const choiceOptions = $('#choice-options');
const webSearch = $('#web-search');
let openChoice = null;
let openPanel = null;
let catalog = { models: [], loading: false, pending: null, message: '' };
let settingsVersion = 0;
let settingsSave = Promise.resolve();
const savingWorkspaces = new Set();
const drafts = createDrafts(api, toast);
let toastTimer,
  searchTimer,
  listVersion = 0,
  frame = 0;
const dkey = (chat = S.chat) => chat.temporary ? 'temporary' : chat.id || (chat === newChat ? 'new' : chat.pendingKey) || 'new';
function toast(message) {
  const t = $('#toast');
  t.textContent = systemMessage(message);
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
          keepalive: path === '/api/settings' || path === '/api/chat/workspace' || (path === '/api/draft' && data.text.length < 8000),
        },
  );
  const result = await r.json();
  if (!r.ok) {
    const e = new Error(systemMessage(result.error || t('The local request failed.', '本地请求失败。')));
    e.code = result.code;
    throw e;
  }
  return result;
}
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(t("Copied to clipboard.", "已复制到剪贴板。"));
  } catch {
    toast(t("Select the text and copy it manually. Clipboard access is unavailable.", "无法访问剪贴板，请选中文字后手动复制。"));
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
function choiceLabel(label) {
  return choiceChinese[label] ? t(label, choiceChinese[label]) : label;
}
function showOverview(focusField) {
  openChoice = null;
  choiceMenu.hidden = true;
  $('#response-overview').hidden = false;
  $('#response-menu').setAttribute('aria-labelledby', 'response-title');
  if (focusField) modelControls[focusField].trigger.focus({ preventScroll: true });
}
function closeComposerMenu(restoreFocus = false) {
  if (!openPanel) return;
  const trigger = openPanel;
  openPanel = null;
  document.getElementById(trigger.getAttribute('aria-controls')).hidden = true;
  trigger.setAttribute('aria-expanded', 'false');
  if (openChoice) showOverview();
  if (restoreFocus) trigger.focus({ preventScroll: true });
}
function positionComposerMenu() {
  if (!openPanel) return;
  const panel = document.getElementById(openPanel.getAttribute('aria-controls'));
  const anchor = openPanel.getBoundingClientRect();
  const composer = $('#composer').getBoundingClientRect();
  const stage = $('#stage').getBoundingClientRect();
  const above = anchor.top - stage.top - 12;
  const below = stage.bottom - anchor.bottom - 12;
  const placeBelow = above < Math.min(340, panel.scrollHeight) + 8 && below > above;
  panel.classList.toggle('place-below', placeBelow);
  panel.style.setProperty('--choice-space', Math.max(64, placeBelow ? below : above) + 'px');
  panel.style.setProperty('--choice-width', (composer.width - 8) + 'px');
  const targetLeft = openPanel.id === 'response-settings' ? anchor.right - panel.offsetWidth : anchor.left;
  const left = Math.max(composer.left + 4, Math.min(targetLeft, composer.right - panel.offsetWidth - 4));
  panel.style.left = (left - openPanel.parentElement.getBoundingClientRect().left) + 'px';
}
function openComposerMenu(trigger) {
  closeComposerMenu();
  const panel = document.getElementById(trigger.getAttribute('aria-controls'));
  openPanel = trigger;
  trigger.setAttribute('aria-expanded', 'true');
  panel.hidden = false;
  positionComposerMenu();
  const first = trigger.id === 'response-settings' ? $('#model:not(:disabled)') : null;
  (first ?? panel.querySelector('button:not(:disabled)'))?.focus({ preventScroll: true });
}
function drawModelOptions() {
  const { choices, selected, labels } = modelControls[openChoice];
  const query = openChoice === 'model' ? modelSearch.value.trim().toLowerCase() : '';
  const matches = choices.filter((value) => value.toLowerCase().includes(query));
  choiceOptions.innerHTML = matches.map((value) => {
    const active = value === selected;
    const detail = openChoice === 'service_tier' ? speedDetails[value]
      : openChoice === 'effort' && value === 'ultra' ? 'Can delegate to subagents · higher token usage' : '';
    const speed = openChoice === 'service_tier' && speedIcons[value]
      ? `<span class="speed-indicator" data-tier="${esc(value)}" aria-hidden="true">${icon(speedIcons[value])}</span>` : '';
    return `<button type="button" class="choice-option" data-value="${esc(value)}" aria-pressed="${active}">${speed}<span>${esc(choiceLabel(labels[value] ?? value))}${detail ? `<small>${esc(choiceLabel(detail))}</small>` : ''}</span>${active ? icon('check') : ''}</button>`;
  }).join('') || '<p class="choice-empty">' + t('No matching models.', '没有匹配的模型。') + '</p>';
}
function showModelMenu(field) {
  openChoice = field;
  $('#response-overview').hidden = true;
  $('#response-menu').setAttribute('aria-labelledby', 'choice-title');
  $('#choice-title').textContent = modelControls[field].title();
  modelSearch.parentElement.hidden = field !== 'model';
  modelSearch.value = '';
  drawModelOptions();
  choiceMenu.hidden = false;
  positionComposerMenu();
  if (field === 'model') modelSearch.focus({ preventScroll: true });
  else (choiceOptions.querySelector('[aria-pressed="true"]') ?? choiceOptions.firstElementChild).focus({ preventScroll: true });
}
function drawModelControl(field, choices, selected, disabled) {
  const control = modelControls[field];
  const { trigger, title, labels } = control;
  control.choices = choices;
  control.selected = selected;
  const label = choiceLabel(labels[selected] ?? selected);
  trigger.querySelector('.choice-value').textContent = label;
  trigger.title = label;
  trigger.setAttribute('aria-label', title() + ', ' + label);
  trigger.disabled = disabled || !choices.some((value) => value !== selected);
}
function currentModel(model) {
  return catalog.models.find(({ id }) => id === model)
    ?? (S.status.model_capabilities?.id === model ? S.status.model_capabilities : null);
}
function searchAvailable(settings) {
  return S.status.configured && !!currentModel(settings.model)?.web_search && settings.effort !== 'minimal';
}
function displayedSettings() {
  return runs.get(S.chat)?.settings ?? { ...S.status.settings, ...catalog.pending };
}
function drawWebSearch() {
  const { configured } = S.status;
  const settings = displayedSettings();
  const item = currentModel(settings.model);
  const available = searchAvailable(settings);
  webSearch.disabled = !available || S.busy || !!catalog.pending || catalog.loading;
  webSearch.setAttribute('aria-pressed', String(available && !!settings.web_search));
  webSearch.title = available ? t('Search the web for sources (additional API charges may apply)', '搜索网络来源，可能产生额外 API 费用')
    : !configured ? t('Connect your API key to search the web', '连接 API 密钥后即可联网搜索')
    : catalog.loading ? t('Checking model support', '正在检查模型是否支持联网搜索')
    : !item ? t('Refresh the model list to check web search support', '刷新模型列表以检查联网搜索支持')
    : item.web_search && settings.effort === 'minimal' ? t('Select Low reasoning effort or higher to search with this model', '推理强度设为低或更高时可联网搜索')
    : t('Web search is unavailable for this model', '此模型不支持联网搜索');
  if (configured && !available && settings.web_search) webSearch.title += t('. Your Search preference is saved.', '。已保留你的搜索偏好。');
}
function renderModelControls() {
  const { configured } = S.status;
  const { model, effort, service_tier } = displayedSettings();
  const item = currentModel(model);
  const options = catalog.models.map(({ id }) => id);
  const disabled = !configured || catalog.loading || !!catalog.pending || S.busy;
  drawModelControl('model', options, configured ? model : '', disabled);
  drawModelControl('effort', item?.efforts ?? [], effort, disabled);
  drawModelControl('service_tier', item?.tiers ?? [], service_tier, disabled);
  $('#refresh-models').disabled = disabled || !!catalog.pending;
  const modelName = configured ? model : t('Connect API', '连接 API');
  const effortName = configured && effort !== 'auto' ? choiceLabel(effortLabels[effort] ?? effort) : '';
  $('#response-settings .model-name').textContent = modelName;
  $('#response-settings .effort-summary').textContent = effortName;
  const trigger = $('#response-settings');
  const indicator = trigger.querySelector('.speed-indicator');
  const speedLabel = configured ? t('Speed · {speed}', '速度 · {speed}', {
    speed: choiceLabel(speedLabels[service_tier] ?? service_tier),
  }) : '';
  indicator.dataset.tier = service_tier;
  indicator.hidden = !configured || !speedIcons[service_tier];
  indicator.innerHTML = indicator.hidden ? '' : icon(speedIcons[service_tier]);
  indicator.title = speedLabel;
  trigger.title = [t('Response settings', '回复设置'), modelName, effortName, speedLabel].filter(Boolean).join(' · ');
  trigger.setAttribute('aria-label', trigger.title);
  if (openChoice) {
    if (modelControls[openChoice].trigger.disabled) showOverview();
    else drawModelOptions();
  }
  const statusText = { loading: () => t('Loading available models…', '正在加载可用模型…'),
    empty: () => t('No supported models are available for this API project.', '此 API 项目没有可用的聊天模型。'),
    error: () => t('Could not refresh models. Use the refresh button to retry.', '刷新模型失败。请在回复设置中重试。') };
  $('#model-status').textContent = statusText[catalog.message]?.() || (options.length && !options.includes(model)
    ? t('Your saved model is missing from the API model list. Refresh the list or choose another model.', 'API 模型列表里没有你保存的模型。请刷新列表或选择其他模型。')
    : '');
  drawWebSearch();
  drawWorkspace();
}
function queueSettingsSave(write) {
  settingsVersion += 1;
  const save = settingsSave.then(write);
  settingsSave = save.catch(() => {});
  return save;
}
function saveSettings(patch) {
  return queueSettingsSave(async () => {
    S.status.settings = await api('/api/settings', patch);
    setLanguage(S.status.settings.language);
    note();
    renderModelControls();
  });
}
async function updateModelSettings(patch) {
  const current = catalog;
  current.pending = patch;
  renderModelControls();
  button();
  try {
    await saveSettings(patch);
  } catch (e) {
    toast(e.message);
  } finally {
    current.pending = null;
    renderModelControls();
    button();
  }
}
function modelSettings(model) {
  const { effort, service_tier } = S.status.settings;
  return {
    model: model.id,
    effort: model.efforts.includes(effort) ? effort : 'auto',
    service_tier: model.tiers.includes(service_tier) ? service_tier : 'default',
  };
}
function modelSettingsPending() {
  return catalog.pending || (catalog.loading && !catalog.models.length);
}
async function loadModels() {
  const current = catalog;
  current.loading = true;
  current.message = 'loading';
  renderModelControls();
  button();
  try {
    const { models } = await api('/api/models');
    if (current !== catalog) return;
    current.models = models;
    current.message = models.length ? '' : 'empty';
  } catch (e) {
    current.message = 'error';
    if (current === catalog) toast(e.message);
  } finally {
    current.loading = false;
    if (current === catalog) {
      renderModelControls();
      button();
    }
  }
}
async function refresh() {
  const version = settingsVersion;
  await settingsSave;
  const status = await api('/api/status');
  $('#relay-version').textContent = 'RELAY ' + status.version;
  const previous = S.status;
  if (version !== settingsVersion) status.settings = previous.settings;
  const connectionChanged = previous.configured !== status.configured || previous.key_source !== status.key_source;
  S.status = status;
  setLanguage(status.settings.language);
  if (!S.chat.id && usesProjectFolder(S.chat.workspace_mode) && S.chat.workspace_directory !== status.settings.workspace_directory) {
    delete S.chat.workspace_mode;
    delete S.chat.workspace_directory;
    delete S.chat.workspace_auto_review;
    delete S.chat.workspace_terminal;
  }
  drawWorkspace();
  const el = $('#connection');
  el.classList.toggle('ready', status.configured);
  el.querySelector('b').textContent = status.configured ? t('Key found', '已找到密钥') : t('Set up API', '配置 API');
  el.dataset.action = status.configured ? 'settings' : 'guide';
  if (connectionChanged) {
    catalog = { models: [], loading: false, pending: null, message: '' };
    if (status.configured) void loadModels();
    else renderModelControls();
  } else if (!status.configured || [...modelFields, 'web_search'].some((field) => previous.settings[field] !== status.settings[field])) {
    renderModelControls();
  }
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
function refreshRunHistory() {
  // A first message can be in SQLite before its stream has delivered the assigned chat ID.
  // Keep its provisional row until the outstanding start events have identified those chats.
  if (![...runs.keys()].some(chat => !chat.id && !chat.temporary)) {
    void listChats().catch(err => toast(err.message));
  }
}
function drawList() {
  const statuses = new Map(unread);
  for (const [chat] of runs) {
    const message = chat.messages.at(-1);
    const status = message?.waitingForCapacity ? 'waiting'
      : message?.approval && !message.approval.submitting ? 'review' : 'running';
    statuses.set(dkey(chat), status);
  }
  const labels = {
    running: t('Running', '运行中'), waiting: t('Waiting', '等待中'), review: t('Needs review', '待审批'), done: t('Done', '已完成'),
    stopped: t('Stopped', '已停止'), error: t('Failed', '失败'), incomplete: t('Incomplete', '未完成'),
  };
  const badge = key => statuses.has(key)
    ? `<small class="chat-status" data-status="${statuses.get(key)}">${labels[statuses.get(key)]}</small>` : '';
  const tempButton = $('#nav-temp');
  tempButton.querySelector('.chat-status')?.remove();
  tempButton.insertAdjacentHTML('beforeend', badge('temporary'));
  const query = $('#history-search').value.toLowerCase();
  const pending = [...pendingChats.values()].filter(c => c.title.toLowerCase().includes(query));
  $('#history').innerHTML = [...pending, ...S.list].map(c => {
    const target = c.id ? `data-chat="${esc(c.id)}"` : `data-pending-chat="${esc(c.pendingKey)}"`;
    const active = c.id ? c.id === S.chat.id : c === S.chat;
    return `<button class="history-item ${active ? 'active' : ''}" ${target} title="${esc(c.title)}">${icon(c.pinned ? 'pin' : c.source === 'local' ? 'chat' : 'branch')}<span>${esc(c.title)}</span>${badge(dkey(c))}</button>`;
  }).join('') || `<p class="history-empty">${query ? t("No matching chats", "没有匹配的聊天") : t("No conversations yet", "暂无聊天")}<span>${query ? t("Try another search term.", "试试其他关键词。") : t("Your saved chats will appear here.", "保存的聊天会显示在这里。")}</span></p>`;
}
function note() {
  const limit = displayedSettings().context;
  const msg = S.chat.temporary
    ? t("Temporary chat · Clears when you reload or close this window", "临时聊天 · 重新载入或关闭窗口后清除")
    : S.chat.messages.length
      ? limit ? t('Saved locally · Includes up to {limit} recent messages', '已保存到本机 · 最多包含最近 {limit} 条消息', { limit })
        : t("Saved locally · Uses available model context", "已保存到本机 · 使用模型可用的上下文")
      : t("Saved on this device · Uses your OpenAI API credits", "保存在本机 · 使用你的 OpenAI API 额度");
  $('#compose-note span').textContent = msg;
  const usage = S.chat.messages.findLast(m => m.context_usage)?.context_usage;
  const meter = $('#context-usage');
  meter.hidden = !usage?.limit;
  if (usage?.limit) {
    const percent = Math.round(100 * usage.used / usage.limit);
    meter.textContent = [usage.compacted && t("Compacted", "已压缩"), t('Context {percent}%', '上下文 {percent}%', { percent })].filter(Boolean).join(' · ');
    meter.title = (usage.estimated ? t('Estimated ', '估算：') : '') +
      t('{used} of {limit} context tokens', '已用 {used} / {limit} 个上下文词元', {
        used: Number(usage.used).toLocaleString(language()), limit: Number(usage.limit).toLocaleString(language()),
      });
  }
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
function answerHtml(m, markers) {
  const content = markdown(m.text, markers, m.artifacts);
  if (m.status !== 'pending') return content;
  if (m.waitingForCapacity)
    return content + `<div class="generation-status rate-progress" role="status"><span class="typing" aria-hidden="true"></span>${t('Waiting for API capacity. Relay will continue automatically.', '等待 API 容量，稍后自动继续。')}</div>`;
  if (m.text) return content + `<span class="typing" aria-label="${t('Generating', '正在生成')}"></span>`;
  return `<div class="generation-status" role="status"><span class="typing" aria-hidden="true"></span>${esc(systemMessage(m.progress || t('Preparing answer…', '正在准备回复…')))}</div>`;
}
function sourcesHtml(references) {
  if (!references.length) return '';
  const links = references.map(({ href, title, number }) =>
    `<li><a target="_blank" rel="noopener noreferrer" title="${esc(title)}" href="${esc(href)}"><span class="source-number">${number}</span><span>${esc(title)}</span>${icon('external')}</a></li>`,
  ).join('');
  return `<section class="answer-sources"><h3>${t('Sources', '来源')}</h3><ul>${links}</ul></section>`;
}
function msgHtml(m, i) {
  if (m.role === 'user')
    return `<article class="msg user"><div class="bubble">${esc(m.text)}${safeFiles(m.files)}</div>${!S.busy && i === S.chat.messages.length - 1 ? '<div class="meta"><button data-action="retry">' + icon('refresh') + ' ' + t('Answer this message', '回复这条消息') + '</button></div>' : ''}</article>`;
  const pending = m.status === 'pending';
  const units = usageHtml(m.usage);
  const rate = m.status === 'error' ? rateLimitDiagnostic(m.note, m.rate_limit) : null;
  const notice = rate ? `<p class="notice bad">${esc(rate.summary)}</p><details class="rate-details"><summary>${t('API error details', 'API 错误详情')}</summary><p>${esc(rate.detail)}</p></details>`
    : m.note ? `<p class="notice ${m.status === 'error' ? 'bad' : ''}">${esc(systemMessage(m.note))}</p>` : '';
  const allowRetry =
    !S.busy && i === S.chat.messages.length - 1 && S.chat.messages[i - 1]?.role === 'user';
  const allowContinue = allowRetry && (m.reason === 'max_output_tokens' || rate?.temporary) && m.text.trim();
  const { byLine, references } = webCitations(m.text, m.sources);
  const fork = S.chat.id && !S.busy && !pending ? `<button data-fork="${i}" title="${t('Start a new chat from this reply', '从此回复开始新聊天')}">${icon('branch')} ${t('Branch', '创建分支')}</button>` : '';
  return `<article class="msg assistant"><div class="speaker"><img src="/mark.svg" alt="">Relay <small>${esc(m.model || '')}</small></div><div class="run-info">${runInfoHtml(m, i)}</div><div class="workspace-log">${workspaceHtml(m)}</div><div class="answer">${answerHtml(m, byLine)}</div>${artifactCards(m.artifacts)}${sourcesHtml(references)}${notice}<div class="meta">${m.text && !pending ? `<button data-copy="${i}" title="${t('Copy response', '复制回复')}" aria-label="${t('Copy response', '复制回复')}">${icon('copy')}</button>` : ''}${fork}${allowContinue ? `<button data-action="continue">${icon('arrow')} ${t('Continue', '继续')}</button>` : ''}${allowRetry ? `<button data-action="retry">${icon('refresh')} ${t('Regenerate', '重新生成')}</button>` : ''}${units}</div></article>`;
}
function usageHtml(usage) {
  if (!usage) return '';
  const count = v => Number(v || 0).toLocaleString(language());
  const cached = usage.input_tokens_details?.cached_tokens;
  const reasoning = usage.output_tokens_details?.reasoning_tokens;
  const prefix = usage.estimated ? t('Estimated usage · ', '估算用量 · ') : '';
  return `<span>${prefix}${t('{n} input', '输入 {n}', { n: count(usage.input_tokens) })}${cached ? ' ' + t('({n} cached)', '（缓存 {n}）', { n: count(cached) }) : ''} · ${t('{n} output tokens', '输出 {n} 词元', { n: count(usage.output_tokens) })}${reasoning ? ' ' + t('({n} reasoning)', '（推理 {n}）', { n: count(reasoning) }) : ''}</span>`;
}
function runInfoHtml(m, index) {
  const summary = m.reasoning_summary ? `<details class="reasoning-summary"><summary>${t('Reasoning summary', '推理摘要')}</summary><p>${esc(m.reasoning_summary)}</p></details>` : '';
  const agents = m.subagents?.length ? agentLinks(m, index ?? S.chat.messages.indexOf(m)) : '';
  const updates = (m.steers || []).map(update => {
    const text = typeof update === 'string' ? update : update.text;
    const label = update.status === 'failed' ? t("Update failed", "追加失败") : t("Your update", "追加指令");
    return '<p class="steer-update"><strong>' + label + '</strong> ' + esc(text) + '</p>';
  }).join('');
  return summary + agents + updates;
}
function draw(scroll = false) {
  const c = S.chat,
    empty = !c.messages.length,
    thread = $('#thread'),
    previousScroll = thread.scrollTop;
  $('#stage').classList.toggle('empty', empty);
  $('#landing').hidden = $('#shortcuts').hidden = !empty;
  $('#thread').hidden = empty;
  $('#hero-title').innerHTML = c.temporary
    ? t("A chat for now<span>.</span>", "开始临时聊天<span>。</span>")
    : t("Where shall we start<span>?</span>", "想从哪里开始<span>？</span>");
  $('#hero-copy').textContent = c.temporary
    ? t("Ask follow-up questions here. Reload or close the window to clear this conversation.", "可以在这里连续提问。重新载入或关闭窗口后，这段对话就会清除。")
    : t("Start a new conversation or pick up where you left off in Codex.", "开始新对话，也可以接着聊之前的 Codex 对话。");
  $('#hero-label').textContent = c.temporary ? t("Temporary workspace", "临时工作区") : t("Personal workspace", "个人工作区");
  $('#second-card').dataset.action = c.temporary ? 'new' : 'temp';
  $('#second-card strong').textContent = c.temporary ? t('Saved chat', '普通聊天') : t('Temporary chat', '临时聊天');
  $('#second-card small').textContent = c.temporary ? t("Keep a local history", "在本机保存聊天记录") : t("Clears when you close the window", "关闭窗口后清除");
  $('#crumb').textContent = c.temporary ? t('Temporary chat', '临时聊天') : !c.id && !c.pendingKey ? t('New chat', '新建聊天') : c.title;
  $('#more').hidden = !c.id && !(c.temporary && c.messages.length) && !(c.pendingKey && c !== newChat && !S.busy);
  $('#nav-temp').classList.toggle('active', !!c.temporary);
  $('#messages').innerHTML = c.messages.map(msgHtml).join('');
  agentPanel.refresh();
  $('#draft').rows = empty ? 2 : 1;
  resize();
  drawList();
  drawChips();
  note();
  button();
  thread.scrollTop = scroll ? thread.scrollHeight : previousScroll;
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
        `<span class="chip">${icon('file')}${esc(f.name)}<button data-remove="${i}" aria-label="${esc(t('Remove attachment {name}', '移除附件 {name}', { name: f.name }))}">${icon('x')}</button></span>`,
    )
    .join('');
}
function chatWorkspace() {
  const chat = S.chat, settings = S.status.settings;
  const mode = chat.workspace_mode ?? (chat.temporary ? 'off' : 'chat');
  return {
    mode, directory: chat.workspace_directory || '',
    autoReview: mode !== 'off' && (chat.workspace_auto_review ?? settings.workspace_auto_review),
    terminal: mode !== 'off' && (chat.workspace_terminal ?? settings.workspace_terminal),
  };
}
function drawWorkspace() {
  const access = chatWorkspace();
  const model = currentModel(displayedSettings().model);
  const unavailable = model?.workspace_tools === false;
  const locked = S.busy || savingWorkspaces.has(S.chat) || !!catalog.pending;
  workspaceButton($('#workspace'), access, locked);
  for (const [button, enabled, hint] of [
    [$('#auto-review'), access.autoReview && (access.mode !== 'read' || access.terminal),
      t('Automatically review eligible file edits and read-only commands. Reviewing edits uses your API credits.', '自动审阅符合条件的文件编辑和只读命令。审阅文件编辑会消耗 API 额度。')],
    [$('#terminal-commands'), access.terminal, t('Run supported commands in this workspace. Tests and builds ask for approval.', '在工作区内运行受支持的命令。测试和构建需要你确认。')],
  ]) {
    const inactive = access.mode === 'off' || unavailable || (button.id === 'auto-review' && access.mode === 'read' && !access.terminal);
    button.setAttribute('aria-pressed', String(!!enabled && !inactive));
    button.disabled = locked || inactive;
    button.title = unavailable ? t('Local tools are unavailable for this model', '此模型不支持本地工具')
      : access.mode === 'off' ? t('Choose a workspace to use local tools', '选择工作区以使用本地工具')
      : inactive ? t('Enable Terminal commands to review read-only commands', '打开终端命令后可审阅只读命令') : hint;
  }
}
function workspaceFields(access) {
  return { workspace_mode: access.mode, workspace_directory: access.directory,
    workspace_terminal: access.terminal, workspace_auto_review: access.autoReview };
}
async function saveWorkspace(access) {
  const chat = S.chat, previous = chatWorkspace();
  const patch = workspaceFields(access);
  const defaults = {};
  if (access.mode !== 'off' && !chat.temporary) {
    if (access.autoReview !== previous.autoReview) defaults.workspace_auto_review = access.autoReview;
    if (access.terminal !== previous.terminal) defaults.workspace_terminal = access.terminal;
  }
  savingWorkspaces.add(chat);
  button();
  try {
    if (chat.id) {
      await queueSettingsSave(async () => {
        const binding = await api('/api/chat/workspace', { id: chat.id, mode: access.mode, directory: access.directory,
          workspace_auto_review: access.autoReview, workspace_terminal: access.terminal, defaults });
        Object.assign(chat, binding);
        Object.assign(S.status.settings, defaults);
      });
    } else {
      if (usesProjectFolder(access.mode) && access.directory !== S.status.settings.workspace_directory) {
        defaults.workspace_directory = access.directory;
      }
      if (Object.keys(defaults).length) await saveSettings(defaults);
      if (usesProjectFolder(access.mode)) patch.workspace_directory = S.status.settings.workspace_directory;
      Object.assign(chat, patch);
    }
  } finally {
    savingWorkspaces.delete(chat);
    button();
  }
}
function button() {
  const run = runs.get(S.chat);
  const el = $('#send');
  el.innerHTML = icon(S.busy ? 'stop' : 'up');
  el.disabled = !run && (savingWorkspaces.has(S.chat) || modelSettingsPending() || readingFiles.has(dkey()) || !($('#draft').value.trim() || S.files.length));
  el.title = S.busy ? t('Stop generating', '停止生成') : t('Send message', '发送消息');
  el.setAttribute('aria-label', el.title);
  $('#draft').readOnly = !!run && !run.canSteer;
  $('#draft').placeholder = run ? (run.canSteer ? t('Add a requirement while Relay is working…', 'Relay 工作时可在此补充要求…') : t('Relay is working…', 'Relay 正在处理…'))
    : t('Ask a question or drop a file here…', '问点什么，或把文件拖到这里…');
  $('#attach').disabled = !!run;
  $('#steer').hidden = !run?.canSteer;
  $('#steer').disabled = !$('#draft').value.trim() || !!run?.postingSteer;
  $('#dictate').disabled = !dictation.supported || $('#draft').readOnly;
  drawArtifacts(S.chat);
  drawWebSearch();
  drawWorkspace();
}
function busyGuard() {
  if (!S.busy) return false;
  toast(t("Stop the current response or wait for it to finish.", "请停止当前回复或等待其完成。"));
  return true;
}
function swap(c) {
  closeComposerMenu();
  navigation++;
  unread.delete(dkey(c));
  dictation.cancel();
  drafts.set(dkey(), { value: $('#draft').value, files: S.files });
  S.chat = c;
  const draft = drafts.get(dkey());
  S.files = draft.files;
  $('#draft').value = draft.value;
  history.replaceState(
    null,
    '',
    c.id ? '#chat/' + c.id : c.temporary ? '#temporary' : location.pathname,
  );
  $('#layout').classList.remove('mobile-nav');
  renderModelControls();
  draw(true);
  $('#draft').focus({ preventScroll: true });
}
async function openChat(id) {
  const localChat = () => [...runs.keys(), ...savingWorkspaces].find(chat => chat.id === id);
  const inProgress = localChat();
  if (inProgress) return swap(inProgress);
  const version = ++navigation;
  const c = await api('/api/chats/' + encodeURIComponent(id));
  if (version === navigation) swap(localChat() ?? c);
}
function openNew() {
  if (runs.has(newChat)) {
    const pending = newChat;
    const submitted = pending === S.chat ? { value: $('#draft').value, files: S.files } : drafts.get('new');
    newChat = blank();
    drafts.set(dkey(pending), submitted);
    drafts.delete('new');
  }
  swap(newChat);
}
function canSummarize() {
  const { model, effort } = S.status.settings;
  return !!currentModel(model)?.reasoning_summary && !['none', 'ultra'].includes(effort);
}
const ui = { state: S, api, modal, close, toast, copy, refresh, saveSettings, refreshBalance, balanceChanged, theme, openChat, listChats, canSummarize };
setupArtifacts(ui);
const dictation = installDictation({ button: $('#dictate'), status: $('#dictation-status'), draft: $('#draft'),
  configured: () => S.status.configured, toast, onTranscribed: () => balanceChanged().catch(() => {}) });
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
    el.innerHTML = answerHtml(m);
    if (near) t.scrollTop = t.scrollHeight;
  });
}
async function stop(run = runs.get(S.chat)) {
  run.wantStop = true;
  if (!run.id) return;
  try {
    await api('/api/stop', { run: run.id });
  } catch (err) {
    toast(err.message);
  }
}
async function sendSteer() {
  const chat = S.chat, run = runs.get(chat);
  const draft = $('#draft'), value = draft.value, text = value.trim();
  if (!text || !run?.canSteer || run.postingSteer) return;
  dictation.cancel();
  run.postingSteer = true;
  button();
  try {
    await api('/api/steer', { run: run.id, text });
    const key = dkey(chat);
    if (dkey() === key) {
      if (draft.value === value) {
        draft.value = '';
        drafts.set(key, { value: '', files: S.files });
        resize();
      }
      toast(t("Update sent to this run.", "已向当前任务追加指令。"));
    } else {
      const saved = drafts.get(key);
      if (saved.value === value) drafts.set(key, { ...saved, value: '' });
    }
  } catch (err) {
    toast(chat === S.chat ? err.message : chat.title + ' · ' + err.message);
  } finally {
    run.postingSteer = false;
    if (chat === S.chat) button();
  }
}
async function send(retry = false, continuation = false) {
  if (S.busy) {
    if (!retry && !continuation) await stop();
    return;
  }
  if (savingWorkspaces.has(S.chat) || modelSettingsPending() || readingFiles.has(dkey())) return;
  if (runs.size >= 4) return toast(t("Four chats are running. Wait for one to finish or stop a response before sending.", "已有四个聊天正在运行。请先停止一个回复或等待其完成。"));
  dictation.cancel();
  const text = continuation ? 'Continue from where you left off in the same language, without repeating what you already wrote.' : $('#draft').value.trim(),
    files = continuation ? [] : S.files.slice();
  if (!retry && !text && !files.length) return;
  const c = S.chat;
  const settings = { ...S.status.settings };
  const { model, effort, service_tier, context: limit, max_output, instructions, reasoning_summary } = settings;
  const workspace = workspaceFields(chatWorkspace());
  const previous = limit ? c.messages.slice(-(retry ? limit + 1 : limit - 1)) : c.messages;
  const history = c.temporary
    ? previous.map(({ role, text, files, compaction, steers }) => ({ role, text, files, compaction, steers }))
    : undefined;
  const payload = {
    id: c.id, temporary: !!c.temporary, temporary_session: c.temporarySession, history, retry, message: { text, files },
    model_settings: { model, effort, service_tier, context: limit, max_output, instructions, reasoning_summary,
      auto_wait_rate_limits: settings.auto_wait_rate_limits !== false },
    web_search: !!settings.web_search && searchAvailable(settings),
    ...workspace,
  };
  const run = { id: null, settings, canSteer: false, wantStop: false, postingSteer: false };
  runs.set(c, run);
  unread.delete(dkey(c));
  if (!c.id && !c.temporary) {
    c.pendingKey ??= 'pending-' + ++nextPending;
    c.title = (text || files[0]?.name || 'New chat').split('\n')[0].slice(0, 36);
    pendingChats.set(c.pendingKey, c);
  }
  renderModelControls();
  drawList();
  button();
  let started = false,
    finished = false,
    outcome = 'error';
  let capacityTimer;
  function showCapacityWait(message, waiting) {
    if (!!message.waitingForCapacity === waiting) return;
    message.waitingForCapacity = waiting;
    drawList();
    if (c === S.chat) {
      updateAnswer();
      agentPanel.tick();
    }
  }
  function handle(e) {
    const visible = c === S.chat;
    if (e.type === 'start') {
      started = true;
      run.id = e.run;
      if (run.wantStop) void stop(run);
      if (retry && c.messages.at(-1)?.role === 'assistant') c.messages.pop();
      if (!retry) c.messages.push({ role: 'user', text, files });
      if (!retry && !continuation) {
        drafts.delete(dkey(c));
        if (visible) {
          $('#draft').value = '';
          S.files = [];
        }
      }
      if (!c.id && e.id) {
        Object.assign(c, workspace);
        c.id = e.id;
        pendingChats.delete(c.pendingKey);
        delete c.pendingKey;
        if (newChat === c) newChat = blank();
        if (visible) window.history.replaceState(null, '', '#chat/' + c.id);
        if (!$('#history-search').value && !S.list.some(chat => chat.id === c.id)) {
          S.list.unshift({ id: c.id, title: c.title, source: c.source });
        }
        refreshRunHistory();
      }
      if (e.chat_folder) c.chat_folder = e.chat_folder;
      c.messages.push({ role: 'assistant', text: '', model: e.model, status: 'pending' });
      if (visible) draw(true);
      else drawList();
    } else if (e.type === 'rate_wait' || e.type === 'rate_resume') {
      const m = c.messages.at(-1);
      clearTimeout(capacityTimer);
      run.canSteer = false;
      if (e.type === 'rate_wait') {
        const secondsUntilNotice = Math.max(0, 8 - (e.waited || 0));
        if (secondsUntilNotice === 0) showCapacityWait(m, true);
        else if (e.seconds > secondsUntilNotice)
          capacityTimer = setTimeout(() => showCapacityWait(m, true), secondsUntilNotice * 1000);
      } else {
        showCapacityWait(m, false);
      }
      if (visible) button();
    } else if (e.type === 'steering') {
      run.canSteer = e.available;
      if (visible) {
        if (e.note) toast(e.note);
        button();
      }
    } else if (e.type === 'steer') {
      const m = c.messages.at(-1);
      (m.steers ??= []).push({ text: e.text, status: e.status });
      if (visible) $('#messages .msg:last-child .run-info').innerHTML = runInfoHtml(m);
      if (visible && e.status === 'failed') toast(e.note || t("Your update could not be applied.", "未能应用追加指令。"));
    } else if (e.type === 'compacting') {
      c.messages.at(-1).progress = 'Compacting earlier context…';
      if (visible) updateAnswer();
    } else if (e.type === 'context') {
      c.messages.at(-1).context_usage = e;
      if (visible) note();
    } else if (e.type === 'delta') {
      c.messages.at(-1).text += e.text;
      if (visible) updateAnswer();
    } else if (e.type === 'search' || e.type === 'reasoning') {
      const m = c.messages.at(-1);
      m.progress = e.type === 'reasoning' ? 'Thinking…' : e.status === 'searching' ? 'Searching the web…' : 'Preparing answer…';
      if (visible && !m.text) updateAnswer();
    } else if (e.type === 'agent_activity') {
      applyAgentActivity(c.messages.at(-1), e);
      if (visible) agentPanel.tick();
    } else if (e.type === 'summary' || e.type === 'subagent') {
      const m = c.messages.at(-1);
      if (e.type === 'summary') m.reasoning_summary = (m.reasoning_summary || '') + e.text;
      else if (!(m.subagents ??= []).includes(e.name)) m.subagents.push(e.name);
      if (!visible) return;
      if (e.type === 'subagent') agentPanel.refresh();
      const info = $('#messages .msg:last-child .run-info');
      const wasOpen = info.querySelector('details')?.open;
      info.innerHTML = runInfoHtml(m);
      if (wasOpen) info.querySelector('details').open = true;
    } else if (['workspace', 'approval', 'terminal', 'review'].includes(e.type)) {
      const m = c.messages.at(-1);
      const waiting = !!m.approval;
      updateWorkspaceEvent(m, e, visible ? $('#messages .msg:last-child .workspace-log') : null);
      if (waiting !== !!m.approval) drawList();
      if (visible) updateAnswer();
    } else if (e.type === 'finish') {
      finished = true;
      outcome = e.status;
      const m = c.messages.at(-1);
      if (typeof e.text === 'string') m.text = e.text;
      delete m.approval;
      delete m.terminalRun;
      delete m.reviewProgress;
      clearTimeout(capacityTimer);
      delete m.waitingForCapacity;
      Object.assign(m, { status: e.status, usage: e.usage, note: e.note, reason: e.reason, sources: e.sources, rate_limit: e.rate_limit });
      for (const key of ['reasoning_summary', 'subagents', 'agent_activity', 'workspace_activity', 'artifacts', 'context_usage', 'compaction', 'steers']) {
        if (e[key]) m[key] = e[key];
      }
    }
  }
  try {
    if (!S.status.configured) {
      await refresh();
      if (!S.status.configured) {
        if (S.chat === c) openGuide(ui);
        toast(t("Follow the setup guide to add your API key. Your draft is still in the composer.", "请按设置指南添加 API 密钥。草稿还在输入框里。"));
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
        if (S.chat === c) openGuide(ui);
      }
      throw new Error(detail.error || t("Could not send the message.", "消息未能发送。"));
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
    if (!finished) throw new Error(t("The connection to Relay closed before the response finished.", "回复完成前与 Relay 的连接已断开。"));
  } catch (e) {
    if (started && !finished) {
      const m = c.messages.at(-1);
      if (m.terminalRun) m.terminalRun.interrupted = true;
      Object.assign(m, { status: 'error', note: e.message, approval: null, reviewProgress: null });
    }
    else toast(S.chat === c ? e.message : c.title + ' · ' + e.message);
  } finally {
    clearTimeout(capacityTimer);
    runs.delete(c);
    if (!started && c === newChat) {
      pendingChats.delete(c.pendingKey);
      c.title = 'New chat';
    }
    if (S.chat === c) {
      renderModelControls();
      const t = $('#thread');
      draw(t.scrollHeight - t.clientHeight - t.scrollTop < 120);
      if (!$('#dialog').open) $('#draft').focus({ preventScroll: true });
    } else {
      if (c.id || c.temporary || pendingChats.has(c.pendingKey)) unread.set(dkey(c), outcome);
      drawList();
    }
    if (started) balanceChanged().catch((e) => toast(e.message));
    if (!c.temporary) refreshRunHistory();
  }
}
async function readAttachment(f, model) {
  const textExt = /\.(txt|md|csv|tsv|json|jsonl|yaml|yml|xml|html|css|js|mjs|jsx|ts|tsx|py|rs|go|java|c|h|cpp|hpp|cs|sql|sh|ps1|toml|ini|log|tex|r|kt|swift|vue|svelte|ipynb)$/i;
  const img = /^image\/(png|jpeg|webp|gif)$/.test(f.type);
  const pdf = f.type === 'application/pdf' || /\.pdf$/i.test(f.name);
  if (pdf && model?.pdf === false) throw new Error(t("Choose a model with PDF input before attaching this file.", "请先选择支持 PDF 输入的模型，再添加此文件。"));
  if (!img && !pdf && !f.type.startsWith('text/') && !textExt.test(f.name)) {
    throw new Error(t("Choose a text file, source file, PDF or supported image.", "请选择文本文件、源码文件、PDF 或受支持的图片。"));
  }
  if (f.size > (img || pdf ? 3 * 1024 * 1024 : 400000)) {
    throw new Error(img || pdf ? t("Each image or PDF can be up to 3 MB.", "每张图片或每个 PDF 最大为 3 MB。") : t("Each text file can contain up to 100,000 characters.", "每个文本文件最多可包含 100,000 个字符。"));
  }
  const data = img || pdf
    ? await new Promise((ok, no) => {
        const reader = new FileReader();
        reader.onload = () => ok(reader.result);
        reader.onerror = () => no(new Error(t("Could not read the attachment.", "无法读取附件。")));
        reader.readAsDataURL(pdf ? f.slice(0, f.size, 'application/pdf') : f);
      })
    : await f.text();
  if (!img && !pdf && data.length > 100000) throw new Error(t("Each text file can contain up to 100,000 characters.", "每个文本文件最多可包含 100,000 个字符。"));
  return { name: f.name || t("Pasted image", "粘贴的图片"), kind: pdf ? 'pdf' : img ? 'image' : 'text', data };
}
async function addFiles(incoming) {
  if (S.busy || !incoming.length) return;
  const key = dkey(), files = S.files, model = currentModel(S.status.settings.model);
  drafts.set(key, { value: $('#draft').value, files });
  readingFiles.set(key, (readingFiles.get(key) || 0) + 1);
  button();
  try {
    for (const f of incoming) {
      try {
        const attachment = await readAttachment(f, model);
        if (files.length >= 4) {
          toast(t("You can attach up to four files per message.", "每条消息最多可添加四个附件。"));
          break;
        }
        files.push(attachment);
      } catch (error) {
        toast(error.message);
      }
    }
  } finally {
    const remaining = readingFiles.get(key) - 1;
    if (remaining) readingFiles.set(key, remaining);
    else readingFiles.delete(key);
    if (key === dkey()) {
      drawChips();
      button();
    }
  }
}
async function exportChat() {
  const c = S.chat,
    transcript = c.messages
      .map(
        (m) =>
          '## ' +
          (m.role === 'user' ? 'You' : 'Relay') +
          '\n\n' +
          m.text +
          (m.files?.length ? '\n\nAttachment: ' + m.files.map((f) => f.name).join(', ') : '') +
          (m.note ? '\n\n> ' + (rateLimitDiagnostic(m.note, m.rate_limit)?.detail || m.note) : ''),
      )
      .join('\n\n');
  const content = '# ' + c.title + '\n\n' + transcript + '\n';
  if (S.status.settings.export_directory) {
    try {
      const { path } = await api('/api/export', { title: c.title, markdown: content });
      toast(t('Saved to {path}', '已保存到 {path}', { path }));
    } catch (err) {
      toast(err.message);
    }
    return;
  }
  const url = URL.createObjectURL(
    new Blob([content], { type: 'text/markdown;charset=utf-8' }),
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = (c.title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 70) || 'Relay') + '.md';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  toast(t("Markdown exported.", "已导出 Markdown。"));
}
function manage() {
  if (busyGuard()) return;
  const c = S.chat;
  const b = modal(
    t("CONVERSATION", "对话"),
    t("Manage chat", "管理聊天"),
    `<div class="management">
    ${c.id ? `<label class="form-row">${t('Chat title', '聊天标题')}<input class="field" id="rename-value" maxlength="100" value="${esc(c.title)}"></label><button class="secondary" id="rename">${icon('edit')} ${t('Save title', '保存标题')}</button><button class="secondary" id="pin">${icon('pin')} ${c.pinned ? t("Unpin chat", "取消置顶") : t("Pin chat", "置顶聊天")}</button>` : ''}
    <button class="secondary" id="export">${icon('download')} ${t('Export Markdown', '导出 Markdown')}</button><button class="danger" id="delete">${icon('trash')} ${c.temporary ? t("Clear temporary chat", "清空临时聊天") : t("Delete chat", "删除聊天")}</button></div>`,
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
    const target = c.temporary ? { temporary_session: c.temporarySession } : { id: c.id };
    const saved = c.id || c.temporary;
    let plan;
    try {
      plan = saved ? await api('/api/chat/delete-preview', target) : { project_files: [] };
    } catch (e) {
      return toast(e.message);
    }
    if (!$('#dialog').open || S.chat !== c) return;
    const files = plan.project_files;
    const confirmBody = modal(t('CONVERSATION', '对话'), c.temporary ? t('Clear temporary chat?', '清空临时聊天？') : t('Delete chat?', '删除聊天？'),
      `<p>${t('Permanently delete the messages, attachments and draft in this chat.', '永久删除此聊天的消息、对话附件和草稿。')}</p>
      <div class="project-options">
        ${plan.shared_folder ? `<p>${t('This folder is shared with a project and will be kept.', '此文件夹与项目共用，删除后将保留。')} <small>${esc(plan.shared_folder)}</small></p>` : ''}
        ${plan.folder ? `<label><input type="checkbox" id="delete-folder" checked><span><strong>${t('Also delete the entire chat folder', '同时删除整个聊天文件夹')}</strong>
          <small>${esc(plan.folder)}</small><small>${t('Includes all files and subfolders at this path.', '包括该路径下的所有文件及子文件夹。')}</small></span></label>` : ''}
        ${files.length ? `<label><input type="checkbox" id="delete-project-files"><span><strong>${t('Also delete files this chat created in your projects', '同时删除此聊天在项目中创建的文件')}</strong>
          <small>${t('Empty folders created with these files are also removed.', '同时清理创建这些文件时新建的空文件夹。')}</small></span></label>
          <details><summary>${t('View {n} project files', '查看 {n} 个项目文件', { n: files.length })}</summary><ul class="cleanup-paths">${files.map(file => '<li>' + esc(file) + '</li>').join('')}</ul></details>` : ''}
        ${files.length || usesProjectFolder(c.workspace_mode) ? `<p>${t('Project cleanup covers files Relay recorded creating with its file tools. Terminal output in your project and separately saved copies stay on disk.', '项目清理只覆盖 Relay 文件工具记录创建的文件。项目中的终端产物和另存的副本会保留。')}</p>` : ''}
      </div><div class="modal-actions"><button class="secondary" id="cancel-delete">${t('Cancel', '取消')}</button>
      <button class="danger" id="confirm-delete">${icon('trash')} ${c.temporary ? t('Clear chat', '清空聊天') : t('Delete chat', '删除聊天')}</button></div>`);
    confirmBody.querySelector('#cancel-delete').onclick = close;
    confirmBody.querySelector('#confirm-delete').onclick = async event => {
      const submit = event.currentTarget;
      submit.disabled = true;
      try {
        const result = saved ? await api('/api/chat/delete', { ...target,
          remove_folder: !!confirmBody.querySelector('#delete-folder')?.checked,
          project_files: confirmBody.querySelector('#delete-project-files')?.checked ? files : [] }) : {};
        const key = dkey(c);
        pendingChats.delete(c.pendingKey);
        if (c.temporary) {
          S.temp = blank(true);
          swap(S.temp);
        } else openNew();
        drafts.delete(key);
        if (c.temporary) {
          $('#draft').value = '';
          S.files = [];
          draw();
        }
        await listChats();
        close();
        if (result.changed?.length) toast(t('Kept {n} project files changed since review.', '保留了确认期间发生变化的 {n} 个项目文件。', { n: result.changed.length }));
      } catch (e) {
        submit.disabled = false;
        toast(e.message);
      }
    };
    confirmBody.querySelector('#cancel-delete').focus();
  };
}
function search() {
  if (matchMedia('(max-width:760px)').matches) $('#layout').classList.add('mobile-nav');
  else $('#layout').classList.remove('side-hidden');
  $('#search-wrap').hidden = false;
  $('#history-search').focus();
}
function action(name) {
  if (name === 'new') {
    openNew();
  } else if (name === 'temp') swap(S.temp);
  else if (name === 'balance') openBalance(ui).catch((e) => toast(e.message));
  else if (name === 'guide') openGuide(ui);
  else if (name === 'settings') openSettings(ui);
  else if (name === 'import') openImporter(ui).catch((e) => toast(e.message));
  else if (name === 'retry') void send(true);
  else if (name === 'continue') void send(false, true);
  else if (name === 'manage') manage();
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.action) action(b.dataset.action);
  else if (b.dataset.pendingChat) swap(pendingChats.get(b.dataset.pendingChat));
  else if (b.dataset.chat) openChat(b.dataset.chat).catch((e) => toast(e.message));
  else if ('fork' in b.dataset && !S.busy) {
    b.disabled = true;
    (async () => {
      const result = await api('/api/chat/fork', { id: S.chat.id, through: Number(b.dataset.fork) });
      await openChat(result.id);
      await listChats();
      toast(t("Opened a new branch.", "已打开新的对话分支。"));
    })().catch(err => toast(err.message)).finally(() => { b.disabled = false; });
  } else if ('decision' in b.dataset) {
    const chat = S.chat, run = runs.get(chat);
    const review = chat.messages.at(-1)?.approval;
    if (!review || review.submitting || !run?.id) return;
    review.submitting = true;
    const buttons = b.parentElement.querySelectorAll('button');
    buttons.forEach(el => { el.disabled = true; });
    drawList();
    api('/api/workspace/decision', { run: run.id, id: review.id, allow: b.dataset.decision === 'allow' })
      .catch(err => {
        review.submitting = false;
        drawList();
        if (S.chat === chat) draw();
        toast(chat === S.chat ? err.message : chat.title + ' · ' + err.message);
      });
  }
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
$('#steer').onclick = () => void sendSteer();
$('#workspace').onclick = () => {
  closeComposerMenu(true);
  showWorkspace(ui, chatWorkspace(), saveWorkspace);
};
for (const [id, field] of [['#auto-review', 'autoReview'], ['#terminal-commands', 'terminal']]) {
  $(id).onclick = () => {
    const access = chatWorkspace();
    saveWorkspace({ ...access, [field]: !access[field] }).catch(err => toast(err.message));
  };
}
for (const trigger of [$('#response-settings'), $('#tools-toggle')]) {
  trigger.onclick = () => {
    if (openPanel === trigger) closeComposerMenu(true);
    else openComposerMenu(trigger);
  };
  trigger.onkeydown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      openComposerMenu(trigger);
    }
  };
}
for (const [field, { trigger }] of Object.entries(modelControls)) {
  trigger.onclick = () => showModelMenu(field);
}
$('#choice-back').onclick = () => showOverview(openChoice);
modelSearch.oninput = drawModelOptions;
choiceOptions.onclick = (e) => {
  const option = e.target.closest('.choice-option');
  if (!option) return;
  const field = openChoice;
  const { value } = option.dataset;
  closeComposerMenu(true);
  if (value === modelControls[field].selected) return;
  const patch = field === 'model'
    ? modelSettings(catalog.models.find(({ id }) => id === value))
    : { [field]: value };
  void updateModelSettings(patch);
};
choiceMenu.onkeydown = (e) => {
  if (e.isComposing) return;
  const options = [...choiceOptions.querySelectorAll('.choice-option')];
  if (!options.length) return;
  if (e.target === modelSearch && e.key === 'Enter') {
    e.preventDefault();
    options[0].click();
    return;
  }
  const current = options.indexOf(e.target);
  let next;
  if (e.key === 'ArrowDown') next = (current + 1) % options.length;
  else if (e.key === 'ArrowUp') next = current > 0 ? current - 1 : options.length - 1;
  else if (current >= 0 && e.key === 'Home') next = 0;
  else if (current >= 0 && e.key === 'End') next = options.length - 1;
  else return;
  e.preventDefault();
  options[next].focus();
};
function closeChoiceOutside(e) {
  if (openPanel && !openPanel.parentElement.contains(e.target)) closeComposerMenu();
}
document.addEventListener('pointerdown', closeChoiceOutside);
document.addEventListener('focusin', closeChoiceOutside);
document.addEventListener('keydown', e => {
  if (!openPanel || e.isComposing || e.key !== 'Escape') return;
  e.preventDefault();
  if (openChoice) showOverview(openChoice);
  else closeComposerMenu(true);
});
window.addEventListener('resize', positionComposerMenu);
$('#refresh-models').onclick = () => void loadModels();
webSearch.onclick = () => void updateModelSettings({ web_search: !S.status.settings.web_search });
$('#draft').oninput = () => {
  drafts.set(dkey(), { value: $('#draft').value, files: S.files });
  resize();
  button();
};
$('#draft').onkeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    if (S.busy) void sendSteer();
    else void send();
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
window.addEventListener('relay-languagechange', () => {
  $('#connection b').textContent = S.status.configured ? t('Key found', '已找到密钥') : t('Set up API', '配置 API');
  renderModelControls();
  if (S.balance) showBalanceState(S.balance);
  draw();
});
window.addEventListener('focus', () => refresh().catch(() => {}));
window.addEventListener('pagehide', event => {
  if (!event.persisted && (S.temp.messages.length || runs.has(S.temp))) {
    void fetch('/api/chat/release-temporary', { method: 'POST', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ temporary_session: S.temp.temporarySession }) }).catch(() => {});
  }
});
paint();
theme();
draw();
try {
  const initialHash = location.hash;
  if (initialHash === '#temporary') swap(S.temp);
  const initialChat = initialHash.startsWith('#chat/') ? openChat(initialHash.slice(6)) : undefined;
  await Promise.all([initialChat, refresh(), listChats(), drafts.restore().then(() => {
    $('#draft').value = drafts.get(dkey()).value;
    resize();
    button();
  })]);
} catch (e) {
  toast(e.message);
}
