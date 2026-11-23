import { t } from './i18n.js';

export function createDrafts(api, toast) {
  const entries = new Map();
  const queued = new Map();
  const writing = new Map();
  let revision = 0;

  function get(id) {
    return entries.get(id) ?? { value: '', files: [] };
  }

  function flush(id) {
    const update = queued.get(id);
    if (!update) return;
    queued.delete(id);
    writing.set(id, update);
    api('/api/draft', update)
      .catch(error => toast(t('Could not save the draft on this device. {message}', '无法在本机保存草稿。{message}', { message: error.message })))
      .finally(() => {
        if (writing.get(id) === update) {
          writing.delete(id);
          flush(id);
        }
      });
  }

  function set(id, draft) {
    const previous = get(id);
    const changed = draft.value !== previous.value;
    entries.set(id, { ...draft, edited: previous.edited || changed });
    if (!changed || id === 'temporary' || id.startsWith('pending-')) return;
    revision = Math.max(Date.now() * 1000, revision + 1);
    queued.set(id, { id, text: draft.value, revision });
    if (!writing.has(id)) flush(id);
  }

  function flushPending() {
    for (const id of queued.keys()) flush(id);
  }
  window.addEventListener('pagehide', flushPending);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) flushPending();
  });

  return {
    get, set,
    delete(id) { set(id, { value: '', files: [] }); },
    async restore() {
      const saved = await api('/api/drafts');
      for (const [id, value] of Object.entries(saved)) {
        if (!get(id).edited) entries.set(id, { value, files: get(id).files });
      }
    },
  };
}
