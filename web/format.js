import { t, language } from './i18n.js';

const paths = {
  wallet: 'M3 7h17v13H3zM3 7V5l14-3v5m4 5h-6v4h6z',
  plus: 'M12 5v14M5 12h14',
  x: 'm6 6 12 12M18 6 6 18',
  up: 'M12 19V5m-6 6 6-6 6 6',
  stop: 'M7 7h10v10H7z',
  search: 'm20 20-4.5-4.5M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18Z',
  panel: 'M3 5h18v14H3zM9 5v14',
  down: 'm6 9 6 6 6-6',
  chevron: 'm9 6 6 6-6 6',
  arrow: 'M5 12h14m-5-5 5 5-5 5',
  chat: 'M20 14a3 3 0 0 1-3 3H9l-5 4v-5a3 3 0 0 1-1-2V7a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3z',
  temp: 'M9 4H6a3 3 0 0 0-3 3v2m0 4v2a3 3 0 0 0 2 2.8V21l4-3h2m4 0h2a3 3 0 0 0 3-3v-2m0-4V7a3 3 0 0 0-3-3h-3',
  branch:
    'M6 7v11m0-8c0 5 11 2 11-3M8.5 5.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0m11 0a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0m-11 14a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0',
  book: 'M12 6C9 4 6 4 3 5v13c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1zm0 0v13',
  sliders: 'M3 6h5m5 0h8M3 12h11m5 0h2M3 18h2m5 0h11M8 4v4m6 2v4m-7 2v4',
  gauge: 'M3.5 17.5a9 9 0 1 1 17 0H3.5zM12 14l3.5-5',
  sparkles: 'm10 4 1.9 5.6L17 12l-5.1 2.4L10 20l-1.9-5.6L3 12l5.1-2.4L10 4zm8-2 1 3 3 1-3 1-1 3-1-3-3-1 3-1z',
  bolt: 'M13.5 2.5 4.5 14h7l-1 7.5 9-12h-7l1-7z',
  speedBolt: 'M16 2.5 8 13h6l-1 8.5L22 10h-7l1-7.5zM2 8h5M1 12h3M3 16h4',
  leaf: 'M20.5 3.5c-6 .2-12-.3-15 4.6-2 3.3-1 7.7 2.2 9.4 3.1 1.6 7 .6 9.2-2.1 2.8-3.5 2.2-8.4 3.6-11.9zM3 21l11-11',
  layers: 'm3 8 9-5 9 5-9 5-9-5zm0 4 9 5 9-5M3 16l9 5 9-5',
  more: 'M4 12h1m6 0h1m6 0h1',
  lock: 'M6 10h12v10H6zM8 10V7a4 4 0 0 1 8 0v3',
  pin: 'm8 3 9 3-2 2-1 4 2 3-4-1-3-1-3 1 1-3 3-4zM9 14l-4 7',
  copy: 'M8 8h12v13H8zM16 8V3H3v13h5',
  refresh: 'M20 8a8 8 0 1 0 0 8m0-13v5h-5',
  file: 'M5 3h9l5 5v13H5zm9 0v6h5',
  folder: 'M3 6h6l2 2h10v11H3z',
  check: 'm4 12 5 5L20 6',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  edit: 'm4 16 12-12 4 4L8 20l-5 1z',
  trash: 'M3 6h18M6 6l1 15h10l1-15M9 6V3h6v3',
  key: 'M14 4a5 5 0 1 1-4 8l-6 6H2v-3l7-7a5 5 0 0 1 5-4',
  external: 'M13 4h7v7m0-7L10 14M9 6H4v14h14v-5',
  back: 'M19 12H5m5-5-5 5 5 5',
};
export const esc = (s = '') =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
export const icon = (name) =>
  `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || ''}"/></svg>`;
export const paint = (root = document) =>
  root.querySelectorAll('i[data-i]').forEach((i) => (i.outerHTML = icon(i.dataset.i)));
export const clockText = (v) =>
  v
    ? new Date(v).toLocaleString(language(), {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';

function renderInline(str, files) {
  const re = /`[^`\n]+`|\[[^\]\n]+\]\((?:https?:\/\/[^\s)]+|sandbox:\/mnt\/data\/[^)\n]+)\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*/gi;
  let out = '',
    last = 0;
  for (const match of str.matchAll(re)) {
    const v = match[0];
    out += esc(str.slice(last, match.index));
    last = match.index + v.length;
    if (v[0] === '`') out += '<code>' + esc(v.slice(1, -1)) + '</code>';
    else if (v[0] === '[') {
      const k = v.indexOf('](');
      const url = v.slice(k + 2, -1), label = esc(v.slice(1, k));
      if (url.startsWith('sandbox:/mnt/data/')) {
        let name = url.split('/').pop();
        try { name = decodeURIComponent(name); } catch { /* Keep literal filename. */ }
        const file = files.find(item => item.source === 'Data analysis' && !item.error && (item.cited_name || item.name) === name);
        out += file ? `<button type="button" class="artifact-link" data-artifact="${esc(file.run + '/' + file.name)}">${label}</button>` : label;
      } else if (/^https?:\/\//i.test(url)) {
        out += `<a target="_blank" rel="noopener noreferrer" href="${esc(url)}">${label}</a>`;
      } else out += label;
    } else if (v.startsWith('**')) out += '<strong>' + esc(v.slice(2, -2)) + '</strong>';
    else out += '<em>' + esc(v.slice(1, -1)) + '</em>';
  }
  return out + esc(str.slice(last));
}
const cells = (s, inline) =>
  s
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((v) => inline(v.trim()));

