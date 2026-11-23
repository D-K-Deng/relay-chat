import { esc, icon } from './format.js';
import { t, language } from './i18n.js';
const credits = 'https://developers.openai.com/api/docs/guides/chatgpt-api-credits';
const billing = 'https://platform.openai.com/settings/organization/billing/overview';
const usage = 'https://platform.openai.com/usage';
const link = (href, label) =>
  `<a href="${href}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`;
export function openGuide(ui) {
  const b = ui.modal(
    t("GET STARTED", "开始使用"),
    t("Connect your API credits", "连接 API 额度"),
    `
    <p class="lead">${t("Set up Relay once on this computer using your own API organization and key.", "使用你自己的 API 组织和密钥，在这台电脑上设置 Relay。")}</p>
    <ol class="steps">
      <li><span class="step-num">01</span><div><h3>${t("Claim your credits", "领取额度")}</h3><p>${t("Eligible Pro and Business owners can link an API organization from ChatGPT billing. Check the grant and its expiry in that organization.", "符合条件的 Pro 和 Business 用户可以在 ChatGPT 账单设置里关联 API 组织，然后到该组织查看额度和到期时间。")}</p>${link(credits, t("Eligibility and instructions", "资格与说明"))} · ${link(billing, t("Open OpenAI Billing", "打开 OpenAI 账单"))}</div></li>
      <li><span class="step-num">02</span><div><h3>${t("Let Codex set up Relay", "让 Codex 设置 Relay")}</h3><p>${t("Open this folder in Codex, enable OpenAI Developers, and copy the instruction below. Select a project in the organization that received the credits.", "在 Codex 里打开这个文件夹，启用 OpenAI Developers，复制下方指令，再选择已领取额度的组织中的项目。")}</p>
        <div class="prompt-box">${t("Read README.md and web/prompt.txt. Follow the prompt to configure and start Relay Chat on this computer using my OpenAI account.", "阅读 README.md 和 web/prompt.txt，按其中说明用我的 OpenAI 账户在这台电脑上配置并启动 Relay Chat。")}</div><button class="secondary" id="copy-setup">${icon('copy')} ${t("Copy setup instruction", "复制设置指令")}</button></div></li>
      <li><span class="step-num">03</span><div><h3>${t("Send your first message", "发送第一条消息")}</h3><p>${t("Open RelayChat.exe on Windows or RelayChat.app on Mac. For a source checkout, use Start.cmd on Windows or Start.command on Mac.", "在 Windows 上打开 RelayChat.exe，在 Mac 上打开 RelayChat.app。从源码运行时，Windows 使用 Start.cmd，Mac 使用 Start.command。")}</p>
        <button class="hint-btn" id="check-key">${t("Check local key", "检查本机密钥")}</button></div></li>
    </ol><div class="callout">${t("OpenAI applies eligible free credits first, then any purchased credits. Check the official balance and auto-recharge settings in Billing.", "OpenAI 优先使用符合条件的免费额度，然后使用购买的额度。你可以在账单页面查看官方余额和自动充值设置。")}</div>
    <div class="modal-links">${link(usage, t("OpenAI usage", "OpenAI 用量"))} · ${link('https://platform.openai.com/api-keys', t("Manage API keys", "管理 API 密钥"))}</div>`,
  );
  b.querySelector('#copy-setup').onclick = () =>
    ui.copy(
      t("Read README.md and web/prompt.txt. Follow the prompt to configure and start Relay Chat on this computer using my OpenAI account.", "阅读 README.md 和 web/prompt.txt，按其中说明用我的 OpenAI 账户在这台电脑上配置并启动 Relay Chat。"),
    );
  b.querySelector('#check-key').onclick = async () => {
    try {
      await ui.refresh();
      ui.toast(
        ui.state.status.configured
          ? t("Key found. Send a message to test the connection.", "已找到密钥。发送消息即可测试连接。")
          : t("No key found. Complete step 2 to connect your account.", "未找到密钥。请按第 2 步连接账户。"),
      );
    } catch (e) {
      ui.toast(e.message);
    }
  };
}

