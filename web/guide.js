import { esc, icon } from './format.js';
const credits = 'https://developers.openai.com/api/docs/guides/chatgpt-api-credits';
const billing = 'https://platform.openai.com/settings/organization/billing/overview';
const usage = 'https://platform.openai.com/usage';
const link = (href, label) =>
  `<a href="${href}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`;
export function openGuide(ui) {
  const b = ui.modal(
    'GET STARTED',
    'Connect your API credits',
    `
    <p class="lead">Set up Relay once on this computer using your own API organization and key.</p>
    <ol class="steps">
      <li><span class="step-num">01</span><div><h3>Claim your credits</h3><p>Eligible Pro and Business owners can link an API organization from ChatGPT billing. Check the grant and its expiry in that organization.</p>${link(credits, 'Eligibility and instructions')} · ${link(billing, 'Open OpenAI Billing')}</div></li>
      <li><span class="step-num">02</span><div><h3>Let Codex set up Relay</h3><p>Open this folder in Codex, enable OpenAI Developers, and copy the instruction below. Select a project in the organization that received the credits.</p>
        <div class="prompt-box">Read README.md and web/prompt.txt. Follow the prompt to configure and start Relay Chat on this computer using my OpenAI account.</div><button class="secondary" id="copy-setup">${icon('copy')} Copy setup instruction</button></div></li>
      <li><span class="step-num">03</span><div><h3>Send your first message</h3><p>Open RelayChat.exe on Windows or RelayChat.app on Mac. For a source checkout, use Start.cmd on Windows or Start.command on Mac.</p>
        <button class="hint-btn" id="check-key">Check local key</button></div></li>
    </ol><div class="callout">OpenAI applies eligible free credits first, then any purchased credits. Check the official balance and auto-recharge settings in Billing.</div>
    <div class="modal-links">${link(usage, 'OpenAI usage')} · ${link('https://platform.openai.com/api-keys', 'Manage API keys')}</div>`,
  );
  b.querySelector('#copy-setup').onclick = () =>
    ui.copy(
      'Read README.md and web/prompt.txt. Follow the prompt to configure and start Relay Chat on this computer using my OpenAI account.',
    );
  b.querySelector('#check-key').onclick = async () => {
    try {
      await ui.refresh();
      ui.toast(
        ui.state.status.configured
          ? 'Key found. Send a message to test the connection.'
          : 'No key found. Complete step 2 to connect your account.',
      );
    } catch (e) {
      ui.toast(e.message);
    }
  };
}

export function openSettings(ui) {
  const { settings: s, configured, key_source } = ui.state.status;
  const theme = localStorage.getItem('relay.theme') || 'system';
  const options = (pairs, sel) =>
    pairs
      .map(
        ([v, label]) =>
          `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(label)}</option>`,
      )
      .join('');
  const b = ui.modal(
    'PREFERENCES',
    'Settings',
    `<div class="meta-box">${icon('key')}<span>API Key</span><strong>${configured ? 'Found in ' + esc(key_source) : 'Not configured'}</strong></div>
    <div class="modal-links"><button class="hint-btn" id="how">Setup guide</button> · ${link(billing, 'OpenAI Billing')} · ${link(usage, 'OpenAI usage')}</div><div class="divider"></div>
    <form id="settings-form"><label class="form-row">API model ID<input class="field" name="model" list="models" value="${esc(s.model)}" required maxlength="150"><datalist id="models"><option value="gpt-5.4-mini"><option value="gpt-5.4"><option value="gpt-5.4-nano"></datalist><span class="form-help">Enter any Responses model available to your project. ${link('https://developers.openai.com/api/docs/models', 'Browse models')}</span></label>
    <div class="grid2"><label class="form-row">Recent messages per request<select class="field" name="context">${options(
      [
        [8, '8 messages'],
        [16, '16 messages'],
        [32, '32 messages'],
        [64, '64 messages'],
        [0, 'All messages (higher usage)'],
      ],
      s.context,
    )}</select></label>
    <label class="form-row">Maximum output tokens<input class="field" type="number" min="256" max="32768" step="1" name="max_output" value="${s.max_output}" required></label></div>
    <label class="form-row">Custom instructions (optional)<textarea class="field" name="instructions" maxlength="12000" placeholder="For example, give the short answer first, then an example.">${esc(s.instructions)}</textarea></label>
    <label class="form-row">Appearance<select class="field" name="theme">${options(
      [
        ['system', 'Use system theme'],
        ['light', 'Light'],
        ['dark', 'Dark'],
      ],
      theme,
    )}</select></label>
    <button class="primary" type="submit">Save settings</button></form>
    <div class="divider"></div><p class="form-help">Saved chats stay on your device. Temporary messages clear with the tab. Both modes use store: false for OpenAI requests.</p>
    <button class="hint-btn" id="quit">Quit local service</button>`,
  );
  b.querySelector('#how').onclick = () => openGuide(ui);
  b.querySelector('form').onsubmit = async (e) => {
    e.preventDefault();
    const a = Object.fromEntries(new FormData(e.target));
    try {
      await ui.api('/api/settings', {
        ...a,
        context: Number(a.context),
        max_output: Number(a.max_output),
      });
      localStorage.setItem('relay.theme', a.theme);
      ui.theme();
      await ui.refresh();
      ui.close();
      ui.toast('Settings saved for the next request.');
    } catch (err) {
      ui.toast(err.message);
    }
  };
  b.querySelector('#quit').onclick = async () => {
    if (ui.state.busy) return ui.toast('Stop the current response first.');
    if (!confirm('Quit Relay and clear temporary messages in this tab?')) return;
    try {
      await ui.api('/api/shutdown', {});
      location.replace('about:blank');
    } catch (e) {
      ui.toast(e.message);
    }
  };
}
