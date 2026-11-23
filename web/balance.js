import { t, language } from './i18n.js';
import { esc, icon } from './format.js';

const billing = 'https://platform.openai.com/settings/organization/billing/overview';
const pricing = 'https://developers.openai.com/api/docs/pricing';
const tierLabels = () => ({ default: t("Standard", "标准"), standard: t("Standard", "标准"), fast: t("Fast", "快速"), priority: t("Fast", "快速"), ultrafast: t("Ultrafast", "极速"), flex: t("Flex", "弹性") });
const rateLabels = () => [t("Input", "输入"), t("Cached input", "缓存输入"), t("Output", "输出")];
const money = (value, digits = 4) => new Intl.NumberFormat(language(), {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: digits,
}).format(value);

function summary(meter) {
  if (!meter) {
    return `<div class="balance-summary"><small>${t("LOCAL ESTIMATE", "本地预估")}</small><strong>${t("Set a starting balance", "设置起始余额")}</strong><p>${t("Enter the current USD balance from your organization’s Billing page.", "输入组织账单页面上显示的当前美元余额。")}</p></div>`;
  }
  const since = new Date(meter.since).toLocaleString(language(), {dateStyle: 'medium', timeStyle: 'short'});
  const gap = meter.gaps ? `<div class="callout">${t('{n} request(s) had missing usage or unsupported pricing. Reconcile with Billing.', '有 {n} 次请求缺少用量或价格，未计入预估。请在账单中核对。', { n: meter.gaps })}</div>` : '';
  const estimates = meter.estimated ? `<p class="form-help">${t(
    'Includes {cost} estimated locally for {n} interrupted requests without complete usage reports.',
    '其中 {cost} 为本地估算，来自 {n} 次未收到完整用量的中断请求。',
    { cost: money(meter.estimated_spend, 8), n: meter.estimated })}</p>` : '';
  const failures = meter.failed ? `<div><small>${t('Failed requests', '失败请求')}</small><b>${meter.failed.toLocaleString(language())}</b></div>` : '';
  return `<div class="balance-summary"><small>${t("ESTIMATED REMAINING", "预估余额")}</small><strong>${money(meter.remaining)}</strong><p>${t('Starting balance on {date}: {amount}.', '{date}的起始余额为 {amount}。', { date: esc(since), amount: money(meter.opening) })}</p></div>
    <div class="balance-stats"><div><small>${t("Estimated Relay spend", "Relay 预估支出")}</small><b>${money(meter.spent, 8)}</b></div><div><small>${t("Priced requests", "已计价请求")}</small><b>${meter.priced.toLocaleString(language())}</b></div>${failures}</div>${estimates}${gap}`;
}

export function showBalanceState(state) {
  const button = document.querySelector('#balance-pill');
  const meter = state.meter;
  button.querySelector('b').textContent = meter ? t("Est. ", "预估 ") + money(meter.remaining) + (meter.gaps ? '*' : '') : t("API balance", "API 余额");
  button.title = meter ? t("Estimated remaining API credits. Open to reconcile with Billing.", "预估剩余 API 额度。点击后可与账单核对。") : t("Track API credits or open official Billing", "跟踪 API 额度或打开官方账单");
  const live = document.querySelector('#balance-live');
  if (live) live.innerHTML = summary(meter);
}

export async function openBalance(ui) {
  const body = ui.modal(t("API CREDITS", "API 额度"), t("API balance", "API 余额"), `<div id="balance-loading" class="muted">${t("Reading your local estimate…", "正在读取本地预估…")}</div>`);
  const loading = body.firstElementChild;
  const state = await ui.refreshBalance();
  if (!loading.isConnected) return;
  const values = state.rates || ['', '', ''];
  const tier = tierLabels()[state.tier] || t("Standard", "标准");
  const currentRates = state.tier_rates
    ? `<p class="form-help">${rateLabels().map((label, i) => {
        const rate = state.tier_rates[i];
        return `${label} ${rate == null ? '—' : money(rate)}`;
      }).join(' · ')}</p>`
    : `<p class="form-help">${t("Rates for this model and speed are unavailable.", "此模型和速度暂无可用费率。")}</p>`;
  body.innerHTML = `<div id="balance-live" aria-live="polite">${summary(state.meter)}</div>
    <p class="balance-explainer">${t("Relay estimates model tokens, web searches and dictation with reported duration. Open Billing to reconcile the full amount.", "Relay 根据模型 token、网页搜索和已报告的听写时长估算费用。完整金额请以账单为准。")}</p>
    <a class="secondary billing-link" href="${billing}" target="_blank" rel="noopener noreferrer">${t('Open OpenAI Billing', '打开 OpenAI 账单')} ${icon('external')}</a>
    <div class="divider"></div>
    <form id="credit-baseline"><label class="form-row">${t('Current balance shown in Billing (USD)', '账单页面的当前余额（美元）')}
      <input class="field" name="amount" type="number" min="0" step="any" placeholder="0.00" required></label>
      <p class="form-help">${t("Saving starts a new estimate from this amount. Each retry can use additional credits.", "保存后将从此余额开始重新估算。重新生成回复可能产生额外费用。")}</p>
      <div class="modal-actions"><button class="primary" type="submit">${t("Save starting balance", "保存起始余额")}</button>
      ${state.meter ? `<button class="secondary" type="button" id="disable-meter">${t("Turn off estimate", "关闭预估")}</button>` : ''}</div></form>
    <details class="price-details" open><summary>${t('Model pricing', '模型价格')} · ${esc(state.model)} · ${tier}</summary>
      ${currentRates}
      <p class="form-help">${t('USD per million tokens for short contexts. Prices checked {date}.', '短上下文价格，单位为美元 / 百万 token。价格更新于 {date}。', { date: esc(state.price_date) })} <a href="${pricing}" target="_blank" rel="noopener noreferrer">${t("OpenAI pricing ↗", "OpenAI 定价 ↗")}</a></p>
      <details><summary>${state.custom_rates ? t("Edit custom Standard rates", "编辑自定义标准费率") : t("Override Standard rates", "设置自定义标准费率")}</summary>
        <form id="rate-form"><div class="rate-fields">${rateLabels().map((label,i)=>`<label class="form-row">${label}<input class="field" name="r${i}" type="number" step="any" min="0" required value="${esc(values[i] ?? '')}"></label>`).join('')}</div>
        <div class="modal-actions"><button class="secondary" type="submit">${t("Save model rates", "保存模型费率")}</button>${state.custom_rates ? `<button type="button" class="hint-btn" id="default-rates">${t("Restore published rates", "恢复官方费率")}</button>` : ''}</div></form>
        <p class="form-help">${t("Custom rates are flat and apply to future Standard requests.", "自定义费率按固定单价计费，适用于之后的标准速度请求。")}</p>
      </details>
    </details>`;
  const update = async (path, payload) => {
    try {
      await ui.api(path, payload);
      await ui.balanceChanged();
      await openBalance(ui);
    } catch (error) { ui.toast(error.message); }
  };
  body.querySelector('#credit-baseline').onsubmit = (event) => {
    event.preventDefault();
    void update('/api/balance', {amount: Number(new FormData(event.target).get('amount'))});
  };
  const disable = body.querySelector('#disable-meter');
  if (disable) disable.onclick = () => void update('/api/balance', {amount: null});
  body.querySelector('#rate-form').onsubmit = (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    void update('/api/balance/rates', {model: state.model, rates: [0,1,2].map(i => Number(data.get('r' + i)))});
  };
  const reset = body.querySelector('#default-rates');
  if (reset) reset.onclick = () => void update('/api/balance/rates', {model: state.model, rates: null});
}