export function openSettings(ui, unsaved = {}) {
  const { settings, configured, key_source, workspace_picker, version } = ui.state.status;
  const s = { ...settings, ...unsaved };
  const summaryAvailable = ui.canSummarize();
  const theme = unsaved.theme || localStorage.getItem('relay.theme') || 'system';
  const options = (pairs, sel) =>
    pairs
      .map(
        ([v, label]) =>
          `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(label)}</option>`,
      )
      .join('');
  const b = ui.modal(
    version ? `Relay Chat ${version}` : 'Relay Chat',
    t("Settings", "设置"),
    `<div class="meta-box">${icon('key')}<span>${t("API Key", "API 密钥")}</span><strong>${configured ? t("Found in ", "来源：") + esc(key_source) : t("Not configured", "尚未配置")}</strong></div>
    <div class="modal-links"><button class="hint-btn" id="how">${t("Setup guide", "设置指南")}</button> · ${link(billing, t("OpenAI Billing", "OpenAI 账单"))} · ${link(usage, t("OpenAI usage", "OpenAI 用量"))}</div><div class="divider"></div>
    <form id="settings-form">
    <label class="form-row" for="ui-language">${t("Language", "界面语言")}<select class="field" name="language" id="ui-language">${options([['en', 'English'], ['zh-CN', '简体中文']], language())}</select></label>
    <div class="grid2"><label class="form-row">${t("Conversation context", "对话上下文")}<select class="field" name="context">${options(
      [
        [0, t("Automatic", "自动")],
        ...[8, 16, 32, 64].map(n => [n, t('{n} messages', '{n} 条消息', { n })]),
      ],
      s.context,
    )}</select></label>
    <label class="form-row">${t("Maximum output tokens", "最大输出词元数")}<input class="field" type="number" min="256" max="272000" step="1" name="max_output" value="${s.max_output || ''}" placeholder="${t("Model default", "使用模型默认值")}"></label></div>
    <p class="form-help">${t("Automatic context follows the Codex model budget when available and keeps the newest messages that fit. Leave output blank to use the model’s default.", "自动模式会参考 Codex 的模型上下文上限，保留可容纳的最近消息。输出上限留空时使用模型默认值。")}</p>
    <label class="summary-setting"><input type="checkbox" name="reasoning_summary" ${s.reasoning_summary ? 'checked' : ''} ${summaryAvailable ? '' : 'disabled'}> ${t("Show reasoning summaries", "显示推理摘要")}</label>
    <p class="form-help">${summaryAvailable ? t("Request a short account of the model’s approach when the API provides one.", "请求 API 返回模型推理过程的简短摘要。") : t("Available on supported reasoning models in single-agent mode with reasoning enabled.", "适用于已启用推理、且处于单智能体模式的受支持模型。")}</p>
    <label class="summary-setting"><input type="checkbox" name="auto_wait_rate_limits" ${s.auto_wait_rate_limits !== false ? 'checked' : ''}> ${t("Automatically wait for temporary rate limits", "遇到临时限流自动等待")}</label>
    <label class="form-row">${t("Custom instructions (optional)", "自定义指令（可选）")}<textarea class="field" name="instructions" maxlength="12000" placeholder="${t("For example, use metric units.", "例如，使用公制单位。")}" aria-describedby="instructions-help">${esc(s.instructions)}</textarea></label>
    <p class="form-help" id="instructions-help">${t("Relay follows the language of your request. Add any standing preferences here.", "Relay 会使用你提问的语言回答。可以在这里填写长期偏好。")}</p>
    <label class="form-row">${t("Appearance", "外观")}<select class="field" name="theme">${options(
      [
        ['system', t("Use system theme", "跟随系统")],
        ['light', t("Light", "浅色")],
        ['dark', t("Dark", "深色")],
      ],
      theme,
    )}</select></label>
    <div class="form-row">
      <label for="workspace-folder">${t("Markdown export folder", "Markdown 导出文件夹")}</label>
      <div class="folder-field">
        <input class="field" id="workspace-folder" name="export_directory" value="${esc(s.export_directory)}" placeholder="${t("Full folder path", "完整文件夹路径")}" aria-describedby="workspace-help">
        ${workspace_picker ? `<button class="secondary" type="button" id="browse-workspace">${t("Browse…", "浏览…")}</button>` : ''}
      </div>
      <p class="form-help" id="workspace-help">${t("Choose where to save chat exports. Use Workspace in the composer to give Relay access to project files.", "选择聊天导出文件的保存位置。要授权访问项目文件，请在输入框的工具菜单中设置工作区。")}</p>
    </div>
    <button class="primary" type="submit">${t("Save settings", "保存设置")}</button></form>
    <div class="divider"></div><p class="form-help">${t("Chat history is saved on this device. Temporary messages clear when the window closes. Both modes use store: false for OpenAI requests.", "聊天记录保存在本机。关闭窗口会清除临时消息。两种模式发送给 OpenAI 的请求均使用 store: false。")}</p>
    <button class="hint-btn" id="quit">${t("Quit local service", "退出本地服务")}</button>`,
  );
  b.querySelector('#how').onclick = () => openGuide(ui);
  const form = b.querySelector('form');
  form.elements.language.onchange = async () => {
    const selector = form.elements.language;
    selector.disabled = true;
    try {
      await ui.saveSettings({ language: selector.value });
      if (form.isConnected && b.closest('dialog').open) {
        const fields = Object.fromEntries(new FormData(form));
        fields.reasoning_summary = form.elements.reasoning_summary.checked;
        fields.auto_wait_rate_limits = form.elements.auto_wait_rate_limits.checked;
        openSettings(ui, fields);
        document.querySelector('#ui-language').focus();
      }
    } catch (error) {
      selector.disabled = false;
      selector.value = language();
      ui.toast(error.message);
    }
  };
  const browse = b.querySelector('#browse-workspace');
  if (browse) {
    const folder = b.querySelector('#workspace-folder');
    browse.onclick = async () => {
      browse.disabled = true;
      try {
        const { directory } = await ui.api('/api/workspace/pick', {});
        if (directory) folder.value = directory;
      } catch (err) {
        ui.toast(err.message);
      } finally {
        browse.disabled = false;
      }
    };
  }
  b.querySelector('form').onsubmit = async (e) => {
    e.preventDefault();
    const a = Object.fromEntries(new FormData(e.target));
    try {
      await ui.saveSettings({
        ...a,
        context: Number(a.context),
        max_output: Number(a.max_output),
        reasoning_summary: summaryAvailable ? a.reasoning_summary === 'on' : s.reasoning_summary,
        auto_wait_rate_limits: a.auto_wait_rate_limits === 'on',
      });
      localStorage.setItem('relay.theme', a.theme);
      ui.theme();
      await ui.refresh();
      ui.close();
      ui.toast(t("Settings saved.", "设置已保存。"));
    } catch (err) {
      ui.toast(err.message);
    }
  };
  b.querySelector('#quit').onclick = async () => {
    if (ui.state.anyBusy) return ui.toast(t("Stop the running responses before quitting Relay.", "退出 Relay 前，请先停止正在生成的回复。"));
    if (!confirm(t("Quit Relay and clear temporary messages in this window?", "退出 Relay 并清除此窗口中的临时消息？"))) return;
    try {
      await ui.api('/api/shutdown', {});
      location.replace('about:blank');
    } catch (e) {
      ui.toast(e.message);
    }
  };
}
