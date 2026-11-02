const paths = {
  wallet: 'M3 7h17v13H3zM3 7V5l14-3v5m4 5h-6v4h6z',
  plus: 'M12 5v14M5 12h14',
  x: 'm6 6 12 12M18 6 6 18',
  up: 'M12 19V5m-6 6 6-6 6 6',
  stop: 'M7 7h10v10H7z',
  search: 'm20 20-4.5-4.5M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0',
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
  layers: 'm3 8 9-5 9 5-9 5-9-5zm0 4 9 5 9-5M3 16l9 5 9-5',
  more: 'M4 12h1m6 0h1m6 0h1',
  lock: 'M6 10h12v10H6zM8 10V7a4 4 0 0 1 8 0v3',
  pin: 'm8 3 9 3-2 2-1 4 2 3-4-1-3-1-3 1 1-3 3-4zM9 14l-4 7',
  copy: 'M8 8h12v13H8zM16 8V3H3v13h5',
  refresh: 'M20 8a8 8 0 1 0 0 8m0-13v5h-5',
  file: 'M5 3h9l5 5v13H5zm9 0v6h5',
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
    ? new Date(v).toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';

function inline(str) {
  const re = /`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*/gi;
  let out = '',
    last = 0;
  for (const match of str.matchAll(re)) {
    const v = match[0];
    out += esc(str.slice(last, match.index));
    last = match.index + v.length;
    if (v[0] === '`') out += '<code>' + esc(v.slice(1, -1)) + '</code>';
    else if (v[0] === '[') {
      const k = v.indexOf('](');
      out += `<a target="_blank" rel="noopener noreferrer" href="${esc(v.slice(k + 2, -1))}">${esc(v.slice(1, k))}</a>`;
    } else if (v.startsWith('**')) out += '<strong>' + esc(v.slice(2, -2)) + '</strong>';
    else out += '<em>' + esc(v.slice(1, -1)) + '</em>';
  }
  return out + esc(str.slice(last));
}
const cells = (s) =>
  s
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((v) => inline(v.trim()));
export function markdown(text) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n'),
    out = [];
  let p = [];
  const flush = () => {
    if (p.length) {
      out.push('<p>' + p.map(inline).join('<br>') + '</p>');
      p = [];
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m;
    if (/^\s*```/.test(line)) {
      flush();
      const lang = line.trim().slice(3),
        code = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      out.push(
        `<div class="codebox"><div class="code-head"><span>${esc(lang || 'code')}</span><button data-copycode>Copy code</button></div><pre><code>${esc(code.join('\n'))}</code></pre></div>`,
      );
    } else if (
      line.includes('|') &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])
    ) {
      flush();
      const head = cells(line);
      i++;
      let rows = '';
      while (i + 1 < lines.length && lines[i + 1].includes('|'))
        rows +=
          '<tr>' +
          cells(lines[++i])
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
    } else if ((m = line.match(/^(#{1,6})\s+(.+)/))) {
      flush();
      const level = Math.min(4, m[1].length + 1);
      out.push(`<h${level}>${inline(m[2])}</h${level}>`);
    } else if ((m = line.match(/^\s*([-*+]|\d+[.)])\s+(.+)/))) {
      flush();
      const ordered = /^\d/.test(m[1]),
        tag = ordered ? 'ol' : 'ul',
        re = ordered ? /^\s*\d+[.)]\s+(.+)/ : /^\s*[-*+]\s+(.+)/;
      let body = '<li>' + inline(m[2]) + '</li>';
      while (i + 1 < lines.length && re.test(lines[i + 1]))
        body += '<li>' + inline(lines[++i].match(re)[1]) + '</li>';
      out.push(`<${tag}>${body}</${tag}>`);
    } else if (/^>\s?/.test(line)) {
      flush();
      out.push('<blockquote>' + inline(line.replace(/^>\s?/, '')) + '</blockquote>');
    } else if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
      flush();
      out.push('<hr>');
    } else if (!line.trim()) flush();
    else p.push(line);
  }
  flush();
  return out.join('');
}