export function webCitations(text, sources = []) {
  const references = new Map(),
    byLine = new Map();
  const starts = [0];
  if (sources.length) {
    const characters = Array.from(text);
    characters.forEach((character, index) => {
      if (character === '\n' || (character === '\r' && characters[index + 1] !== '\n')) starts.push(index + 1);
    });
  }
  for (const { url, title, start_index, end_index } of sources) {
    if (!/^https?:\/\//i.test(url)) continue;
    let address;
    try {
      address = new URL(url);
    } catch {
      continue;
    }
    if (!['http:', 'https:'].includes(address.protocol)) continue;
    const href = address.href;
    const source = references.get(href) ?? { href, title: title || address.hostname, number: references.size + 1 };
    references.set(href, source);
    if (!Number.isInteger(end_index) || end_index <= 0) continue;
    const finalIndex = Math.max(start_index ?? 0, end_index - 1);
    const row = starts.findIndex((offset) => offset > finalIndex);
    const line = row < 0 ? starts.length - 1 : row - 1;
    if (!byLine.has(line)) byLine.set(line, new Set());
    byLine.get(line).add(source);
  }
  return { byLine, references: [...references.values()] };
}

function citationLinks(markers, from, to) {
  if (!markers?.size) return '';
  const sources = new Set();
  for (let row = from; row <= to; row++) {
    for (const source of markers.get(row) ?? []) sources.add(source);
  }
  return [...sources].map(({ href, title, number }) =>
    ` <a class="citation" target="_blank" rel="noopener noreferrer" href="${esc(href)}" title="${esc(title)}" aria-label="${esc(t('Source {number}, {title}', '来源 {number}，{title}', { number, title }))}">${number}</a>`,
  ).join('');
}

export function markdown(text, markers, files = []) {
  const inline = line => renderInline(line, files);
  const lines = text.replace(/\r\n?/g, '\n').split('\n'),
    out = [];
  const citations = (from, to = from) => citationLinks(markers, from, to);
  const below = (from, to) => {
    const links = citations(from, to);
    if (links) out.push('<p class="citation-row">' + links + '</p>');
  };
  let p = [];
  const flush = () => {
    if (p.length) {
      out.push('<p>' + p.join('<br>') + '</p>');
      p = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const first = i,
      line = lines[i];
    let m;
    if (/^\s*```/.test(line)) {
      flush();
      const lang = line.trim().slice(3),
        code = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      out.push(
        `<div class="codebox"><div class="code-head"><span>${esc(lang || t('code', '代码'))}</span><button data-copycode>${t('Copy code', '复制代码')}</button></div><pre><code>${esc(code.join('\n'))}</code></pre></div>`,
      );
      below(first, i);
    } else if (
      line.includes('|') &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])
    ) {
      flush();
      const head = cells(line, inline);
      i++;
      let rows = '';
      while (i + 1 < lines.length && lines[i + 1].includes('|'))
        rows +=
          '<tr>' +
          cells(lines[++i], inline)
            .map((c) => '<td>' + c + '</td>')
            .join('') +
          '</tr>';
      out.push(
        '<div class="table-scroll"><table><thead><tr>' +
          head.map((c) => '<th>' + c + '</th>').join('') +
          '</tr></thead><tbody>' +
          rows +
          '</tbody></table></div>',
      );
      below(first, i);
    } else if ((m = line.match(/^(#{1,6})\s+(.+)/))) {
      flush();
      const level = Math.min(4, m[1].length + 1);
      out.push(`<h${level}>${inline(m[2])}${citations(i)}</h${level}>`);
    } else if ((m = line.match(/^\s*([-*+]|\d+[.)])\s+(.+)/))) {
      flush();
      const ordered = /^\d/.test(m[1]),
        tag = ordered ? 'ol' : 'ul',
        re = ordered ? /^\s*\d+[.)]\s+(.+)/ : /^\s*[-*+]\s+(.+)/;
      let body = '<li>' + inline(m[2]) + citations(i) + '</li>';
      while (i + 1 < lines.length && re.test(lines[i + 1]))
        body += '<li>' + inline(lines[++i].match(re)[1]) + citations(i) + '</li>';
      out.push(`<${tag}>${body}</${tag}>`);
    } else if (/^>\s?/.test(line)) {
      flush();
      out.push('<blockquote>' + inline(line.replace(/^>\s?/, '')) + citations(i) + '</blockquote>');
    } else if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
      flush();
      out.push('<hr>');
      below(i, i);
    } else if (!line.trim()) {
      flush();
      below(i, i);
    } else p.push(inline(line) + citations(i));
  }
  flush();
  return out.join('');
}
