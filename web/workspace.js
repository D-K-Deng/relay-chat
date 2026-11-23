import { systemMessage } from './system-messages.js';
import { t } from './i18n.js';
import { esc, icon } from './format.js';

const modes = () => ({ chat: t("Chat folder", "聊天文件夹"), read: t("Read files", "读取文件"), edit: t("Review edits", "审阅修改"), off: t("No local tools", "关闭本地工具") });
export const usesProjectFolder = mode => mode === 'read' || mode === 'edit';

export function showWorkspace(ui, access, save) {
  const settings = ui.state.status.settings;
  const chat = ui.state.chat;
  const body = ui.modal(t("LOCAL FILES", "本地文件"), t("Workspace", "工作区"), `
    <form id="project-form">
      <fieldset class="project-modes"><legend>${t("File access for this chat", "此聊天的文件权限")}</legend>
        ${Object.entries(modes()).filter(([key]) => key !== 'chat' || !chat.temporary).map(([key, label]) => `<label><input type="radio" name="mode" value="${key}" ${key === access.mode ? 'checked' : ''}>
          <span><strong>${label}</strong><small>${{ chat: t("Work in a folder Relay creates for this conversation.", "使用 Relay 为这次聊天创建的文件夹。"), off: t("Chat with local tools turned off.", "仅聊天，关闭本地工具。"), read: t("Find and read files in a project you choose.", "在选定的项目中查找并读取文件。"), edit: t("Read and edit files in a project you choose.", "在选定的项目中读取和编辑文件。") }[key]}</small></span></label>`).join('')}
      </fieldset>
      <div id="project-location">
        <label class="form-row" for="project-folder">${t("Project folder", "项目文件夹")}</label>
        <div class="folder-field">
          <input class="field" id="project-folder" placeholder="${t("Full folder path", "完整文件夹路径")}" value="${esc(access.directory || settings.workspace_directory || '')}">
          ${ui.state.status.workspace_picker ? `<button class="secondary" type="button" id="project-browse">${t("Browse…", "浏览…")}</button>` : ''}
        </div>
      </div>
      ${chat.chat_folder ? `<label class="form-row" id="chat-location">${t("Chat folder path", "聊天文件夹路径")}<input class="field" readonly value="${esc(chat.chat_folder)}"></label>` : ''}
      <div class="project-options">
        <label><input type="checkbox" id="workspace-terminal" ${access.terminal ? 'checked' : ''}>
          <span><strong>${t("Terminal commands", "终端命令")}</strong><small id="terminal-hint"></small></span></label>
        <label><input type="checkbox" id="workspace-auto-review" ${access.autoReview ? 'checked' : ''}>
          <span><strong>${t("Auto Review", "自动审阅")}</strong><small id="review-hint"></small></span></label>
      </div>
      <p class="form-help">${t("Requested file excerpts and terminal output are sent to your OpenAI API project.", "选取的文件片段和终端输出会发送到你的 OpenAI API 项目。")}</p>
      <p class="form-error" role="alert"></p>
      <button class="primary" type="submit">${t("Use these settings", "保存设置")}</button>
    </form>`);
  const form = body.querySelector('form');
  const folder = form.querySelector('#project-folder');
  const browse = form.querySelector('#project-browse');
  const terminal = form.querySelector('#workspace-terminal');
  const autoReview = form.querySelector('#workspace-auto-review');
  function updateOptions() {
    const mode = form.elements.mode.value;
    form.querySelector('#project-location').hidden = !usesProjectFolder(mode);
    const location = form.querySelector('#chat-location');
    if (location) location.hidden = mode !== 'chat';
    terminal.disabled = mode === 'off';
    autoReview.disabled = mode === 'off' || (mode === 'read' && !terminal.checked);
    form.querySelector('#terminal-hint').textContent = mode === 'read'
      ? t("Inspect the current branch, recent commits and tracked files with read-only Git queries.", "通过只读 Git 命令查看当前分支、最近提交和已跟踪的文件。")
      : t("Run supported Git commands, tests and builds. Project code runs with your user permissions after you approve.", "运行支持的 Git 命令、测试和构建。你批准后，项目代码会以你的系统权限执行。");
    form.querySelector('#review-hint').textContent = mode === 'read'
      ? t("Allow eligible read-only Git queries automatically.", "自动允许符合条件的只读 Git 命令。")
      : t("Use a separate, billable API request to review low-risk edits. Eligible Git queries use local rules. Tests and builds still ask you.", "通过单独计费的 API 请求审阅低风险修改。符合条件的 Git 命令使用本地规则；测试和构建仍需你批准。");
  }
  form.addEventListener('change', event => {
    if (event.target.name === 'mode' && access.mode === 'off' && terminal.disabled && form.elements.mode.value !== 'off') {
      terminal.checked = settings.workspace_terminal;
      autoReview.checked = settings.workspace_auto_review;
    }
    updateOptions();
  });
  updateOptions();
  if (browse) browse.onclick = async () => {
    browse.disabled = true;
    try {
      const { directory } = await ui.api('/api/workspace/pick', {});
      if (directory) folder.value = directory;
    } catch (error) {
      form.querySelector('.form-error').textContent = error.message;
    } finally {
      browse.disabled = false;
    }
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    const mode = form.elements.mode.value;
    if (usesProjectFolder(mode) && !folder.value.trim()) {
      form.querySelector('.form-error').textContent = t("Choose the project folder you want Relay to work in.", "请选择 Relay 要使用的项目文件夹。");
      folder.focus();
      return;
    }
    const submit = form.querySelector('[type=submit]');
    submit.disabled = true;
    try {
      await save({ mode, directory: usesProjectFolder(mode) ? folder.value.trim() : '',
        terminal: mode !== 'off' && terminal.checked, autoReview: mode !== 'off' && autoReview.checked });
      ui.close();
    } catch (error) {
      form.querySelector('.form-error').textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  };
}

export function workspaceButton(button, access, busy) {
  const project = usesProjectFolder(access.mode);
  const folder = access.directory.split(/[\\/]/).filter(Boolean).pop() || access.directory;
  button.querySelector('span').textContent = project ? `${modes()[access.mode]} · ${folder}` : access.mode === 'chat' ? modes().chat : t("Workspace", "工作区");
  button.classList.toggle('active', project);
  button.title = project ? t('{mode} in {path}', '{mode} · {path}', { mode: modes()[access.mode], path: access.directory })
    : access.mode === 'chat' ? t("Work in this chat’s own folder. Choose a different workspace.", "当前使用此聊天的专属文件夹。点击切换工作区。") : t("Choose where Relay works", "选择 Relay 的工作文件夹");
  button.disabled = busy;
}

function diffHtml(diff) {
  return `<pre class="project-diff" tabindex="0" aria-label="${t('File changes', '文件更改')}">` + diff.split(/\r?\n/).map(line => {
    const kind = line.startsWith('@@') ? 'range' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'remove' : '';
    return `<span class="${kind}">${esc(line)}</span>`;
  }).join('\n') + '</pre>';
}

const toolLabels = () => ({ find_files: t("Find files", "查找文件"), search_text: t("Search text", "搜索内容"), read_file: t("Read file", "读取文件"), edit_file: t("Edit file", "编辑文件"), create_file: t("Create file", "创建文件"), run_terminal: t("Terminal", "终端") });

function commandText(argv) {
  return (argv || []).map(arg => /^[\w./:=@+-]+$/.test(arg) ? arg : JSON.stringify(arg)).join(' ');
}

function commandHtml(step) {
  const resolved = step.effective_argv?.length ? `<details class="terminal-details"><summary>${t("Resolved command", "实际执行的命令")}</summary><code class="terminal-command">${esc(commandText(step.effective_argv))}</code></details>` : '';
  return `<code class="terminal-command">${esc(step.command || commandText(step.argv))}</code><small class="terminal-cwd">${t('Working directory', '工作目录')} · ${esc(step.directory || (step.cwd === '.' ? t("Project root", "项目根目录") : step.cwd))}</small>${resolved}`;
}

function reviewNote(review) {
  if (!review) return '';
  const who = review.source === 'auto' ? t("Auto Review", "自动审阅") : t("Manual review", "手动审阅");
  return `<p class="review-note"><strong>${who}</strong>${review.reason ? ' · ' + esc(systemMessage(review.reason)) : ''}</p>`;
}

function activityItem(step) {
  const info = `<small>${esc(systemMessage(step.detail || step.status))}</small>`;
  if (step.tool === 'run_terminal') {
    const exit = step.exit_code == null ? '' : t(' · Exit {code}', ' · 退出码 {code}', { code: esc(step.exit_code) });
    const output = step.output ? `<pre class="terminal-output" tabindex="0">${esc(step.output)}</pre>` : '';
    return `<li><details><summary><strong>${t("Terminal", "终端")}</strong> ${esc(commandText(step.argv))}${exit}</summary>
      ${info}${commandHtml(step)}${reviewNote(step.review)}${output}</details></li>`;
  }
  const label = `<strong>${esc(toolLabels()[step.tool] || step.tool)}</strong> ${esc(step.path || '')}`;
  const body = info + reviewNote(step.review);
  return `<li>${step.diff ? `<details><summary>${label}</summary>${body}${diffHtml(step.diff)}</details>` : label + body}</li>`;
}
export function workspaceHtml(message) {
  const steps = message.workspace_activity || [];
  let html = '';
  if (steps.length) {
    const items = steps.map(activityItem).join('');
    html = `<details class="project-activity"><summary>${icon('folder')} ${t('Workspace activity · {count} {unit}', '工作区操作 · {count} 步', { count: steps.length, unit: steps.length === 1 ? 'step' : 'steps' })}</summary><ol>${items}</ol></details>`;
  }
  const review = message.approval;
  if (review) {
    const command = review.tool === 'run_terminal';
    const heading = command ? t("Run terminal command?", "运行此终端命令？") : `${review.tool === 'create_file' ? t("Create", "创建") : t("Edit", "编辑")} ${esc(review.path)}`;
    html += `<section class="project-review" aria-label="${command ? t("Review terminal command", "审阅终端命令") : t("Review file change", "审阅文件修改")}"><strong>${heading}</strong>
      ${command ? commandHtml(review) : diffHtml(review.diff)}
      ${review.review_reason ? `<p class="review-note">${esc(systemMessage(review.review_reason))}</p>` : ''}
      <div class="review-actions">
      <button class="secondary" data-decision="allow" ${review.submitting ? 'disabled' : ''}>${command ? t("Run command", "运行命令") : t("Apply change", "应用修改")}</button>
      <button class="secondary" data-decision="deny" ${review.submitting ? 'disabled' : ''}>${t("Decline", "拒绝")}</button></div></section>`;
  }
  if (message.terminalRun) {
    const run = message.terminalRun;
    html += `<section class="terminal-live"><strong>${run.interrupted ? t("Terminal output before disconnect", "断开前的终端输出") : t("Running command", "正在运行命令")}</strong>
      ${commandHtml(run)}<pre class="terminal-output" tabindex="0" aria-label="${t("Command output", "命令输出")}">${esc(run.output)}</pre></section>`;
  }
  if (message.reviewProgress) {
    html += `<p class="review-progress" role="status"><strong>${t("Auto Review", "自动审阅")}</strong> · ${esc(systemMessage(message.reviewProgress))}</p>`;
  }
  return html;
}

export function updateWorkspaceEvent(message, event, log) {
  delete message.reviewProgress;
  if (event.type === 'terminal') {
    if (message.terminalRun && message.terminalRun.id === event.id) {
      message.terminalRun.output += event.text;
      const output = log?.querySelector('.terminal-live .terminal-output');
      if (!output) return;
      const nearEnd = output.scrollHeight - output.scrollTop - output.clientHeight < 30;
      output.append(document.createTextNode(event.text));
      if (nearEnd) output.scrollTop = output.scrollHeight;
      return;
    }
    message.terminalRun = { ...event, output: event.text };
    delete message.approval;
    message.progress = 'Running terminal command…';
  } else if (event.type === 'review') {
    message.reviewProgress = event.reason || 'Checking the proposed action…';
  } else if (event.type === 'approval') {
    message.approval = event;
    message.progress = 'Waiting for your review…';
  } else {
    (message.workspace_activity ??= []).push(event);
    delete message.approval;
    delete message.terminalRun;
    message.progress = event.detail || 'Working in the project…';
  }
  if (!log) return;
  const wasOpen = log.querySelector('.project-activity')?.open;
  log.innerHTML = workspaceHtml(message);
  if (wasOpen) log.querySelector('.project-activity').open = true;
  if (event.type === 'approval') log.querySelector('.project-review').scrollIntoView({ block: 'nearest' });
}
