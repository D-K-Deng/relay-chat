import { esc, icon } from './format.js';

const billing = 'https://platform.openai.com/settings/organization/billing/overview';
const pricing = 'https://developers.openai.com/api/docs/pricing';
const money = (value, digits = 4) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: digits,
}).format(value);

function summary(meter) {
  if (!meter) {
    return '<div class="balance-summary"><small>LOCAL ESTIMATE</small><strong>Set a starting balance</strong><p>Enter the current USD balance from your organization’s Billing page.</p></div>';
  }
  const since = new Date(meter.since).toLocaleString('en-US', {dateStyle: 'medium', timeStyle: 'short'});
  const gap = meter.gaps ? `<div class="callout">${meter.gaps} request(s) had missing usage or unsupported pricing. The displayed estimate excludes them. Reconcile with Billing.</div>` : '';
  return `<div class="balance-summary"><small>ESTIMATED REMAINING</small><strong>${money(meter.remaining)}</strong><p>From a starting balance of ${money(meter.opening)} on ${esc(since)}.</p></div>
    <div class="balance-stats"><div><small>Estimated Relay spend</small><b>${money(meter.spent, 8)}</b></div><div><small>Priced requests</small><b>${meter.priced.toLocaleString('en-US')}</b></div></div>${gap}`;
}

export function showBalanceState(state) {
  const button = document.querySelector('#balance-pill');
  const meter = state.meter;
  button.querySelector('b').textContent = meter ? 'Est. ' + money(meter.remaining) + (meter.gaps ? '*' : '') : 'API balance';
  button.title = meter ? 'Estimated remaining API credits. Open to reconcile with Billing.' : 'Track API credits or open official Billing';
  const live = document.querySelector('#balance-live');
  if (live) live.innerHTML = summary(meter);
}

export async function openBalance(ui) {
  const body = ui.modal('API CREDITS', 'API balance', '<div id="balance-loading" class="muted">Reading your local estimate…</div>');
  const loading = body.firstElementChild;
  const state = await ui.refreshBalance();
  if (!loading.isConnected) return;
  const values = state.rates || ['', '', ''];
  body.innerHTML = `<div id="balance-live" aria-live="polite">${summary(state.meter)}</div>
    <p class="balance-explainer">Relay updates this estimate after each reply that reports usage. Open Billing for grants, expiry, top-ups, and spending outside this app.</p>
    <a class="secondary billing-link" href="${billing}" target="_blank" rel="noopener noreferrer">Open OpenAI Billing ${icon('external')}</a>
    <div class="divider"></div>
    <form id="credit-baseline"><label class="form-row">Current balance shown in Billing (USD)
      <input class="field" name="amount" type="number" min="0" step="any" placeholder="0.00" required></label>
      <p class="form-help">Saving starts a new estimate from this amount. Each retry can use additional credits.</p>
      <div class="modal-actions"><button class="primary" type="submit">Save starting balance</button>
      ${state.meter ? '<button class="secondary" type="button" id="disable-meter">Turn off estimate</button>' : ''}</div></form>
    <details class="price-details"><summary>Model pricing · ${esc(state.model)}</summary>
      <p class="form-help">USD per million tokens for Standard processing. ${state.custom_rates ? 'Using your custom rates.' : state.rates ? 'Using reference rates from November 2, 2026.' : 'Add rates to include this model.'} <a href="${pricing}" target="_blank" rel="noopener noreferrer">OpenAI pricing ↗</a></p>
      <form id="rate-form"><div class="rate-fields">${['Input', 'Cached input', 'Output'].map((label,i)=>`<label class="form-row">${label}<input class="field" name="r${i}" type="number" step="any" min="0" required value="${esc(values[i])}"></label>`).join('')}</div>
      <div class="modal-actions"><button class="secondary" type="submit">Save model rates</button>${state.custom_rates ? '<button type="button" class="hint-btn" id="default-rates">Restore default rates</button>' : ''}</div></form>
      <p class="form-help">New rates apply to future requests. Custom rates are flat. Discounts, regional pricing, and nonstandard processing can differ from the estimate.</p>
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
