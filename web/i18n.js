let currentLanguage = 'en';

export const language = () => currentLanguage;

export function t(english, chinese, values) {
  const template = currentLanguage === 'zh-CN' ? chinese : english;
  return values ? template.replace(/\{(\w+)\}/g, (_, key) => values[key]) : template;
}

// Static controls opt in with a Chinese label. All user content is rendered separately.
const staticLabels = [
  ['data-zh', null],
  ['data-zh-title', 'title'],
  ['data-zh-placeholder', 'placeholder'],
  ['data-zh-aria-label', 'aria-label'],
];
const originalLabels = new WeakMap();
export function setLanguage(selected) {
  const next = selected === 'zh-CN' ? selected : 'en';
  if (currentLanguage === next) return;
  currentLanguage = next;
  document.documentElement.lang = next;
  for (const [marker, attr] of staticLabels) {
    for (const el of document.querySelectorAll('[' + marker + ']')) {
      if (!originalLabels.has(el)) originalLabels.set(el, {});
      const original = originalLabels.get(el);
      original[marker] ??= attr ? el.getAttribute(attr) : el.textContent;
      const value = t(original[marker], el.getAttribute(marker));
      if (attr) el.setAttribute(attr, value);
      else el.textContent = value;
    }
  }
  window.webkit?.messageHandlers?.relayLanguage?.postMessage(next);
  window.dispatchEvent(new Event('relay-languagechange'));
}
