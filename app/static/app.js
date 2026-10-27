'use strict';
/* Idea Atlas UI — vanilla JS, talks to the loopback API. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const S = {
  projects: [], pid: null, view: 'capture', kinds: [], edgeTypes: [], xp: 0, dataDir: '',
  nodes: null, selected: new Set(), lastCapture: null, searchQ: '', searchKind: '', searchMode: 'matches', captureTarget: null, showAllWords: false, editLinks: false,
  map: { focus: null, focusLabel: '', depth: 1, kinds: new Set(), sources: false, suggested: true, zoom: 1 },
  cards: { kind: '', status: '', confidence: '', sort: 'updated', q: '' },
  drawerId: null,
};
const RANKS = ['Wanderer', 'Note-taker', 'Cartographer', 'Pathfinder', 'Navigator', 'Explorer', 'Atlas Keeper', 'Polymath'];
const KIND_ICON = { concept: '◆', method: '⚙', idea: '✦', solution: '✔', question: '?', evidence: '▤', decision: '⚑', assumption: '≈', risk: '⚠', explanation: 'ℹ', reference: '❞', task: '☐', claim: '❝' };
const QUEST_ICON = { answer: '❓', verify: '✅', contradiction: '⚔️', 'definition-conflict': '📖', define: '✏️', evidence: '🔎', mitigate: '🛡️', assumption: '🧪', task: '☑️', suggested: '🔗', comention: '🧩', duplicate: '👯', isolated: '🏝️', cluster: '🌉' };

// ------------------------------------------------------------------ plumbing
async function api(method, path, body) {
  let r;
  try {
    r = await fetch(path, { method, headers: { 'X-Atlas': '1', 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (_) {
    // the window is open but the app behind it has stopped
    showOffline();
    throw new Error('Idea Atlas has stopped. Double-click IdeaAtlas.exe to open it again.');
  }
  hideOffline();
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

async function download(path) {
  const r = await fetch(path, { headers: { 'X-Atlas': '1' } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  const name = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'atlas-export';
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast(`Saved ${name} to your Downloads folder`);
}

// ------------------------------------------------------------------ connection
let offlineTimer = null;
function showOffline() {
  let b = $('#offline');
  if (!b) {
    b = Object.assign(document.createElement('div'), { id: 'offline', className: 'offline' });
    document.body.appendChild(b);
  }
  b.innerHTML = `<b>Idea Atlas has stopped.</b><span>Close this window and double-click <code>IdeaAtlas.exe</code> to open it again. Your notes are saved.</span>`;
  if (!offlineTimer) offlineTimer = setInterval(async () => {   // light check every 3 s until it is back
    try {
      const r = await fetch('/api/state', { headers: { 'X-Atlas': '1' } });
      if (r.ok) { hideOffline(); location.reload(); }
    } catch (_) { /* still down */ }
  }, 3000);
}
function hideOffline() {
  if (offlineTimer) { clearInterval(offlineTimer); offlineTimer = null; }
  $('#offline')?.remove();
}

let toastTimer;
function toast(msg, err = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.className = 'toast', err ? 5000 : 2600);
}
const fail = e => { console.error(e); toast(e.message || String(e), true); };

function modal(html, { onOpen } = {}) {
  return new Promise(resolve => {
    const m = $('#modal'), box = $('#modalBox');
    box.innerHTML = html; m.hidden = false;
    const close = v => { m.hidden = true; box.innerHTML = ''; document.removeEventListener('keydown', key); resolve(v); };
    const key = e => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', key);
    box.onsubmit = e => { e.preventDefault(); close(Object.fromEntries(new FormData(e.target))); };
    box.onclick = e => { if (e.target.dataset.close !== undefined) close(null); };
    m.onclick = e => { if (e.target === m) close(null); };
    onOpen && onOpen(box);
    (box.querySelector('[autofocus]') || box.querySelector('input,textarea,select,button'))?.focus();
  });
}
const confirmBox = async (text, ok = 'OK', danger = false) =>
  !!(await modal(`<form><p>${text}</p><div class="row" style="justify-content:flex-end"><button type="button" data-close>Cancel</button><button class="${danger ? 'danger' : 'primary'}" autofocus>${ok}</button></div></form>`));

const proj = () => S.projects.find(p => p.id === S.pid);
const isLocation = s => /^(https?:\/\/|file:|[A-Za-z]:[\\/]|\\\\)/i.test((s || '').trim());
// Plain-language names for item types and relationships (internal codes like C-001 are never shown).
const KIND_LABEL = { concept: 'Concept', method: 'Method', idea: 'Idea', solution: 'Solution', question: 'Question', evidence: 'Evidence', decision: 'Decision', assumption: 'Assumption', risk: 'Risk', explanation: 'Explanation', reference: 'Source', task: 'Task', claim: 'Statement' };
const kindName = k => KIND_LABEL[k] || k;
const kindPill = k => `<span class="kind ${esc(k)}">${KIND_ICON[k] || ''} ${esc(kindName(k))}</span>`;
const short = (s, n = 90) => (s || '').length > n ? s.slice(0, n - 1).trimEnd() + '…' : (s || '');
const nodeLink = (id, title, kind) => `<span class="nodelink" ${id ? `data-node="${id}"` : ''}>${kind ? `<i class="dot ${esc(kind)}" title="${esc(kindName(kind))}"></i>` : ''}<span class="t">${esc(short(title))}</span></span>`;
const edgeBadges = e => [
  e.status === 'suggested' ? '<span class="badge sugg">suggested</span>' : '',
  e.status === 'resolved' ? '<span class="badge">resolved</span>' : '',
  e.confidence === 'low' && e.status !== 'suggested' ? '<span class="badge hyp" title="Based on hedged wording or a weak signal">uncertain</span>' : '',
  e.type === 'contradicts' && e.status === 'active' ? '<span class="badge bad">conflict</span>' : '',
].join(' ');
// how a relationship reads from the item's own point of view
const REL_OUT = { 'depends on': 'Depends on', explains: 'Explains', supports: 'Supports', contradicts: 'Contradicts', solves: 'Solves', 'is an example of': 'Is an example of', 'raises question': 'Raises questions', 'derived from': 'Based on', 'relates to': 'Related to' };
const REL_IN = { 'depends on': 'Needed by', explains: 'Explained by', supports: 'Supported by', contradicts: 'Contradicted by', solves: 'Solved by', 'is an example of': 'Examples', 'raises question': 'Raised by', 'derived from': 'Used by', 'relates to': 'Related to' };
const REL_ORDER = ['Contradicted by', 'Contradicts', 'Solved by', 'Solves', 'Supported by', 'Supports', 'Explained by', 'Explains', 'Depends on', 'Needed by', 'Raises questions', 'Raised by', 'Is an example of', 'Examples', 'Based on', 'Used by', 'Related to'];

// ------------------------------------------------------------------ mermaid
mermaid.initialize({
  startOnLoad: false, securityLevel: 'loose', theme: 'base', maxTextSize: 500000,
  flowchart: { htmlLabels: true, curve: 'basis', nodeSpacing: 40, rankSpacing: 60, padding: 12 },
  themeVariables: {
    darkMode: true, background: 'transparent', fontFamily: '"Segoe UI Variable Text", -apple-system, "Segoe UI", system-ui, sans-serif', fontSize: '13px',
    primaryColor: '#1e1e2c', primaryTextColor: '#f2f2f7', primaryBorderColor: '#3a3a4c', lineColor: '#7c7c92',
    secondaryColor: '#1e1e2c', tertiaryColor: '#1e1e2c', edgeLabelBackground: '#16161f', textColor: '#d8d8e2',
  },
});
let mmSeq = 0;
async function renderMermaid(el, src, zoom = 1) {
  try {
    const { svg, bindFunctions } = await mermaid.render('mm' + (++mmSeq), src);
    el.innerHTML = svg;
    bindFunctions && bindFunctions(el);
    applyZoom(el, zoom);
  } catch (e) {
    el.innerHTML = `<div class="empty">The map could not be drawn (${esc(e.message || e)}).</div>`;
    $$('body > [id^="dmm"]').forEach(x => x.remove());
  }
}
function applyZoom(el, zoom) {
  const svg = el.querySelector('svg');
  if (!svg) return;
  const vb = svg.viewBox.baseVal;
  if (vb && vb.width) { svg.style.width = vb.width * zoom + 'px'; svg.style.height = vb.height * zoom + 'px'; svg.style.maxWidth = 'none'; }
}
window.atlasPick = id => {
  if (S.view === 'capture' && S.lastCapture?.dry_run) return toast('Preview only: add the note to open cards');
  openCard(id);
};
function enablePan(box) {
  let down = null;
  box.onpointerdown = e => { if (e.target.closest('.node')) return; down = { x: e.clientX, y: e.clientY, l: box.scrollLeft, t: box.scrollTop }; box.style.cursor = 'grabbing'; };
  box.onpointermove = e => { if (!down) return; box.scrollLeft = down.l - (e.clientX - down.x); box.scrollTop = down.t - (e.clientY - down.y); };
  box.onpointerup = box.onpointerleave = () => { down = null; box.style.cursor = ''; };
}

// ------------------------------------------------------------------ state & chrome
async function loadState() {
  const st = await api('GET', '/api/state');
  Object.assign(S, { projects: st.projects, kinds: st.kinds, edgeTypes: st.edge_types, xp: st.xp, dataDir: st.data_dir, version: st.version });
  let saved = null;
  try { saved = +localStorage.getItem('atlas.pid'); } catch (_) { /* storage blocked */ }
  if (!S.projects.some(p => p.id === S.pid)) S.pid = S.projects.some(p => p.id === saved) ? saved : (S.projects.find(p => !p.archived)?.id ?? null);
  renderChrome();
}
function setProject(pid) {
  S.pid = pid; S.nodes = null; S.selected.clear(); S.map.focus = null; S.map.focusLabel = '';
  try { localStorage.setItem('atlas.pid', pid); } catch (_) { /* ignore */ }
  renderChrome(); render();
}
function renderChrome() {
  const sel = $('#projectSelect');
  sel.innerHTML = S.projects.length
    ? S.projects.filter(p => !p.archived || p.id === S.pid).map(p => `<option value="${p.id}" ${p.id === S.pid ? 'selected' : ''}>@${esc(p.handle)} · ${esc(p.name)}</option>`).join('')
    : '<option value="">No projects yet</option>';
  const lvl = 1 + Math.floor(S.xp / 100);
  $('#xpBadge').innerHTML = `<div class="lvl"><span>Level ${lvl} · ${RANKS[Math.min(RANKS.length - 1, lvl - 1)]}</span><span class="mute">${S.xp} XP</span></div><div class="xpbar"><i style="width:${S.xp % 100}%"></i></div>`;
  $('#hotkeyHint').innerHTML = S.version ? `<span class="ver">Version ${esc(S.version)} · closes completely when you close this window</span>` : '';
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === S.view));
  $('#tabs [data-view="trace"]').hidden = !(proj()?.requirement_count);     // only projects that have requirements
  document.title = proj() ? `Idea Atlas · @${proj().handle}` : 'Idea Atlas';
}
async function refreshXp() {
  try { S.xp = (await api('GET', '/api/state')).xp; renderChrome(); } catch (_) { /* ignore */ }
}
async function getNodes(force = false) {
  if (!S.pid) return [];
  if (!S.nodes || force) S.nodes = (await api('GET', `/api/projects/${S.pid}/nodes?sort=title`)).nodes;
  return S.nodes;
}

function go(view) {
  if (location.hash.slice(1) !== view) location.hash = view; else render();
}
window.addEventListener('hashchange', () => render());

async function render() {
  const v = location.hash.slice(1) || 'capture';
  S.view = ['capture', 'map', 'cards', 'quests', 'trace', 'notes', 'compare', 'export', 'projects', 'search'].includes(v) ? v : 'capture';
  renderChrome();
  const main = $('#main');
  if (!S.projects.length && !['projects', 'capture'].includes(S.view)) return renderWelcome(main);
  try {
    await ({ capture: renderCapture, map: renderMap, cards: renderCards, quests: renderQuests, trace: renderTrace, notes: renderNotes, compare: renderCompare, export: renderExport, projects: renderProjects, search: renderSearch })[S.view](main);
  } catch (e) { fail(e); }
}

function renderWelcome(main) {
  main.innerHTML = `<div class="wrap"><div class="panel empty"><h2>Welcome to your Idea Atlas</h2>
  <p>Create a project, then capture notes with <code>@handle</code>. Everything stays in <code>${esc(S.dataDir)}</code>.</p>
  <button class="primary" data-act="new-project">Create a project</button></div></div>`;
}

// ------------------------------------------------------------------ capture
const CHEAT = `
<h3 style="margin-top:0">Just paste or type</h3>
<p>Write the way you normally do: meeting notes, paper highlights, half-sentences, brainstorms. The atlas splits the text into sentences and works out which are questions, ideas, solutions, evidence, risks or tasks, and what they are about.</p>
<p><code>@thesis</code> picks the project. Without it the note goes to the selected project.</p>
<p>Type <code>[[</code> (or click <b>Link a concept</b>) to pick a concept you already have. The note then adds to that concept instead of creating a near-duplicate. Below the editor, <span class="chip ok" style="cursor:default">✓</span> means it will reuse an existing concept, and <span class="chip newc" style="cursor:default">＋ <i>new</i></span> means it will create one.</p>
<p>Use <b>Preview</b> to check the mapping before saving, and fix anything in the card afterwards.</p>
<details><summary class="mute small" style="cursor:pointer">Optional shortcuts (only to override a guess)</summary>
<ul>
<li>Line prefixes: <code>Q:</code> <code>Idea:</code> <code>Solution:</code> <code>Method:</code> <code>Evidence:</code> <code>Decision:</code> <code>Assumption:</code> <code>Risk:</code> <code>Todo:</code> <code>Ref:</code></li>
<li>Headings such as <code>Methods:</code> or <code>## Open questions</code> type the bullets under them.</li>
<li>Definitions: <code>X is a …</code>, <code>X means …</code>, <code>Term: definition</code></li>
<li>Aliases: <code>Graph Neural Networks (GNNs)</code>, <code>X, also called Y</code></li>
<li>Explicit concepts: <code>[[Concept name]]</code> (an existing name or alias reuses that concept) or "quoted terms"</li>
<li>Relations: <i>depends on, explains, supports, contradicts, solves, is an example of, derived from, based on</i></li>
<li>Tags: type <code>#</code> to pick one you already use, or keep typing to make a new one. A trailing <code>#todo</code>, <code>#idea</code> or <code>#question</code> also sets the type.</li>
</ul></details>
<p class="mute small">Hedged wording (<i>maybe, I think, might</i>) is kept as a hypothesis. Parsing happens locally; ask Claude Code to "enrich my Idea Atlas" for a deeper pass.</p>`;

function renderCapture(main) {
  let draft = '';
  try { draft = localStorage.getItem('atlas.draft') || ''; } catch (_) { /* ignore */ }
  main.innerHTML = `<div class="wrap capture-grid">
    <div>
      <div class="panel">
        <div class="row" style="margin-bottom:8px"><h2 class="grow" style="margin:0">Capture</h2><span class="targets" id="targets"></span>
          <button class="sm" data-act="insert-wiki" title="Pick a concept you already have, so this note adds to it instead of creating a new one (or type [[ )">[[ ]] Link a concept</button></div>
        ${S.captureTarget ? `<div class="target-banner"><span>${S.captureTarget.mode === 'evidence' ? 'Adding evidence for' : 'Answering'}</span>
          <b>${esc(short(S.captureTarget.title, 110))}</b><span class="grow"></span><button class="icon-btn" data-act="clear-target" title="Write a normal note instead"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>` : ''}
        <div class="editor">
          <textarea id="captureText" spellcheck="true" placeholder="@project then paste or type anything: notes, paper annotations, ideas, questions, solutions…&#10;&#10;e.g. @thesis the pipeline gets slow on big corpora. caching embeddings might fix it, but not sure it helps reranking. maybe try approximate search instead?&#10;&#10;Type [[ to link a concept you already have.">${esc(draft)}</textarea>
          <div class="ac" id="ac" hidden></div>
        </div>
        <div class="linkstate" id="linkState"></div>
        <div class="row" style="margin-top:10px">
          <input id="captureSource" type="text" class="grow" placeholder="Source (optional): file path e.g. C:\\Papers\\zhu2020.pdf, a link, or a label like “meeting with Anna”" title="Tip: in Explorer, Shift+right-click a file → Copy as path, then paste here">
          <button data-act="preview" title="See how the note would be mapped without saving">Preview</button>
          <button class="primary" data-act="capture">Add to atlas <span class="small">Ctrl+Enter</span></button>
        </div>
      </div>
      <div id="captureResult"></div>
    </div>
    <div class="panel cheat">${CHEAT}</div>
  </div>`;
  const ta = $('#captureText');
  ta.addEventListener('input', () => {
    updateTargets(); autocomplete(ta);
    clearTimeout(renderCapture.t); renderCapture.t = setTimeout(() => updateLinkState().catch(() => {}), 250);
    try { localStorage.setItem('atlas.draft', ta.value); } catch (_) { /* ignore */ }
  });
  ta.addEventListener('keydown', acKeys);
  ta.addEventListener('click', () => autocomplete(ta));
  ta.addEventListener('blur', () => setTimeout(() => { $('#ac') && ($('#ac').hidden = true); }, 150));
  $('[data-act="insert-wiki"]').onmousedown = e => e.preventDefault();   // keep the caret in the editor
  updateTargets(); updateLinkState().catch(() => {});
  if (S.lastCapture) renderCaptureResult(S.lastCapture);
  setTimeout(() => ta.focus(), 20);
}

function handlesIn(text) { return [...new Set([...text.matchAll(/(?<![\w@])@([A-Za-z][\w-]{0,40})/g)].map(m => m[1].toLowerCase()))]; }
function updateTargets() {
  const ta = $('#captureText'); if (!ta) return;
  const hs = handlesIn(ta.value);
  const known = new Set(S.projects.map(p => p.handle));
  $('#targets').innerHTML = hs.length
    ? '→ ' + hs.map(h => known.has(h) ? `<b>@${esc(h)}</b>` : `<b>@${esc(h)}</b> <span class="badge sugg">new project</span>`).join(', ')
    : (proj() ? `→ <b>@${esc(proj().handle)}</b> (selected)` : '<span class="badge bad">pick a project or type @handle</span>');
}

// @handle autocomplete with a mirror element to find the caret position
let acItems = [], acIndex = 0;
function caretXY(ta) {
  const m = document.createElement('div'), cs = getComputedStyle(ta);
  for (const p of ['font', 'lineHeight', 'padding', 'border', 'width', 'letterSpacing', 'whiteSpace', 'wordWrap', 'boxSizing']) m.style[p] = cs[p];
  Object.assign(m.style, { position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap', wordWrap: 'break-word', top: 0, left: 0 });
  m.textContent = ta.value.slice(0, ta.selectionStart);
  const span = document.createElement('span'); span.textContent = '​'; m.appendChild(span);
  ta.parentElement.appendChild(m);
  const xy = { x: span.offsetLeft, y: span.offsetTop - ta.scrollTop + parseInt(cs.lineHeight || 20, 10) + 4 };
  m.remove();
  return xy;
}
let acMode = 'handle', acSeq = 0;
function showAc(ta, html, wide) {
  const ac = $('#ac');
  const { x, y } = caretXY(ta);
  ac.classList.toggle('concepts', wide);
  Object.assign(ac.style, { left: Math.max(0, Math.min(x, ta.clientWidth - (wide ? 380 : 240))) + 'px', top: y + 'px' });
  ac.innerHTML = html;
  ac.hidden = false;
  ac.onmousedown = e => { e.preventDefault(); const d = e.target.closest('[data-i]'); if (d) acPick(+d.dataset.i); };
}
function autocomplete(ta) {
  const ac = $('#ac'), before = ta.value.slice(0, ta.selectionStart);
  const w = before.match(/\[\[([^\[\]\n]{0,200})$/);
  if (w) return conceptSuggest(ta, w[1]).catch(fail);
  const t = before.match(/(?:^|[\s(,;])#([A-Za-z][\w-]{0,40})?$/);      // "## heading" and "C#" do not trigger
  if (t) return tagSuggest(ta, t[1] || '').catch(fail);
  acSeq++;
  const m = before.match(/(?:^|[^\w@])@([\w-]*)$/);
  if (!m) { ac.hidden = true; return; }
  const frag = m[1].toLowerCase();
  acMode = 'handle';
  acItems = S.projects.filter(p => !p.archived && (p.handle.startsWith(frag) || p.name.toLowerCase().includes(frag))).slice(0, 8).map(p => ({ handle: p.handle, label: `@${p.handle} · ${p.name}` }));
  if (frag && !S.projects.some(p => p.handle === frag)) acItems.push({ handle: frag, label: `Create @${frag}`, create: true });
  if (!acItems.length) { ac.hidden = true; return; }
  acIndex = 0;
  showAc(ta, acItems.map((it, i) => `<div data-i="${i}" class="${i === 0 ? 'on' : ''}">${esc(it.label)}</div>`).join(''), false);
}
function acKeys(e) {
  const ac = $('#ac');
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); return doCapture(false); }
  if (ac.hidden) return;
  if (e.key === 'Escape') { e.preventDefault(); ac.hidden = true; return; }
  if (!acItems.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    acIndex = (acIndex + (e.key === 'ArrowDown' ? 1 : -1) + acItems.length) % acItems.length;
    $$('#ac [data-i]').forEach((d, i) => d.classList.toggle('on', i === acIndex));
    $(`#ac [data-i="${acIndex}"]`)?.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); acPick(acIndex); }
}
function acPick(i) {
  const ta = $('#captureText'), it = acItems[i]; if (!it) return;
  let before = ta.value.slice(0, ta.selectionStart), after = ta.value.slice(ta.selectionStart);
  if (acMode === 'concept') {
    before = before.replace(/\[\[[^\[\]\n]*$/, `[[${it.create || it.concept.title}]]`);
    after = after.replace(/^[^\[\]\n]*\]\]/, '');           // finish an existing [[…]] instead of nesting one
    if (!/^[\s.,;:!?)]/.test(after)) before += ' ';
  } else if (acMode === 'tag') {
    before = before.replace(/#[\w-]*$/, '#' + it.tag);
    after = after.replace(/^[\w-]+/, '');                  // replace the rest of a half-typed tag
    if (!/^[\s.,;:!?)]/.test(after)) before += ' ';
  } else {
    before = before.replace(/@([\w-]*)$/, '@' + it.handle + ' ');
  }
  ta.value = before + after;
  ta.selectionStart = ta.selectionEnd = before.length;
  $('#ac').hidden = true; ta.focus(); updateTargets(); updateLinkState().catch(() => {});
  try { localStorage.setItem('atlas.draft', ta.value); } catch (_) { /* ignore */ }
}

// [[concept]] suggestions: link a note to a concept you already have instead of creating a near-duplicate
const conceptCache = {};    // project id -> { at, list }
async function conceptsFor(pid) {
  const c = conceptCache[pid];
  if (c && Date.now() - c.at < 60000) return c.list;
  const list = (await api('GET', `/api/projects/${pid}/concepts`)).concepts;
  conceptCache[pid] = { at: Date.now(), list };
  return list;
}
function captureProject(text) {       // where the note goes: its first @handle, else the selected project
  const hs = handlesIn(text);
  return hs.length ? S.projects.find(p => p.handle === hs[0]) || null : proj() || null;
}
const fold = s => (s || '').toLowerCase().replace(/[^a-z0-9+#]+/g, ' ').trim();
const foldKey = s => fold(s).split(' ').map(w => w.length > 3 ? w.replace(/(?:es|s)$/, '') : w).join(' ');
function hl(text, frag) {
  const i = frag ? text.toLowerCase().indexOf(frag.toLowerCase()) : -1;
  return i < 0 ? esc(text) : esc(text.slice(0, i)) + '<b>' + esc(text.slice(i, i + frag.length)) + '</b>' + esc(text.slice(i + frag.length));
}
function rankConcepts(list, frag) {
  const f = fold(frag);
  if (!f) return list.slice(0, 8).map(c => ({ c, via: null }));    // already sorted by how often they appear
  const words = f.split(' ');
  const out = [];
  for (const c of list) {
    let best = 0, via = null;
    for (const name of [c.title, ...c.aliases]) {
      const n = fold(name), alias = name !== c.title;
      let s = n === f ? 100 : n.startsWith(f) ? 80 : (' ' + n).includes(' ' + f) ? 60 : n.includes(f) ? 40
        : words.every(w => (' ' + n).includes(' ' + w)) ? 30 : 0;
      if (s && alias) s -= 5;
      if (s > best) { best = s; via = alias ? name : null; }
    }
    if (best) out.push({ c, via, s: best + Math.min(c.notes, 10) });
  }
  return out.sort((a, b) => b.s - a.s).slice(0, 8);
}
async function conceptSuggest(ta, frag) {
  const seq = ++acSeq, p = captureProject(ta.value);
  const list = p ? await conceptsFor(p.id) : [];
  if (seq !== acSeq || $('#ac') === null) return;
  const f = frag.trim(), hits = rankConcepts(list, f);
  const exact = list.some(c => [c.title, ...c.aliases].some(a => foldKey(a) === foldKey(f)));
  acMode = 'concept';
  acItems = hits.map(h => ({ concept: h.c, via: h.via }));
  if (f && !exact) acItems.push({ create: f });
  acIndex = 0;
  const hs = handlesIn(ta.value);
  const head = !p ? (hs.length ? `@${hs[0]} is a new project, so there are no concepts to pick yet` : 'Pick a project first: type @handle at the start of the note')
    : !list.length ? `No concepts in @${p.handle} yet. Type a name to create one`
    : f ? `Concepts in @${p.handle}` : `Most used in @${p.handle} · type to filter`;
  const row = (it, i) => it.create
    ? `<div data-i="${i}" class="${i === 0 ? 'on' : ''}"><span class="ac-l"><span class="plus">＋</span>New concept “${esc(short(it.create, 60))}”</span><span class="ac-sub">Nothing matches, so this creates a new one</span></div>`
    : `<div data-i="${i}" class="${i === 0 ? 'on' : ''}"><span class="ac-l"><i class="dot ${esc(it.concept.kind)}"></i><span class="ac-t">${hl(it.concept.title, f)}</span>
        ${it.via ? `<span class="ac-m">also “${hl(it.via, f)}”</span>` : ''}<span class="grow"></span><span class="ac-m">${it.concept.notes} note${it.concept.notes === 1 ? '' : 's'}</span></span>
        ${it.concept.definition ? `<span class="ac-sub">${esc(short(it.concept.definition, 100))}</span>` : ''}</div>`;
  showAc(ta, `<div class="ac-head">${esc(head)}</div>${acItems.map(row).join('')}
    <div class="ac-foot">${acItems.length ? '↑ ↓ choose · Enter or Tab to link · ' : ''}Esc to close</div>`, true);
}
// #tag suggestions: reuse the tags you already have, or make a new one on the spot
const tagCache = {};        // project id -> { at, list }
const TYPE_TAGS = { todo: 'task', idea: 'idea', question: 'question' };   // a trailing #todo/#idea/#question also sets the type
async function tagsFor(pid) {
  const c = tagCache[pid];
  if (c && Date.now() - c.at < 60000) return c.list;
  const list = (await api('GET', `/api/projects/${pid}/tags`)).tags;
  for (const t of Object.keys(TYPE_TAGS)) if (!list.some(x => x.tag === t)) list.push({ tag: t, notes: 0, items: 0 });
  tagCache[pid] = { at: Date.now(), list };
  return list;
}
async function tagSuggest(ta, frag) {
  const seq = ++acSeq, p = captureProject(ta.value);
  const list = p ? await tagsFor(p.id) : Object.keys(TYPE_TAGS).map(tag => ({ tag, notes: 0, items: 0 }));
  if (seq !== acSeq || $('#ac') === null) return;
  const f = frag.toLowerCase();
  acMode = 'tag';
  acItems = list.filter(x => x.tag.includes(f))
    .sort((a, b) => (b.tag.startsWith(f) - a.tag.startsWith(f)) || (b.notes - a.notes) || (b.items - a.items) || a.tag.localeCompare(b.tag))
    .slice(0, 8).map(x => ({ ...x }));
  if (f && !list.some(x => x.tag === f)) acItems.push({ tag: f, create: true });
  acIndex = 0;
  const head = p ? (f ? `Tags in @${p.handle}` : `Tags in @${p.handle} · type to filter or make a new one`) : 'Tags';
  const row = (it, i) => `<div data-i="${i}" class="${i === 0 ? 'on' : ''}">${it.create
    ? `<span class="ac-l"><span class="plus">＋</span>New tag #${esc(it.tag)}</span><span class="ac-sub">Creates it with this note</span>`
    : `<span class="ac-l"><span class="ac-t">#${hl(it.tag, f)}</span><span class="grow"></span><span class="ac-m">${it.notes ? `in ${it.notes} note${it.notes === 1 ? '' : 's'}` : it.items ? `on ${it.items} item${it.items === 1 ? '' : 's'}` : TYPE_TAGS[it.tag] ? `also sets the type to ${kindName(TYPE_TAGS[it.tag]).toLowerCase()}` : 'not used yet'}</span></span>`}</div>`;
  showAc(ta, `<div class="ac-head">${esc(head)}</div>${acItems.map(row).join('')}
    <div class="ac-foot">${acItems.length ? '↑ ↓ choose · Enter or Tab to insert · ' : ''}Esc to close</div>`, true);
}
function insertConceptLink() {
  const ta = $('#captureText'); if (!ta) return;
  const s = ta.selectionStart, pre = ta.value.slice(0, s);
  const ins = (pre && !/\s$/.test(pre) ? ' ' : '') + '[[';
  ta.value = pre + ins + ta.value.slice(ta.selectionEnd);
  ta.focus(); ta.selectionStart = ta.selectionEnd = s + ins.length;
  autocomplete(ta);
}
let linkSeq = 0;
async function updateLinkState() {
  const ta = $('#captureText'), box = $('#linkState'); if (!ta || !box) return;
  const names = [...new Set([...ta.value.matchAll(/\[\[([^\[\]\n]{1,200})\]\]/g)].map(m => m[1].trim()).filter(Boolean))];
  const seq = ++linkSeq, p = captureProject(ta.value);
  const list = names.length && p ? await conceptsFor(p.id) : [];
  if (seq !== linkSeq) return;
  const find = n => list.find(c => [c.title, ...c.aliases].some(a => foldKey(a) === foldKey(n)));
  box.innerHTML = names.length ? `<span>Linked concepts</span>${names.map(n => {
    const c = find(n);
    return c ? `<span class="chip ok" data-node="${c.id}" title="Adds to your existing ${esc(kindName(c.kind).toLowerCase())}">✓ ${esc(short(c.title, 40))}</span>`
      : `<span class="chip newc" title="Not found in ${p ? '@' + esc(p.handle) : 'this project'}: a new concept will be created">＋ ${esc(short(n, 40))} <i>new</i></span>`;
  }).join('')}` : '';
}

async function doCapture(dry) {
  const ta = $('#captureText'); if (!ta) return;
  const text = ta.value.trim();
  if (!text) return toast('Nothing to capture yet');
  const body = { text, project_id: S.pid, dry_run: dry, source: $('#captureSource').value.trim() };
  if (S.captureTarget) Object.assign(body, { target_id: S.captureTarget.id, target_mode: S.captureTarget.mode });
  try {
    let r = await api('POST', '/api/capture', body);
    if (r.unknown) {
      if (!(await confirmBox(`Create new project${r.unknown.length > 1 ? 's' : ''} ${r.unknown.map(h => '<b>@' + esc(h) + '</b>').join(', ')}?`, 'Create'))) return;
      r = await api('POST', '/api/capture', { ...body, create_missing: true });
    }
    S.lastCapture = r;
    if (!dry) {
      ta.value = ''; $('#captureSource').value = ''; $('#linkState').innerHTML = '';
      for (const k in conceptCache) delete conceptCache[k];       // the note may have added concepts and tags
      for (const k in tagCache) delete tagCache[k];
      if (S.captureTarget) { S.captureTarget = null; $('.target-banner')?.remove(); }
      try { localStorage.removeItem('atlas.draft'); } catch (_) { /* ignore */ }
      await loadState();
      if (r.results.length && !r.results.some(x => x.project.id === S.pid)) { S.pid = r.results[0].project.id; try { localStorage.setItem('atlas.pid', S.pid); } catch (_) { /* ignore */ } }
      S.nodes = null; renderChrome(); updateTargets();
      const added = r.results.reduce((a, x) => a + x.added.length, 0);
      toast(`Mapped: ${added} new item${added === 1 ? '' : 's'}`);
    }
    renderCaptureResult(r);
  } catch (e) { fail(e); }
}

// Notices under a captured note. Every action is optional: skipping one changes nothing and never blocks the next note.
const ISSUE_ICON = { conflict: '⚔️', definition: '📖', alias: '🤔', kind: '🤔', duplicate: '♻️', promoted: '🌱' };
const ISSUE_LABEL = { conflict: 'Conflict', definition: 'Two definitions', alias: 'Same name?', kind: 'Ambiguous', duplicate: 'Already in your map', promoted: 'New concept' };
function issueHtml(it, ri, ii, dry) {
  if (it.done) return `<div class="callout issue done" data-r="${ri}" data-i="${ii}">✓ ${esc(it.done)}${it.done_node ? ` ${nodeLink(it.done_node, it.done_title, it.done_kind)}` : ''}</div>`;
  const link = k => it[k + '_title'] ? nodeLink(dry ? '' : it[k], it[k + '_title'], it[k + '_kind']) : '';
  const b = (act, label, primary = false, extra = '') => `<button type="button" class="sm${primary ? ' primary' : ''}" data-act="issue" data-do="${act}" ${extra}>${esc(label)}</button>`;
  let acts = [];
  if (!dry) {
    if (it.type === 'conflict') acts = [b('resolve', 'Mark resolved'), b('not-conflict', 'Not a contradiction'), b('keep', 'Decide later')];
    else if (it.type === 'definition') acts = [b('use-def', 'Use the new definition', true), b('keep', 'Keep the current one')];
    else if (it.type === 'alias' && it.node) acts = [b('merge', 'Same thing: merge them', true), b('keep', 'Keep separate')];
    else if (it.type === 'kind' && it.node) acts = [...it.kinds.filter(k => k !== it.filed).map(k => b('kind', `File as ${kindName(k).toLowerCase()}`, false, `data-kind="${esc(k)}"`)), b('keep', `Keep as ${kindName(it.filed).toLowerCase()}`)];
    else if (it.type === 'duplicate') acts = [b('keep', 'Yes, same item', true), b('split', 'No, keep it separate')];
    else if (it.type === 'promoted') acts = [b('keep', 'Keep it', true), b('remove', 'Not a concept: remove')];
  }
  return `<div class="callout issue ${it.type === 'conflict' || it.type === 'definition' ? '' : 'info'}" data-r="${ri}" data-i="${ii}">
    <div><b>${ISSUE_ICON[it.type] || ''} ${ISSUE_LABEL[it.type] || ''}</b> · ${esc(it.text)}</div>
    ${it.type === 'definition' ? `<div class="defs"><div><span class="mute small">Current</span>${esc(it.current)}</div><div><span class="mute small">New</span>${esc(it.definition)}</div></div>` : ''}
    ${it.node_title || it.other_title ? `<div class="issue-links">${link('node')}${it.other_title ? link('other') : ''}</div>` : ''}
    ${acts.length ? `<div class="row issue-acts">${acts.join('')}</div>` : ''}</div>`;
}
async function issueAction(btn) {
  const box = btn.closest('.issue'), ri = +box.dataset.r, ii = +box.dataset.i;
  const it = S.lastCapture?.results?.[ri]?.issues?.[ii]; if (!it) return;
  const act = btn.dataset.do;
  $$('button', box).forEach(x => { x.disabled = true; });
  try {
    if (act === 'resolve') { await api('PATCH', `/api/edges/${it.edge}`, { status: 'resolved' }); it.done = 'Marked resolved.'; }
    else if (act === 'not-conflict') { await api('PATCH', `/api/edges/${it.edge}`, { type: 'relates to' }); it.done = 'Changed to a plain “related to” link; no conflict.'; }
    else if (act === 'use-def') { await api('PATCH', `/api/nodes/${it.node}`, { definition: it.definition }); it.done = `Now using the new definition for “${short(it.node_title, 50)}”. The old one is kept in its card.`; }
    else if (act === 'merge') { await api('POST', `/api/nodes/${it.node}/merge`, { into: it.other }); it.done = `Merged into`; Object.assign(it, { done_node: it.other, done_title: it.other_title, done_kind: it.other_kind }); }
    else if (act === 'kind') { await api('PATCH', `/api/nodes/${it.node}`, { kind: btn.dataset.kind }); it.done = `Filed as ${kindName(btn.dataset.kind).toLowerCase()}:`; Object.assign(it, { done_node: it.node, done_title: it.node_title, done_kind: btn.dataset.kind }); }
    else if (act === 'split') { const c = await api('POST', `/api/nodes/${it.node}/split`, { note_id: it.note }); it.done = 'Kept as a separate item:'; Object.assign(it, { done_node: c.node.id, done_title: c.node.title, done_kind: c.node.kind }); }
    else if (act === 'remove') { await api('DELETE', `/api/nodes/${it.node}`); it.done = `Removed “${short(it.node_title, 50)}”.`; }
    else it.done = 'Kept as it is.';
    if (act !== 'keep') { S.nodes = null; for (const k in conceptCache) delete conceptCache[k]; }
    const head = box.parentElement.querySelector('.issues-head');
    box.outerHTML = issueHtml(it, ri, ii, false);
    const left = S.lastCapture.results[ri].issues.filter(x => !x.done).length;
    if (head) head.textContent = left ? `${left} thing${left === 1 ? '' : 's'} you may want to check. Optional: act now, later, or not at all.` : 'All checked.';
  } catch (e) { $$('button', box).forEach(x => { x.disabled = false; }); fail(e); }
}

function renderCaptureResult(r) {
  const box = $('#captureResult'); if (!box) return;
  box.innerHTML = r.results.map((x, i) => {
    const chips = (list, isNew) => list.map(n => `<span class="chip ${isNew ? 'k-new' : ''}" data-node="${r.dry_run ? '' : n.id}" title="${esc(kindName(n.kind))}"><i class="dot ${esc(n.kind)}"></i>${esc(short(n.title, 60))}</span>`).join('');
    const links = [...x.edges, ...x.suggested];
    const open = (x.issues || []).filter(it => !it.done).length;
    const notes = x.issues ? (x.issues.length ? `<div class="issues"><div class="small mute issues-head">${r.dry_run
        ? 'Add the note to act on these.'
        : `${open ? `${open} thing${open === 1 ? '' : 's'} you may want to check.` : 'All checked.'} Optional: act now, later, or not at all. Your next note can go in right away.`}</div>
      ${x.issues.map((it, j) => issueHtml(it, i, j, r.dry_run)).join('')}</div>` : '')
      : [   // results from before issues existed
      ...x.conflicts.map(c => `<div class="callout">⚔️ Conflict for review: ${esc(c)}</div>`),
      ...x.ambiguities.map(c => `<div class="callout">🤔 Ambiguous: ${esc(c)}</div>`),
      ...x.merged.map(c => `<div class="callout info">♻️ ${esc(c)}</div>`),
      ...(x.promoted.length ? [`<div class="callout info">🌱 A phrase you keep using is now a concept: ${x.promoted.map(esc).join(', ')}</div>`] : []),
    ].join('');
    const shown = links.filter(e => e.type !== 'derived from');
    return `<div class="panel result">
      <div class="row"><b>Saved to @${esc(x.project.handle)}</b>${r.dry_run ? '<span class="badge sugg">preview, not saved</span>' : ''}<span class="grow"></span>
      ${r.dry_run ? '' : `<button class="sm" data-act="undo-note" data-id="${x.note.id}">Undo</button>`}</div>
      ${notes}
      <h4>Added or updated <span class="n">(${x.added.length} new · ${x.updated.length} updated)</span></h4>
      <div>${chips(x.added, true)}${chips(x.updated, false) || ''}${!x.added.length && !x.updated.length ? '<span class="mute">Nothing was picked up from this note.</span>' : ''}</div>
      <h4>Connections <span class="n">(${shown.length})</span></h4>
      <ul class="links">${shown.map(e => `<li>${nodeLink(r.dry_run ? '' : e.src, e.src_title, e.src_kind)}<span class="rel">${esc(e.type)}</span>${nodeLink(r.dry_run ? '' : e.dst, e.dst_title, e.dst_kind)} ${edgeBadges(e)}</li>`).join('') || '<li class="mute">No connections yet</li>'}</ul>
      <h4>On the map</h4><div class="mapbox small" id="capmap${i}"></div>
      <h4>Open quests</h4>${x.quests.length ? x.quests.map(q => questRow(q, x.project.id, true)).join('') : '<p class="mute">No new quests from this note.</p>'}
    </div>`;
  }).join('');
  r.results.forEach((x, i) => { const el = $('#capmap' + i); enablePan(el); renderMermaid(el, x.mermaid); });
}

// ------------------------------------------------------------------ map
async function renderMap(main) {
  if (!S.pid) return renderWelcome(main);
  const m = S.map;
  main.innerHTML = `<div class="wrap" style="max-width:none">
    <div class="mapbar">
      <div class="picker" style="width:260px"><input type="text" id="mapFocus" placeholder="Focus on a concept…" value="${esc(m.focusLabel)}" autocomplete="off"><div class="ac" hidden></div></div>
      ${m.focus ? '<button class="sm" data-act="map-clear">Whole map</button>' : ''}
      <label class="small">Depth <select id="mapDepth">${[1, 2, 3].map(d => `<option ${d === m.depth ? 'selected' : ''}>${d}</option>`).join('')}</select></label>
      <label class="small"><input type="checkbox" id="mapSources" ${m.sources ? 'checked' : ''}> sources</label>
      <label class="small"><input type="checkbox" id="mapSugg" ${m.suggested ? 'checked' : ''}> suggested links</label>
      <span class="grow"></span>
      <button class="sm" data-act="zoom-fit" title="Fit the map to the window">Fit</button>
      <button class="sm" data-act="zoom" data-z="-1">−</button><span class="small mute" id="zoomLbl">${Math.round(m.zoom * 100)}%</span><button class="sm" data-act="zoom" data-z="1">＋</button>
    </div>
    <div class="kfilter" style="margin-bottom:10px">${S.kinds.filter(k => k !== 'reference').map(k => `<label><input type="checkbox" data-kind="${k}" ${!m.kinds.size || m.kinds.has(k) ? 'checked' : ''}>${kindPill(k)}</label>`).join('')}</div>
    <div id="mapNote" class="small mute" style="margin-bottom:6px"></div>
    <div class="mapbox" id="mapBox"><div class="empty">Drawing…</div></div>
  </div>`;
  nodePicker($('#mapFocus'), n => { m.focus = n.id; m.focusLabel = n.title; renderMap(main); });
  $('#mapDepth').onchange = e => { m.depth = +e.target.value; drawMap(); };
  $('#mapSources').onchange = e => { m.sources = e.target.checked; drawMap(); };
  $('#mapSugg').onchange = e => { m.suggested = e.target.checked; drawMap(); };
  $$('.kfilter input').forEach(cb => cb.onchange = () => {
    const on = $$('.kfilter input').filter(x => x.checked).map(x => x.dataset.kind);
    m.kinds = new Set(on.length === $$('.kfilter input').length ? [] : on); drawMap();
  });
  enablePan($('#mapBox'));
  await drawMap();
}
async function drawMap() {
  const m = S.map, box = $('#mapBox'); if (!box) return;
  const q = new URLSearchParams({ depth: m.depth, sources: m.sources ? 1 : 0, suggested: m.suggested ? 1 : 0, limit: m.focus ? 90 : 45 });
  if (m.focus) q.set('focus', m.focus);
  if (m.kinds.size) q.set('kinds', [...m.kinds, ...(m.sources ? ['reference'] : [])].join(','));
  const g = await api('GET', `/api/projects/${S.pid}/graph?${q}`);
  $('#mapNote').textContent = !g.nodes.length ? '' : g.truncated
    ? `Showing the ${g.nodes.length} most connected of ${g.total} items. Focus on one to see its neighbourhood.`
    : `${g.nodes.length} items · ${g.edges.length} connections. Click an item to open it; drag to move around.`;
  if (!g.nodes.length) { box.innerHTML = '<div class="empty">The map is empty. Capture a note to start it.</div>'; return; }
  await renderMermaid(box, g.mermaid, m.zoom);
  if (m.autoFit !== false) fitMap();
}
function fitMap() {
  const box = $('#mapBox'), svg = box?.querySelector('svg');
  if (!svg || !svg.viewBox.baseVal.width) return;
  const vb = svg.viewBox.baseVal;
  // fit the width (scrolling down is natural), but never so small that labels become unreadable
  S.map.zoom = Math.max(.6, Math.min(1.2, (box.clientWidth - 24) / vb.width));
  applyZoom(box, S.map.zoom);
  box.scrollLeft = 0; box.scrollTop = 0;
  if ($('#zoomLbl')) $('#zoomLbl').textContent = Math.round(S.map.zoom * 100) + '%';
}

// ------------------------------------------------------------------ cards
async function renderCards(main) {
  if (!S.pid) return renderWelcome(main);
  const c = S.cards;
  const q = new URLSearchParams({ sort: c.sort });
  for (const k of ['kind', 'status', 'confidence']) if (c[k]) q.set(k, c[k]);
  const { nodes } = await api('GET', `/api/projects/${S.pid}/nodes?${q}`);
  const f = c.q.toLowerCase();
  const list = nodes.filter(n => !f || (n.title + ' ' + n.definition + ' ' + n.body).toLowerCase().includes(f));
  const opt = (name, label, pairs, cur) => `<select data-filter="${name}"><option value="">${label}: all</option>${pairs.map(([v, l]) => `<option value="${v}" ${v === cur ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  main.innerHTML = `<div class="wrap">
    <div class="row" style="margin-bottom:12px">
      <input type="text" id="cardFilter" placeholder="Filter cards…" value="${esc(c.q)}" style="width:220px">
      ${opt('kind', 'Type', S.kinds.map(k => [k, kindName(k)]), c.kind)}
      ${opt('status', 'Status', [['open', 'Open'], ['active', 'Active'], ['resolved', 'Resolved']], c.status)}
      ${opt('confidence', 'Certainty', [['high', 'Certain'], ['medium', 'Normal'], ['low', 'Uncertain']], c.confidence)}
      <select data-filter="sort">${[['updated', 'Recently changed'], ['code', 'By type'], ['title', 'A–Z'], ['links', 'Most connected']].map(([v, l]) => `<option value="${v}" ${v === c.sort ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <span class="grow"></span><span class="mute small">${list.length} cards</span>
      <button data-act="compare-selected" ${S.selected.size < 2 ? 'disabled' : ''}>Compare selected (${S.selected.size})</button>
    </div>
    <div class="cards">${list.map(n => `<div class="ncard ${S.selected.has(n.id) ? 'selected' : ''}" data-node="${n.id}">
      <input type="checkbox" class="sel" data-sel="${n.id}" ${S.selected.has(n.id) ? 'checked' : ''} title="Select for comparison">
      <div class="row" style="gap:6px">${kindPill(n.kind)}${n.confidence === 'low' ? '<span class="badge hyp">uncertain</span>' : ''}${n.status === 'resolved' ? '<span class="badge">resolved</span>' : ''}</div>
      <div class="t">${esc(n.title)}</div>
      <div class="d">${esc(n.definition || (n.body !== n.title ? n.body : ''))}</div>
      <div class="small mute">${n.links} connection${n.links === 1 ? '' : 's'} · ${n.sources} note${n.sources === 1 ? '' : 's'}</div>
    </div>`).join('') || '<div class="empty">No cards match.</div>'}</div></div>`;
  $('#cardFilter').oninput = e => { c.q = e.target.value; clearTimeout(renderCards.t); renderCards.t = setTimeout(() => renderCards(main).then(() => { const i = $('#cardFilter'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }), 250); };
  $$('[data-filter]').forEach(s => s.onchange = () => { c[s.dataset.filter] = s.value; renderCards(main); });
  $$('[data-sel]').forEach(cb => cb.onclick = e => { e.stopPropagation(); const id = +cb.dataset.sel; cb.checked ? S.selected.add(id) : S.selected.delete(id); renderCards(main); });
}

// ------------------------------------------------------------------ quests
const ACTION_LABEL = { open: 'Open', 'resolve-node': 'Mark resolved', 'resolve-edge': 'Mark resolved', 'capture-answer': 'Write answer', 'capture-evidence': 'Add evidence', dismiss: 'Dismiss' };
function questRow(q, pid, compact = false) {
  const btn = a => {
    const [kind] = a.split(':');
    const label = { 'accept-edge': 'Accept', 'reject-edge': 'Reject', link: 'Link…', merge: 'Merge' }[kind] || ACTION_LABEL[a] || a;
    return `<button class="sm ${kind === 'accept-edge' || kind === 'resolve-node' || kind === 'resolve-edge' ? 'primary' : ''}" data-act="quest" data-a="${esc(a)}" data-key="${esc(q.key)}" data-pid="${pid}" data-xp="${q.xp}" data-nodes="${q.nodes.join(',')}" data-type="${q.type}">${label}</button>`;
  };
  const acts = [...q.actions];
  if (!compact && !acts.includes('open')) acts.push('open');
  if (!compact && !acts.includes('dismiss')) acts.push('dismiss');
  return `<div class="quest" data-titles="${esc(JSON.stringify(q.titles || []))}"><div class="ico">${QUEST_ICON[q.type] || '★'}</div>
    <div><div class="qt">${esc(q.title)} <span class="badge">+${q.xp} XP</span></div><div class="qd">${esc(q.detail)}</div></div>
    <div class="acts">${acts.map(btn).join('')}</div></div>`;
}
async function renderQuests(main) {
  if (!S.pid) return renderWelcome(main);
  const q = await api('GET', `/api/projects/${S.pid}/quests`);
  S.xp = q.xp; renderChrome();
  const total = q.done + q.open;
  const groups = [
    ['Conflicts & contradictions', ['contradiction', 'definition-conflict']],
    ['Questions to answer', ['answer', 'verify']],
    ['Gaps to fill', ['evidence', 'assumption', 'mitigate', 'define']],
    ['Connections to make', ['suggested', 'comention', 'duplicate', 'isolated', 'cluster']],
    ['Next steps', ['task']],
  ];
  main.innerHTML = `<div class="wrap">
    <div class="panel level"><div class="big">Lv ${q.level}</div>
      <div class="grow"><div><b>${q.rank}</b> · ${q.xp} XP · ${q.next_level_xp} XP to next level</div>
      <div class="progress" style="margin:8px 0"><i style="width:${total ? Math.round(100 * q.done / total) : 0}%"></i></div>
      <div class="small mute">${q.done} quests completed · ${q.open} open in @${esc(proj().handle)}</div></div></div>
    ${groups.map(([title, types]) => {
      const items = q.quests.filter(x => types.includes(x.type));
      return items.length ? `<h3>${title} <span class="mute">(${items.length})</span></h3>${items.map(x => questRow(x, S.pid)).join('')}` : '';
    }).join('') || '<div class="panel empty">🎉 No open quests. Capture more notes to grow the map.</div>'}
  </div>`;
}

async function questAction(b) {
  const { a, key, pid, xp, nodes, type } = b.dataset;
  const ids = nodes.split(',').filter(Boolean).map(Number);
  let ts = [];
  try { ts = JSON.parse(b.closest('.quest')?.dataset.titles || '[]').map(t => short(t, 70)); } catch (_) { /* no titles */ }
  const done = async (status = 'done') => { const r = await api('POST', `/api/projects/${pid}/quests`, { key, status, xp: status === 'done' ? +xp : 0 }); S.xp = r.xp; if (status === 'done') toast(`Quest complete: +${xp} XP`); };
  const [kind, x, y] = a.split(':');
  if (kind === 'open') return openCard(ids[0]);
  if (kind === 'dismiss') { await done('dismissed'); return rerender(); }
  if (kind === 'resolve-node') { await api('PATCH', `/api/nodes/${ids[0]}`, { status: 'resolved' }); await done(); return rerender(); }
  if (kind === 'resolve-edge') { await api('PATCH', `/api/edges/${key.split(':')[1]}`, { status: 'resolved' }); await done(); return rerender(); }
  if (kind === 'accept-edge') { await api('PATCH', `/api/edges/${x}`, { status: 'active' }); await done(); return rerender(); }
  if (kind === 'reject-edge') { await api('PATCH', `/api/edges/${x}`, { status: 'rejected' }); await done('dismissed'); return rerender(); }
  if (kind === 'merge') {
    if (!(await confirmBox(`Merge “<b>${esc(ts[1] || '')}</b>” into “<b>${esc(ts[0] || '')}</b>”? Its other names, notes and connections move over.`, 'Merge'))) return;
    await api('POST', `/api/nodes/${x}/merge`, { into: +y }); S.nodes = null; await done(); return rerender();
  }
  if (kind === 'link') {
    const r = await modal(`<form><h2>Connect these two</h2><p>“${esc(ts[0] || '')}”</p><label>…how does it relate to…</label>
      <select name="type">${S.edgeTypes.map(t => `<option ${t === 'relates to' ? 'selected' : ''}>${t}</option>`).join('')}</select>
      <p>“${esc(ts[1] || '')}”</p>
      <label><input type="checkbox" name="flip"> the other way round</label>
      <div class="row" style="justify-content:flex-end;margin-top:12px"><button type="button" data-close>Cancel</button><button class="primary">Link</button></div></form>`);
    if (!r) return;
    const [s, d] = r.flip ? [+y, +x] : [+x, +y];
    await api('POST', '/api/edges', { src: s, dst: d, type: r.type }); await done(); return rerender();
  }
  if (kind === 'capture-answer' || kind === 'capture-evidence') {
    // the capture box shows "Answering: <title>" and links the note to it; nothing to type but the answer
    S.captureTarget = { id: ids[0], title: ts[0] || '', mode: kind === 'capture-evidence' ? 'evidence' : 'answer' };
    if (+pid !== S.pid) setProject(+pid);
    try { localStorage.setItem('atlas.draft', ''); } catch (_) { /* ignore */ }
    S.lastCapture = null; closeDrawer(); go('capture');
  }
}
function rerender() { renderChrome(); if (S.drawerId) openCard(S.drawerId, true); return render(); }

// ------------------------------------------------------------------ notes
async function renderNotes(main) {
  if (!S.pid) return renderWelcome(main);
  const { notes } = await api('GET', `/api/projects/${S.pid}/notes`);
  main.innerHTML = `<div class="wrap"><h2>Notes in @${esc(proj().handle)} <span class="mute">(${notes.length})</span></h2>
    <p class="mute small">Your original text is kept verbatim. Deleting a note removes the links it created and any items only it mentioned.</p>
    ${notes.map(n => `<div class="note" id="note-${n.id}">
      <div class="row"><span class="small"><b>${new Date(n.created * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</b></span>
      ${n.source ? (n.source_node ? `<span class="badge nodelink" data-node="${n.source_node}" title="Open source">📎 ${esc(short(n.source.split(/[\\/]/).pop(), 60))}</span>` : `<span class="badge">${esc(n.source)}</span>`) : ''}${n.ai_reviewed ? '<span class="badge ai">reviewed by AI</span>' : ''}
      ${n.tags.map(t => `<span class="badge">#${esc(t)}</span>`).join('')}<span class="grow"></span>
      <button class="sm" data-act="note-reuse" data-id="${n.id}">Copy to capture</button><button class="sm danger" data-act="note-delete" data-id="${n.id}">Delete</button></div>
      <pre>${esc(n.text)}</pre>
      <div>${n.nodes.filter(x => x.kind !== 'reference').map(x => `<span class="chip" data-node="${x.id}" title="${esc(kindName(x.kind))}"><i class="dot ${esc(x.kind)}"></i>${esc(short(x.title, 50))}</span>`).join('')}</div>
    </div>`).join('') || '<div class="panel empty">No notes yet.</div>'}</div>`;
  main._notes = notes;
  const el = S.focusNote && $(`#note-${S.focusNote}`);   // opened from a search result
  S.focusNote = null;
  if (el) { el.scrollIntoView({ block: 'start' }); el.classList.add('flash'); }
}

// ------------------------------------------------------------------ traceability matrix (requirements + the user's tracking)
const REQ_STATUS_LABEL = { 'not started': 'Not started', 'in progress': 'In progress', 'at risk': 'At risk', blocked: 'Blocked', done: 'Done', dropped: 'Dropped' };
const reqSlug = s => (s || '').replace(/\s+/g, '-');
S.trace = { q: '', grp: '', status: '' };
async function renderTrace(main) {
  if (!S.pid) return renderWelcome(main);
  const { requirements: R, statuses } = await api('GET', `/api/projects/${S.pid}/requirements`);
  main._reqs = R; main._statuses = statuses;
  if (!R.length) {
    main.innerHTML = `<div class="wrap"><div class="panel empty">No requirements for @${esc(proj().handle)} yet.<br>Ask Claude in Claude Code to extract them from a project document, such as the proposal's objectives and KPIs.</div></div>`;
    return;
  }
  const groups = [...new Map(R.map(r => [r.grp, r.grp_title])).entries()];
  const src = [...new Set(R.map(r => r.source).filter(Boolean))].map(s => s.split(/[\\/]/).pop());
  main.innerHTML = `<div class="wrap" style="max-width:none">
    <div class="row" style="margin-bottom:6px"><h2 class="grow" style="margin:0">Traceability matrix · @${esc(proj().handle)}</h2>
      <button class="sm" data-act="req-csv" title="Download the matrix with your tracking as a spreadsheet file">Export CSV</button></div>
    <p class="small mute" style="margin:0 0 12px">${R.length} requirements${src.length ? ` from ${src.map(esc).join(', ')}` : ''}. Requirement text is quoted from the source; status, progress, owner, due date and notes are yours, and every change is kept in the history. <b>Brief</b> shows everything your notes say about a requirement.</p>
    <div id="traceSummary"></div>
    <div class="row trace-filters"><input type="search" id="trq" class="grow" placeholder="Filter requirements…" value="${esc(S.trace.q)}" autocomplete="off">
      <select id="trg"><option value="">All objectives</option>${groups.map(([g, t]) => `<option value="${esc(g)}" ${g === S.trace.grp ? 'selected' : ''}>${esc(g)} · ${esc(short(t, 50))}</option>`).join('')}</select>
      <select id="trs"><option value="">Any status</option>${statuses.map(s => `<option value="${esc(s)}" ${s === S.trace.status ? 'selected' : ''}>${REQ_STATUS_LABEL[s] || s}</option>`).join('')}</select></div>
    <div class="trace-wrap"><table class="trace"><thead><tr><th>ID</th><th>Requirement</th><th>Traces to</th><th>Evidence in notes</th><th>Status</th><th>Progress</th><th>Owner</th><th>Due</th><th>Your tracking note</th><th></th></tr></thead>
      <tbody id="traceBody"></tbody></table></div></div>`;
  const redraw = () => { traceSummary(main); traceRows(main); };
  $('#trq').oninput = e => { S.trace.q = e.target.value; traceRows(main); };
  $('#trg').onchange = e => { S.trace.grp = e.target.value; traceRows(main); };
  $('#trs').onchange = e => { S.trace.status = e.target.value; traceRows(main); };
  // edits save as soon as a field is left (or a choice is made); nothing else to press
  $('#traceBody').addEventListener('change', async e => {
    const el = e.target.closest('[data-f]'), tr = e.target.closest('tr[data-rid]'); if (!el || !tr) return;
    try {
      const upd = await api('PATCH', `/api/projects/${S.pid}/requirements/${encodeURIComponent(tr.dataset.rid)}`, { [el.dataset.f]: el.value });
      main._reqs = main._reqs.map(r => r.rid === upd.rid ? upd : r);
      tr.outerHTML = traceRow(upd, main._statuses);
      traceSummary(main);
      toast(`${upd.rid} saved`);
    } catch (err) { fail(err); }
  });
  $('#traceBody').addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('input[data-f]')) e.target.blur(); });
  redraw();
}
function traceSummary(main) {
  const R = main._reqs, by = {};
  R.forEach(r => { by[r.status] = (by[r.status] || 0) + 1; });
  const live = R.filter(r => r.status !== 'dropped');
  const avg = live.length ? Math.round(live.reduce((a, r) => a + r.progress, 0) / live.length) : 0;
  $('#traceSummary').innerHTML = `<div class="trace-sum"><div class="trace-overall"><b>${avg}%</b> overall<div class="pbar big"><i style="width:${avg}%"></i></div></div>
    ${main._statuses.map(s => `<button class="tchip st-${reqSlug(s)}${S.trace.status === s ? ' on' : ''}" data-act="req-filter" data-status="${esc(s)}">${REQ_STATUS_LABEL[s]} <b>${by[s] || 0}</b></button>`).join('')}</div>`;
}
function traceRows(main) {
  const q = S.trace.q.trim().toLowerCase();
  const rows = main._reqs.filter(r => (!S.trace.grp || r.grp === S.trace.grp) && (!S.trace.status || r.status === S.trace.status)
    && (!q || `${r.rid} ${r.title} ${r.text} ${r.node_title || ''} ${r.owner} ${r.note} ${r.wp}`.toLowerCase().includes(q)));
  let html = '', last = null;
  for (const r of rows) {
    if (r.grp !== last) {
      const g = main._reqs.filter(x => x.grp === r.grp && x.status !== 'dropped');
      const avg = g.length ? Math.round(g.reduce((a, x) => a + x.progress, 0) / g.length) : 0;
      html += `<tr class="grp"><td colspan="10"><b>${esc(r.grp)}</b> ${esc(r.grp_title)} <span class="mute small">· ${avg}% · ${g.filter(x => x.status === 'done').length}/${g.length} done</span></td></tr>`;
      last = r.grp;
    }
    html += traceRow(r, main._statuses);
  }
  $('#traceBody').innerHTML = html || '<tr><td colspan="10" class="empty">No requirement matches.</td></tr>';
}
function traceRow(r, statuses) {
  return `<tr data-rid="${esc(r.rid)}" class="st-${reqSlug(r.status)}">
    <td class="rid">${esc(r.rid)}</td>
    <td class="req"><div class="rt">${esc(r.title)}</div>${r.text ? `<div class="rx">${esc(r.text)}</div>` : ''}${r.context ? `<div class="rc">${esc(r.context)}</div>` : ''}</td>
    <td class="links">${r.node_id ? nodeLink(r.node_id, r.node_title, r.node_kind) : '<span class="mute">no concept</span>'}
      <div class="small mute">${[r.wp, r.challenges, r.page ? `p.${r.page}` : ''].filter(Boolean).map(esc).join(' · ')}</div></td>
    <td class="ev">${r.notes ? `<b>${r.notes}</b> note${r.notes === 1 ? '' : 's'}<div class="small mute">last ${day(r.last_note)}</div>` : '<span class="mute">none yet</span>'}</td>
    <td><select data-f="status" class="st">${statuses.map(s => `<option value="${esc(s)}" ${s === r.status ? 'selected' : ''}>${REQ_STATUS_LABEL[s] || s}</option>`).join('')}</select></td>
    <td class="prog"><input type="number" data-f="progress" min="0" max="100" step="5" value="${r.progress}"><span class="small mute">%</span><div class="pbar"><i style="width:${r.progress}%"></i></div></td>
    <td><input type="text" data-f="owner" value="${esc(r.owner)}" placeholder="—"></td>
    <td><input type="text" data-f="due" value="${esc(r.due)}" placeholder="e.g. M18"></td>
    <td class="tnote"><textarea data-f="note" rows="2" placeholder="Progress, blockers, links…">${esc(r.note)}</textarea>
      <div class="small mute">${r.updated && r.changes ? `updated ${day(r.updated)} · <a data-act="req-log" data-rid="${esc(r.rid)}">history (${r.changes})</a>` : ''}</div></td>
    <td><button class="sm primary" data-act="req-brief" data-q="${esc(r.node_title || r.title)}" title="Everything your notes say about “${esc(r.node_title || r.title)}”">Brief</button></td></tr>`;
}
async function reqHistory(rid) {
  const { log } = await api('GET', `/api/projects/${S.pid}/requirements/${encodeURIComponent(rid)}/log`);
  const F = { status: 'Status', progress: 'Progress', owner: 'Owner', due: 'Due', note: 'Note' };
  const v = (f, x) => f === 'status' ? (REQ_STATUS_LABEL[x] || x) : f === 'progress' ? `${x}%` : (x || '—');
  await modal(`<form><div class="row"><h2 class="grow" style="margin:0">History · ${esc(rid)}</h2><button type="button" data-close>Close</button></div>
    <ul class="links" style="margin-top:10px">${log.map(l => `<li><span class="small mute" style="width:150px">${new Date(l.at * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</span>
      <b>${F[l.field] || l.field}</b><span>${esc(short(v(l.field, l.old), 60))} → ${esc(short(v(l.field, l.new), 80))}</span></li>`).join('') || '<li class="mute">No changes yet.</li>'}</ul></form>`);
}
function reqCsv() {
  const R = $('#main')._reqs || [];
  const cols = [['rid', 'ID'], ['grp', 'Objective'], ['title', 'Requirement'], ['text', 'Target (source text)'], ['node_title', 'Concept'], ['wp', 'Work packages'], ['challenges', 'Challenges'], ['page', 'Page'], ['status', 'Status'], ['progress', 'Progress %'], ['owner', 'Owner'], ['due', 'Due'], ['note', 'Tracking note'], ['notes', 'Notes with evidence']];
  const cell = x => `"${String(x ?? '').replace(/"/g, '""')}"`;
  const csv = '﻿' + [cols.map(c => cell(c[1])).join(','), ...R.map(r => cols.map(c => cell(r[c[0]])).join(','))].join('\r\n');
  const name = `traceability-${proj().handle}-${new Date().toISOString().slice(0, 10)}.csv`;
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast(`Saved ${name} to your Downloads folder`);
}

// ------------------------------------------------------------------ compare
async function renderCompare(main) {
  if (!S.pid) return renderWelcome(main);
  const ids = [...S.selected];
  const rows = ids.length ? (await api('GET', `/api/projects/${S.pid}/compare?ids=${ids.join(',')}`)).rows : [];
  const fields = [['kind', 'Type'], ['definition', 'What it is'], ['confidence', 'Certainty'], ['status', 'Status'], ['depends_on', 'Depends on'], ['derived_from', 'Based on'], ['supported_by', 'Supported by'], ['contradicted_by', 'Contradicted by'], ['solves', 'Solves'], ['open_questions', 'Open questions'], ['sources', 'Notes mentioning it']];
  const cell = v => Array.isArray(v) ? (v.length ? v.map(x => `<div>${esc(short(x, 80))}</div>`).join('') : '<span class="mute">—</span>') : (v === '' || v == null ? '<span class="mute">—</span>' : esc(v));
  main.innerHTML = `<div class="wrap"><div class="row" style="margin-bottom:12px"><h2 class="grow" style="margin:0">Compare</h2>
    <div class="picker" style="width:280px"><input type="text" id="cmpAdd" placeholder="Add a method, solution, concept…" autocomplete="off"><div class="ac" hidden></div></div>
    ${ids.length ? '<button class="sm" data-act="compare-clear">Clear</button>' : ''}</div>
    ${rows.length < 2 ? '<div class="panel empty">Pick two to four items to compare: tick them in Cards, or add them here.</div>' : ''}
    ${rows.length ? `<div style="overflow:auto"><table class="cmp"><tr><th></th>${rows.map(r => `<th style="width:auto">${nodeLink(r.id, r.title, r.kind)} <button class="ghost sm" data-act="compare-remove" data-id="${r.id}">✕</button></th>`).join('')}</tr>
    ${fields.map(([k, label]) => `<tr><th>${label}</th>${rows.map(r => `<td>${k === 'kind' ? kindPill(r.kind) : cell(r[k])}</td>`).join('')}</tr>`).join('')}</table></div>` : ''}</div>`;
  nodePicker($('#cmpAdd'), n => { S.selected.add(n.id); renderCompare(main); });
}

// ------------------------------------------------------------------ export
function renderExport(main) {
  const p = proj();
  main.innerHTML = `<div class="wrap">
    <div class="panel"><h2>Export ${p ? '@' + esc(p.handle) : ''}</h2>
      <p class="mute">Portable copies for sharing or archiving. Files go to your Downloads folder.</p>
      <div class="row">
        <button data-act="export" data-fmt="html" ${p ? '' : 'disabled'}>Interactive HTML atlas</button>
        <button data-act="export" data-fmt="md" ${p ? '' : 'disabled'}>Markdown (cards + Mermaid)</button>
        <button data-act="export" data-fmt="json" ${p ? '' : 'disabled'}>JSON</button>
        <button data-act="export" data-fmt="csv-nodes" ${p ? '' : 'disabled'}>CSV: concepts</button>
        <button data-act="export" data-fmt="csv-edges" ${p ? '' : 'disabled'}>CSV: links</button>
      </div></div>
    <div class="panel"><h2>Backup & import</h2>
      <div class="row"><button data-act="backup">Download full backup (all projects)</button>
      <label class="row" style="gap:6px"><span>Import JSON export or backup:</span><input type="file" id="importFile" accept=".json,application/json"></label></div>
      <p class="mute small">Your live data is <code>${esc(S.dataDir)}\\atlas.db</code>. Changes are written into that file as you go, so a copy of it is also a backup, but the download above is the safest way.</p></div>
    <div class="panel"><h2>Deeper enrichment with Claude</h2>
      <p>The built-in parser works offline with fixed rules. For an interpretive pass (better concept names, merged duplicates, inferred links, definitions), open Claude Code in the project folder and say <b>"enrich my Idea Atlas"</b>. Claude reads the notes not yet reviewed, applies changes through <code>tools/atlas_cli.py</code>, and marks them <span class="badge ai">AI-reviewed</span>. Say <b>"summarise my topics"</b> to have Claude write a short summary for each busy topic; it appears on the topic's brief (Search → Topic brief) and is flagged when newer notes arrive. Nothing is sent anywhere unless you start that session.</p></div>
  </div>`;
  $('#importFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const r = await api('POST', '/api/import', JSON.parse(await f.text()));
      await loadState(); toast(r.imported ? `Imported ${r.imported.length} project(s)` : `Imported @${r.handle}`);
    } catch (err) { fail(err); }
    e.target.value = '';
  };
}

// ------------------------------------------------------------------ projects
function renderProjects(main) {
  main.innerHTML = `<div class="wrap"><div class="row" style="margin-bottom:12px"><h2 class="grow" style="margin:0">Projects</h2><button class="primary" data-act="new-project">New project</button></div>
    ${S.projects.map(p => `<div class="panel"><div class="row">
      <span style="width:12px;height:12px;border-radius:50%;background:${esc(p.color)}"></span>
      <b>${esc(p.name)}</b><span class="code">@${esc(p.handle)}</span>${p.archived ? '<span class="badge">archived</span>' : ''}
      <span class="mute small">${p.node_count} nodes · ${p.note_count} notes${p.unreviewed ? ` · ${p.unreviewed} not AI-reviewed` : ''}</span><span class="grow"></span>
      <button class="sm" data-act="open-project" data-id="${p.id}">Open</button>
      <button class="sm" data-act="edit-project" data-id="${p.id}">Edit</button>
      <button class="sm" data-act="archive-project" data-id="${p.id}">${p.archived ? 'Unarchive' : 'Archive'}</button>
      <button class="sm danger" data-act="delete-project" data-id="${p.id}">Delete</button></div>
      ${p.description ? `<p class="mute" style="margin:8px 0 0">${esc(p.description)}</p>` : ''}</div>`).join('') || '<div class="panel empty">No projects yet.</div>'}</div>`;
}
async function projectForm(p) {
  return modal(`<form><h2>${p ? 'Edit project' : 'New project'}</h2>
    <label>Name</label><input name="name" required value="${esc(p?.name || '')}" autofocus>
    <label>Handle (used as @handle when capturing)</label><input name="handle" value="${esc(p?.handle || '')}" placeholder="auto from name" pattern="[A-Za-z][A-Za-z0-9_\\-]{0,40}">
    <label>Description</label><textarea name="description" rows="3">${esc(p?.description || '')}</textarea>
    <label>Colour</label><input name="color" type="color" value="${esc(p?.color || '#5b6ef5')}">
    <div class="row" style="justify-content:flex-end;margin-top:14px"><button type="button" data-close>Cancel</button><button class="primary">${p ? 'Save' : 'Create'}</button></div></form>`, {
    onOpen: box => { if (!p) { const n = box.querySelector('[name=name]'), h = box.querySelector('[name=handle]'); n.oninput = () => { h.placeholder = n.value.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'auto from name'; }; } },
  });
}

// ------------------------------------------------------------------ search
// ------------------------------------------------------------------ search (inside the selected project)
const FIELD_LABEL = { title: 'name', alias: 'alias', tag: 'tag', definition: 'definition', body: 'statement', excerpt: 'your notes', glossary: 'glossary', summary: "Claude's summary" };
function renderSearch(main) {
  if (!S.pid) return renderWelcome(main);
  const brief = S.searchMode === 'brief';
  main.innerHTML = `<div class="wrap" style="max-width:1140px">
    <div class="sbox"><input type="search" id="sq" placeholder="${brief ? `A topic in @${esc(proj().handle)}, e.g. a concept's name` : `Search @${esc(proj().handle)}: words, phrases, even half-typed words…`}" value="${esc(S.searchQ || '')}" autocomplete="off">
    <select id="sk" ${brief ? 'hidden' : ''}><option value="">All types</option>${S.kinds.map(k => `<option value="${k}" ${k === S.searchKind ? 'selected' : ''}>${kindName(k)}</option>`).join('')}</select>
    <button class="gloss-btn" data-act="glossary" title="Abbreviations from this project's documents. Click one to see everything your notes say about it">Aa Glossary</button></div>
    <div class="smode"><div class="seg"><button data-mode="matches" class="${brief ? '' : 'on'}" title="Every item that matches, best first">Matches</button><button data-mode="brief" class="${brief ? 'on' : ''}" title="Everything your notes say about one topic, on one page">Topic brief</button></div>
    <div class="smeta" id="smeta"></div></div><div id="sres"></div></div>`;
  const inp = $('#sq');
  inp.oninput = () => { S.searchQ = inp.value; clearTimeout(renderSearch.t); renderSearch.t = setTimeout(() => doSearch().catch(fail), S.searchMode === 'brief' ? 350 : 160); };
  $('#sk').onchange = e => { S.searchKind = e.target.value; doSearch().catch(fail); };
  $$('.seg [data-mode]', main).forEach(b => b.onclick = () => setSearchMode(b.dataset.mode));
  inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
  return doSearch();
}
function setSearchMode(mode, q) {
  S.searchMode = mode;
  if (q !== undefined) S.searchQ = q;
  try { localStorage.setItem('atlas.smode', mode); } catch (_) { /* storage blocked */ }
  closeDrawer();
  if (S.view === 'search') return renderSearch($('#main'));
  go('search');
}
let searchSeq = 0;
async function doSearch() {
  if (S.searchMode === 'brief') return doBrief();
  const q = (S.searchQ || '').trim(), box = $('#sres'), meta = $('#smeta');
  if (!box) return;
  if (!q) {
    meta.textContent = '';
    box.innerHTML = `<div class="empty">Search everything in @${esc(proj().handle)}: names, definitions, statements and the words of your notes.<br>
      Results update as you type and tolerate typos, partial words, aliases (GNN ↔ graph neural network) and terms that keep appearing together in your notes.</div>`;
    return;
  }
  const seq = ++searchSeq;
  const qs = new URLSearchParams({ q });
  if (S.searchKind) qs.set('kind', S.searchKind);
  const r = await api('GET', `/api/projects/${S.pid}/search?${qs}`);
  if (seq !== searchSeq) return;   // a newer keystroke already started another search
  const exact = r.results.filter(x => x.match === 'exact'), related = r.results.filter(x => x.match === 'related');
  meta.innerHTML = `${r.results.length} item${r.results.length === 1 ? '' : 's'}`
    + (r.texts.length ? ` · ${r.texts.length} in the full text of your notes` : '')
    + (r.did_you_mean ? ` · Did you mean <a data-dym="${esc(r.did_you_mean)}">${esc(r.did_you_mean)}</a>?` : '')
    + (r.glossary ? ` · <b>${esc(r.glossary.abbr)}</b> = ${esc(r.glossary.expansion)} (glossary), both forms searched` : '')
    + (r.expanded.length ? ` · also matching terms that go with it in your notes: <i>${r.expanded.map(esc).join(', ')}</i>` : '');
  if (!r.results.length && !r.texts.length) { box.innerHTML = `<div class="empty">No matches for “${esc(q)}” in @${esc(proj().handle)}.</div>`; return; }
  const snip = segs => segs.map(s => s.m === 1 ? `<mark>${esc(s.t)}</mark>` : s.m === 2 ? `<mark class="rel">${esc(s.t)}</mark>` : esc(s.t)).join('');
  const TEXT_LABEL = { note: 'note', summary: "Claude's summary", glossary: 'glossary' };
  const textHit = x => `<div class="hit"><div class="crumb"><span class="badge">${TEXT_LABEL[x.type]}</span></div>
    <a class="ht" ${x.type === 'note' ? `data-act="goto-note" data-id="${x.id}"` : `data-act="brief-of" data-q="${esc(x.topic)}"`} style="cursor:pointer">${esc(x.type === 'note' ? `Note of ${day(x.created)}` : x.label)}</a>
    <div class="sn">${snip(x.snippet)}</div></div>`;
  if (!r.results.length) { box.innerHTML = `<h3>In your notes</h3>${r.texts.map(textHit).join('')}`; return; }
  const hit = x => `<div class="hit"><div class="crumb">${kindPill(x.node.kind)}
      ${x.node.status === 'resolved' ? '<span class="badge">resolved</span>' : ''}${x.node.confidence === 'low' ? '<span class="badge hyp">uncertain</span>' : ''}
      <span>matched in ${x.fields.map(f => FIELD_LABEL[f] || f).join(', ') || 'linked terms'}</span></div>
    <a class="ht" data-node="${x.node.id}">${esc(x.node.title)}</a><div class="sn">${snip(x.snippet)}</div></div>`;
  const top = r.results[0];
  box.innerHTML = `<div class="sgrid"><div>
      ${exact.map(hit).join('')}
      ${r.texts.length ? `<h3>In your notes</h3>${r.texts.map(textHit).join('')}` : ''}
      ${related.length ? `<h3>Related by meaning in your notes</h3>${related.map(hit).join('')}` : ''}
    </div><aside>
      <div class="featured"><div class="small mute">Top result</div><div style="margin:4px 0">${nodeLink(top.node.id, top.node.title, top.node.kind)}</div>
        ${top.node.definition ? `<p class="small">${esc(top.node.definition)}</p>` : ''}
        ${r.path && r.path.length ? `<div class="small mute" style="margin-top:8px">How it connects to the rest of your map</div>${pathHtml(r.path)}` : ''}
        <div style="margin-top:10px"><a class="small" data-act="brief-of" data-q="${esc(q)}" style="cursor:pointer">Everything about “${esc(short(q, 40))}” on one page →</a></div></div>
      ${r.connected.length ? `<div class="featured"><div class="small mute">Also connected to these results</div><ul class="links">${r.connected.map(c => `<li>${nodeLink(c.node.id, c.node.title, c.node.kind)}</li>`).join('')}</ul></div>` : ''}
    </aside></div>`;
}
// ------------------------------------------------------------------ topic brief (everything about one topic, one page)
const markSegs = segs => segs.map(s => s.m ? `<mark>${esc(s.t)}</mark>` : esc(s.t)).join('');
const day = ts => ts ? new Date(ts * 1000).toLocaleDateString([], { dateStyle: 'medium' }) : '';
async function doBrief() {
  const q = (S.searchQ || '').trim(), box = $('#sres'), meta = $('#smeta');
  if (!box) return;
  const handle = esc(proj().handle);
  if (!q) {
    meta.textContent = '';
    box.innerHTML = `<div class="empty">Type a topic, for example a concept's name.<br>The brief gathers everything @${handle} says about it on one page: your definitions, every statement,
      question and decision that uses it, how it connects to other topics, and the passages from each note, oldest first. Nothing is reworded.</div>`;
    return;
  }
  const seq = ++searchSeq;
  const b = await api('GET', `/api/projects/${S.pid}/brief?${new URLSearchParams({ q })}`);
  if (seq !== searchSeq) return;
  const st = b.stats, t = b.topic, g = b.glossary, name = g ? g.abbr : t ? t.title : (b.corrected || b.query);
  if (!st.notes && !st.items && !t && !g) {
    meta.textContent = '';
    box.innerHTML = `<div class="empty">Nothing in @${handle} talks about “${esc(q)}” yet.</div>`;
    return;
  }
  const span = st.first ? (day(st.first) === day(st.last) ? day(st.first) : `${day(st.first)} – ${day(st.last)}`) : '';
  meta.innerHTML = [`${st.notes} note${st.notes === 1 ? '' : 's'}`, `${st.items} item${st.items === 1 ? '' : 's'}`, span].filter(Boolean).join(' · ')
    + (b.corrected ? ` · showing “${esc(b.corrected)}”, the closest spelling in your notes` : '');

  const relTxt = r => (r.dir === 'out' ? REL_OUT[r.type] : REL_IN[r.type]) || r.type;
  const rels = i => i.relations.map(r => `<span class="badge${r.status === 'suggested' ? ' sugg' : ''}${r.type === 'contradicts' ? ' bad' : ''}">${esc(relTxt(r))} ${esc(short(name, 32))}${r.status === 'suggested' ? '?' : ''}</span>`).join('');
  const item = i => `<div class="hit"><div class="crumb">${kindPill(i.kind)}<span>${day(i.first_seen)}</span>
      ${i.status === 'resolved' ? '<span class="badge">resolved</span>' : ''}${i.confidence === 'low' ? '<span class="badge hyp">uncertain</span>' : ''}${i.origin === 'ai' ? '<span class="badge ai">added by Claude</span>' : ''}${rels(i)}</div>
    <a class="ht" data-node="${i.id}">${esc(i.title)}</a>${i.body && i.body !== i.title ? `<div class="sn">${esc(i.body)}</div>` : ''}</div>`;
  const defs = b.definitions.length ? b.definitions.map(d => `<div class="quote">${esc(d.text)}<div class="src">${{ parser: 'from your notes', ai: 'added by Claude', user: 'added by you' }[d.origin] || ''}${d.created ? ' · ' + day(d.created) : ''}</div></div>`).join('')
    : t && t.definition ? `<p class="what">${esc(t.definition)}</p>` : '';
  const sm = b.summary;
  const summary = sm ? `<div class="featured summary"><div class="row small mute" style="gap:8px"><span class="badge ai">Claude's summary</span><span>${day(sm.created)} · an interpretation of your notes, not your words</span>
      ${sm.newer_notes ? `<span class="badge sugg">${sm.newer_notes} newer note${sm.newer_notes === 1 ? '' : 's'} since</span>` : ''}</div><div class="sumtext">${esc(sm.text)}</div></div>`
    : `<div class="featured small mute">No written summary yet. Next time you open Claude Code in the Idea Atlas folder, ask it to <b>summarise my topics</b>. The summary appears here, marked as Claude's interpretation.</div>`;
  const why = x => [...x.relations.map(r => `${relTxt(r).toLowerCase()} this`), x.named ? 'similar name' : '', x.shared ? `in ${x.shared} shared statement${x.shared === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');

  box.innerHTML = `<div class="sgrid brief"><div>
      <div class="bhead"><div class="row" style="gap:8px">${t ? kindPill(t.kind) : g ? '<span class="badge">abbreviation</span>' : '<span class="badge">keyword</span>'}<span class="grow"></span>
        ${g ? '<button class="sm" data-act="glossary">Glossary</button>' : ''}
        ${t ? `<button class="sm" data-node="${t.id}">Open item</button><button class="sm" data-act="brief-map" data-id="${t.id}" data-title="${esc(t.title)}">Show on map</button>` : ''}
        <button class="sm" data-act="brief-md">Save as Markdown</button></div>
        <div class="card-title">${esc(name)}${g ? `<span class="gl-exp">${esc(g.expansion)}</span>` : ''}</div>
        ${g ? `<div class="gl-banner">${esc(GLOSS_KIND[g.kind][1])}${g.page ? `, p.${g.page}` : ''}${g.context ? ` · ${esc(g.context)}` : ''}</div>` : ''}
        ${t && t.aliases.length ? `<div class="small mute">Also called ${t.aliases.map(esc).join(', ')}</div>` : ''}
        ${defs ? `<h3>What it is</h3>${defs}` : ''}</div>
      ${summary}
      ${!st.notes && !st.items ? `<p class="mute">Not in your notes yet.</p>` : ''}
      ${b.groups.map(g => `<h3>${esc(g.label)} <span class="mute">${g.items.length}</span></h3>${g.items.map(item).join('')}`).join('')}
      ${b.timeline.length ? `<h3>In your notes, oldest first <span class="mute">${b.timeline.length}</span></h3>${b.timeline.map(e => `<div class="note">
        <div class="row"><b class="small">${new Date(e.note.created * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}</b>
          ${e.note.source ? `<span class="badge" title="${esc(e.note.source)}">📎 ${esc(short(e.note.source.split(/[\\/]/).pop(), 60))}</span>` : ''}</div>
        ${e.passages.map(p => `<div class="bpass">${markSegs(p)}</div>`).join('')}
        ${e.more ? `<div class="small mute">… and ${e.more} more passage${e.more === 1 ? '' : 's'} in this note</div>` : ''}
        <details class="more"><summary class="small">Whole note</summary><pre>${esc(e.text)}</pre></details></div>`).join('')}` : ''}
    </div><aside>
      ${b.conflicts.length ? `<div class="featured"><div class="small mute">Unresolved conflicts</div><ul class="links">${b.conflicts.map(c => `<li>${nodeLink(c.a.id, c.a.title, c.a.kind)}<span class="badge bad">contradicts</span>${nodeLink(c.b.id, c.b.title, c.b.kind)}</li>`).join('')}</ul></div>` : ''}
      ${b.topics.length ? `<div class="featured"><div class="small mute">Connected topics</div><ul class="links">${b.topics.map(x => `<li>${nodeLink(x.id, x.title, x.kind)}
        <button class="ghost sm" data-act="brief-of" data-q="${esc(x.title)}" title="Topic brief for ${esc(x.title)}">Brief</button><span class="why">${esc(why(x))}</span></li>`).join('')}</ul></div>` : ''}
      ${b.sources.length ? `<div class="featured"><div class="small mute">Sources</div><ul class="links">${b.sources.map(s => `<li><span class="nodelink" data-node="${s.id}">📄 <span class="t">${esc(short(s.title, 50))}</span></span>
        ${isLocation(s.body) ? `<button class="ghost sm" data-act="ref-open-id" data-id="${s.id}">Open</button>` : ''}</li>`).join('')}</ul></div>` : ''}
    </aside></div>`;
}

// ------------------------------------------------------------------ glossary (abbreviations from project documents)
const GLOSS_KIND = {
  list: ['', "From the proposal's list of abbreviations"],
  text: ['', 'Spelled out in the proposal text'],
  standard: ['standard meaning', 'Used in the proposal without being spelled out; this is the standard meaning'],
  partner: ['partner', 'Consortium partner'],
};
async function openGlossary() {
  if (!S.pid) return;
  const { entries } = await api('GET', `/api/projects/${S.pid}/glossary`);
  const p = proj(), used = entries.filter(e => e.notes).length;
  const src = [...new Set(entries.map(e => e.source).filter(Boolean))].map(s => s.split(/[\\/]/).pop());
  const row = e => `<button type="button" class="gl-row${e.notes ? '' : ' unused'}" data-abbr="${esc(e.abbr)}" data-a="${esc(e.abbr.toLowerCase())}" data-f="${esc(`${e.abbr} ${e.expansion} ${e.context}`.toLowerCase())}" data-n="${e.notes}">
      <span class="gl-a">${esc(e.abbr)}</span>
      <span class="gl-x"><span>${esc(e.expansion)}</span>${GLOSS_KIND[e.kind][0] ? `<span class="badge" title="${esc(GLOSS_KIND[e.kind][1])}">${GLOSS_KIND[e.kind][0]}</span>` : ''}${e.context ? `<span class="gl-c">${esc(e.context)}</span>` : ''}</span>
      <span class="gl-n">${e.notes ? `${e.notes} note${e.notes === 1 ? '' : 's'}` : 'not in notes'}${e.summary ? `<span class="badge ai" title="${e.summary_stale ? "Claude's summary; newer notes since" : "Claude's summary available"}">summary${e.summary_stale ? ' · stale' : ''}</span>` : ''}</span></button>`;
  const html = entries.length
    ? `<form class="gloss"><div class="row"><h2 class="grow" style="margin:0">Glossary · @${esc(p.handle)}</h2>
        <button type="button" class="icon-btn" data-close title="Close (Esc)"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
      <p class="small mute" style="margin:4px 0 10px">${entries.length} abbreviations${src.length ? ` from ${src.map(esc).join(', ')}` : ''}. Click one to see everything your notes say about it.</p>
      <div class="row"><input type="search" id="glf" class="grow" placeholder="Filter by abbreviation or meaning…" autocomplete="off" autofocus>
        <label class="gl-only"><input type="checkbox" id="glu"> In my notes (${used})</label></div>
      <input type="hidden" name="abbr">
      <div class="gl-list">${entries.map(row).join('')}<div class="empty small" id="glnone" hidden>No abbreviation matches.</div></div></form>`
    : `<form><h2>Glossary · @${esc(p.handle)}</h2><p>No glossary for this project yet. In Claude Code, ask Claude to build one from a project document such as the proposal, and it will appear here.</p>
      <div class="row" style="justify-content:flex-end"><button type="button" data-close>Close</button></div></form>`;
  const r = await modal(html, {
    onOpen: box => {
      box.classList.add('wide');
      const f = box.querySelector('#glf'); if (!f) return;
      const only = box.querySelector('#glu'), rows = [...box.querySelectorAll('.gl-row')];
      const apply = () => {
        const q = f.value.trim().toLowerCase(); let n = 0;
        rows.forEach(el => {
          const show = (!q || el.dataset.f.includes(q)) && (!only.checked || +el.dataset.n > 0);
          el.hidden = !show; n += show;
          el.style.order = q && el.dataset.a === q ? 0 : q && el.dataset.a.startsWith(q) ? 1 : 2;   // abbreviation hits first
        });
        box.querySelector('#glnone').hidden = n > 0;
      };
      f.oninput = apply; only.onchange = apply;
      const pick = abbr => { box.querySelector('[name=abbr]').value = abbr; box.querySelector('form').requestSubmit(); };
      rows.forEach(el => { el.onclick = () => pick(el.dataset.abbr); });
      f.onkeydown = e => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const first = rows.filter(el => !el.hidden).sort((a, b) => a.style.order - b.style.order)[0];
        if (first) pick(first.dataset.abbr);
      };
    },
  });
  $('#modalBox').classList.remove('wide');
  if (r && r.abbr) setSearchMode('brief', r.abbr);
}

function pathHtml(path) {
  return `<div class="path">${path.map((s, i) => `${i === 0 ? `<span class="chip" data-node="${s.from.id}">${esc(short(s.from.title, 40))}</span>` : ''}
    <span class="arrow">${esc(s.dir === 'out' ? s.type : REL_IN[s.type]?.toLowerCase() || s.type)}</span>
    <span class="chip" data-node="${s.to.id}">${esc(short(s.to.title, 40))}</span>`).join('')}</div>`;
}

// ------------------------------------------------------------------ item panel (right-hand sheet)
async function openCard(id, keepScroll = false) {
  if (!id) return;
  let c;
  try { c = await api('GET', `/api/nodes/${id}`); } catch (e) { return fail(e); }
  if (S.drawerId !== id) { S.showAllWords = false; S.editLinks = false; }
  S.drawerId = id;
  const n = c.node, body = $('#drawerBody');
  const scroll = keepScroll ? body.scrollTop : 0;
  $('#drawerCode').textContent = c.project.name;

  // --- group connections into plain sentences from this item's point of view
  const groups = {}, suggested = [], sources = [];
  const add = (label, e, other) => (groups[label] = groups[label] || []).push({ e, other });
  for (const e of c.out) {
    const other = { id: e.dst, title: e.dst_title, kind: e.dst_kind };
    if (e.status === 'suggested') suggested.push({ e, other, phrase: REL_OUT[e.type] });
    else if (e.type === 'derived from' && e.dst_kind === 'reference') sources.push({ e, other });
    else add(REL_OUT[e.type] || e.type, e, other);
  }
  for (const e of c.in) {
    const other = { id: e.src, title: e.src_title, kind: e.src_kind };
    if (e.status === 'suggested') suggested.push({ e, other, phrase: REL_IN[e.type] });
    else add(REL_IN[e.type] || e.type, e, other);
  }
  const seen = new Set();   // "Related to" can appear in both directions; show each item once
  if (groups['Related to']) groups['Related to'] = groups['Related to'].filter(x => !seen.has(x.other.id) && seen.add(x.other.id));
  const labels = Object.keys(groups).sort((a, b) => (REL_ORDER.indexOf(a) + 99) % 99 - (REL_ORDER.indexOf(b) + 99) % 99);

  const row = ({ e, other }) => `<li class="crow ${e.type === 'contradicts' && e.status === 'active' ? 'conflict' : ''}">
      ${nodeLink(other.id, other.title, other.kind)}
      ${e.status === 'resolved' ? '<span class="badge">resolved</span>' : ''}${e.confidence === 'low' ? '<span class="badge hyp" title="Based on hedged wording or a weak signal">uncertain</span>' : ''}
      ${e.type === 'contradicts' && e.status === 'active' && !S.editLinks ? `<button class="sm" data-act="edge-resolve" data-id="${e.id}">Mark resolved</button>` : ''}
      ${S.editLinks ? `<span class="grow"></span><select class="sm" data-edge-type="${e.id}" title="Change how they relate">${S.edgeTypes.map(t => `<option ${t === e.type ? 'selected' : ''}>${t}</option>`).join('')}</select>
        <button class="ghost sm" data-act="edge-delete" data-id="${e.id}" title="Remove this connection">✕</button>` : ''}</li>`;

  const isLoc = n.kind === 'reference' && isLocation(n.body);
  const words = c.mentions.filter(m => m.excerpt && m.excerpt !== n.body);
  const shownWords = S.showAllWords ? words : words.slice(0, 3);
  const what = n.definition || (n.body && n.body !== n.title && n.kind !== 'reference' ? n.body : '');
  const resolvable = ['question', 'task', 'risk', 'assumption'].includes(n.kind);
  const originLine = { parser: 'Picked up automatically from your notes', ai: 'Added by Claude during a review', user: 'Added by you' }[n.origin] || '';
  const noteCount = new Set(c.mentions.map(m => m.note_id)).size;

  body.innerHTML = `
    <div class="sheet-head">
      <div class="row" style="gap:8px">${kindPill(n.kind)}
        ${n.status === 'resolved' ? '<span class="badge">resolved</span>' : resolvable ? '<span class="badge sugg">open</span>' : ''}
        ${n.confidence === 'low' ? '<span class="badge hyp" title="Hedged wording or weak signal">uncertain</span>' : ''}</div>
      <div class="card-title">${esc(n.title)}</div>
      <div class="small mute">${[noteCount ? `Mentioned in ${noteCount} note${noteCount === 1 ? '' : 's'}` : '', originLine].filter(Boolean).join(' · ')}</div>
      <div class="row sheet-actions">
        ${resolvable ? `<button class="sm ${n.status === 'resolved' ? '' : 'primary'}" data-act="node-resolve">${n.status === 'resolved' ? 'Reopen' : 'Mark resolved'}</button>` : ''}
        ${n.kind === 'question' && n.status !== 'resolved' ? '<button class="sm" data-act="node-answer">Write answer</button>' : ''}
        ${['concept', 'method'].includes(n.kind) ? '<button class="sm" data-act="node-brief" title="Everything your notes say about it, on one page">Topic brief</button>' : ''}
        <button class="sm" data-act="node-focus">Show on map</button>
        <button class="sm" data-act="node-edit">Edit</button>
        <details class="menu"><summary class="sm-btn" title="More actions">More</summary><div class="menu-list">
          <button data-act="node-compare">${S.selected.has(n.id) ? 'Remove from compare' : 'Add to compare'}</button>
          <button data-act="node-merge">Merge into another item…</button>
          <button data-act="toggle-edit-links">${S.editLinks ? 'Done editing connections' : 'Edit connections'}</button>
          <button class="danger" data-act="node-delete">Delete item</button></div></details>
      </div>
    </div>

    ${isLoc ? `<section class="sec"><div class="filebox"><div class="fname">📄 ${esc(n.title)}</div><div class="loc">${esc(n.body)}</div>
      <div class="row" style="gap:6px;margin-top:8px"><button class="sm primary" data-act="ref-open">Open</button>
      ${/^https?:/i.test(n.body) ? '' : '<button class="sm" data-act="ref-reveal">Show in folder</button>'}</div></div></section>` : ''}

    ${n.kind !== 'reference' ? `<section class="sec"><h3>What it is</h3>${what ? `<p class="what">${esc(what)}</p>` : '<p class="mute small">No description yet. <a data-act="node-edit">Add one</a>, or write “X is …” in a note.</p>'}
      ${c.definitions.length > 1 ? `<details class="more"><summary>${c.definitions.length} descriptions recorded</summary><ul class="links">${c.definitions.map(d => `<li>${esc(d.text)}${d.text === n.definition ? '<span class="badge">in use</span>' : `<button class="sm" data-act="use-def" data-text="${esc(d.text)}">Use this</button>`}</li>`).join('')}</ul></details>` : ''}</section>` : ''}

    ${suggested.length ? `<section class="sec"><h3>Suggested connections <span class="mute">· your call</span></h3>${suggested.map(({ e, other, phrase }) => `
      <div class="suggest"><div><span class="mute small">${esc(phrase)}</span> ${nodeLink(other.id, other.title, other.kind)}</div>
        ${e.evidence ? `<div class="why">${esc(e.evidence)}</div>` : ''}
        <div class="row" style="gap:6px"><button class="sm primary" data-act="edge-accept" data-id="${e.id}">Accept</button><button class="sm" data-act="edge-reject" data-id="${e.id}">Dismiss</button></div></div>`).join('')}</section>` : ''}

    <section class="sec"><div class="row"><h3 class="grow">Connections</h3>${S.editLinks ? '<button class="sm" data-act="toggle-edit-links">Done</button>' : ''}</div>
      ${labels.length ? labels.map(l => `<div class="cgroup"><div class="clabel">${esc(l)}</div><ul class="clist">${groups[l].map(row).join('')}</ul></div>`).join('')
        : '<p class="mute small">Not connected to anything yet.</p>'}
      ${S.editLinks ? `<div class="addlink"><div class="clabel">Add a connection</div><div class="row"><select id="newLinkType">${S.edgeTypes.map(t => `<option>${t}</option>`).join('')}</select>
        <div class="picker grow"><input type="text" id="newLinkTarget" placeholder="Find an item…" autocomplete="off"><div class="ac" hidden></div></div></div></div>` : ''}
    </section>

    ${n.kind !== 'reference' ? `<section class="sec"><h3>From your notes</h3>
      ${shownWords.map(m => `<div class="quote">${esc(m.excerpt)}<div class="src">${new Date(m.created * 1000).toLocaleDateString([], { dateStyle: 'medium' })}</div></div>`).join('') || '<p class="mute small">Not written in a note (added while editing or reviewing).</p>'}
      ${words.length > 3 ? `<button class="ghost sm" data-act="toggle-words">${S.showAllWords ? 'Show fewer' : `Show all ${words.length}`}</button>` : ''}</section>` : ''}

    ${sources.length ? `<section class="sec"><h3>Sources</h3>${sources.map(({ other }) => `<div class="srcrow"><span class="nodelink" data-node="${other.id}">📄 <span class="t">${esc(short(other.title, 60))}</span></span>
      <span class="grow"></span><button class="sm" data-act="ref-open-id" data-id="${other.id}">Open</button></div>`).join('')}</section>` : ''}

    <details class="more sec"><summary>Names, tags and certainty</summary>
      <div class="kv" style="margin-top:10px">
        <span class="small mute">Also known as</span><div>${c.aliases.filter(a => a !== n.title).map(a => `<span class="chip">${esc(a)}<span data-act="alias-remove" data-alias="${esc(a)}" title="Remove">✕</span></span>`).join('')}
          <input type="text" id="aliasAdd" placeholder="+ add a name" style="width:140px;padding:3px 9px"></div>
        <span class="small mute">Tags</span><div>${c.tags.map(t => `<span class="chip">#${esc(t)}<span data-act="tag-remove" data-tag="${esc(t)}" title="Remove">✕</span></span>`).join('')}
          <input type="text" id="tagAdd" placeholder="+ tag" style="width:100px;padding:3px 9px"></div>
        <span class="small mute">Certainty</span><select id="confSel">${[['high', 'Certain'], ['medium', 'Normal'], ['low', 'Uncertain']].map(([v, l]) => `<option value="${v}" ${v === n.confidence ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <span class="small mute">Status</span><select id="statusSel">${[['open', 'Open'], ['active', 'Active'], ['resolved', 'Resolved']].map(([v, l]) => `<option value="${v}" ${v === n.status ? 'selected' : ''}>${l}</option>`).join('')}</select>
      </div>
      <p class="small mute" style="margin-top:12px">Connections are worked out from your wording. If one is wrong, use More → Edit connections.</p>
    </details>`;
  body.scrollTop = scroll;
  $('#drawer').classList.add('open');
  $('#drawer').setAttribute('aria-hidden', 'false');
  const patch = async f => { try { await api('PATCH', `/api/nodes/${id}`, f); S.nodes = null; openCard(id, true); } catch (e) { fail(e); } };
  $('#aliasAdd').onkeydown = e => { if (e.key === 'Enter' && e.target.value.trim()) patch({ add_aliases: [e.target.value.trim()] }); };
  $('#tagAdd').onkeydown = e => { if (e.key === 'Enter' && e.target.value.trim()) patch({ add_tags: [e.target.value.trim()] }); };
  $('#confSel').onchange = e => patch({ confidence: e.target.value });
  $('#statusSel').onchange = e => patch({ status: e.target.value });
  $$('[data-edge-type]').forEach(s => s.onchange = async () => { try { await api('PATCH', `/api/edges/${s.dataset.edgeType}`, { type: s.value }); openCard(id, true); } catch (e) { fail(e); } });
  if ($('#newLinkTarget')) nodePicker($('#newLinkTarget'), async t => {
    try { await api('POST', '/api/edges', { src: id, dst: t.id, type: $('#newLinkType').value }); toast('Connected'); openCard(id, true); } catch (e) { fail(e); }
  }, c.project.id);
}
function closeDrawer() { $('#drawer').classList.remove('open'); $('#drawer').setAttribute('aria-hidden', 'true'); S.drawerId = null; }

async function nodeAction(act, el) {
  const id = S.drawerId;
  const card = await api('GET', `/api/nodes/${id}`);
  const n = card.node;
  if (act === 'node-edit') {
    const r = await modal(`<form><h2>Edit</h2>
      <label>Name</label><input name="title" value="${esc(n.title)}" required autofocus>
      <label>Type</label><select name="kind">${S.kinds.map(k => `<option value="${k}" ${k === n.kind ? 'selected' : ''}>${kindName(k)}</option>`).join('')}</select>
      <label>What it is (definition)</label><textarea name="definition" rows="3">${esc(n.definition)}</textarea>
      <label>Full statement</label><textarea name="body" rows="3">${esc(n.body)}</textarea>
      <div class="row" style="justify-content:flex-end;margin-top:14px"><button type="button" data-close>Cancel</button><button class="primary">Save</button></div></form>`);
    if (r) { await api('PATCH', `/api/nodes/${id}`, r); S.nodes = null; openCard(id, true); render(); }
  } else if (act === 'node-brief') {
    setSearchMode('brief', n.title);
  } else if (act === 'node-focus') {
    Object.assign(S.map, { focus: id, focusLabel: n.title }); closeDrawer(); go('map');
  } else if (act === 'node-answer') {
    S.captureTarget = { id, title: n.title, mode: 'answer' };
    S.lastCapture = null; closeDrawer(); go('capture');
  } else if (act === 'node-resolve') {
    await api('PATCH', `/api/nodes/${id}`, { status: n.status === 'resolved' ? 'open' : 'resolved' }); openCard(id, true); render();
  } else if (act === 'node-compare') {
    S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id); openCard(id, true); if (['cards', 'compare'].includes(S.view)) render();
  } else if (act === 'node-merge') {
    const r = await modal(`<form><h2>Merge into another item</h2><p class="mute small">“${esc(short(n.title, 80))}” will be folded into the item you pick. Its other names, notes and connections move over.</p>
      <div class="picker"><input type="text" id="mergeTarget" placeholder="Search target…" autocomplete="off"><div class="ac" hidden></div></div>
      <input type="hidden" name="into"><p id="mergeChosen" class="small"></p>
      <div class="row" style="justify-content:flex-end;margin-top:14px"><button type="button" data-close>Cancel</button><button class="primary">Merge</button></div></form>`,
      { onOpen: box => nodePicker(box.querySelector('#mergeTarget'), t => { box.querySelector('[name=into]').value = t.id; box.querySelector('#mergeChosen').textContent = `Merging into: ${t.title}`; }, card.project.id) });
    if (r && r.into) { await api('POST', `/api/nodes/${id}/merge`, { into: +r.into }); S.nodes = null; toast('Merged'); openCard(+r.into); render(); }
  } else if (act === 'node-delete') {
    if (await confirmBox(`Delete “<b>${esc(short(n.title, 80))}</b>” and its connections? Your notes are kept.`, 'Delete', true)) {
      await api('DELETE', `/api/nodes/${id}`); S.nodes = null; S.selected.delete(id); closeDrawer(); render();
    }
  }
}

// ------------------------------------------------------------------ node picker
function nodePicker(input, onPick, pid) {
  const ac = input.parentElement.querySelector('.ac');
  let items = [], idx = 0;
  const show = async () => {
    const all = pid && pid !== S.pid ? (await api('GET', `/api/projects/${pid}/nodes?sort=title`)).nodes : await getNodes();
    const f = input.value.toLowerCase().trim();
    items = all.filter(n => !f || n.title.toLowerCase().includes(f)).slice(0, 30);
    idx = 0;
    ac.innerHTML = items.map((n, i) => `<div data-i="${i}" class="${i === 0 ? 'on' : ''}"><i class="dot ${esc(n.kind)}"></i>${esc(short(n.title, 70))} <span class="small mute">${esc(kindName(n.kind))}</span></div>`).join('') || '<div class="mute">No match</div>';
    ac.hidden = false;
  };
  input.addEventListener('focus', show);
  input.addEventListener('input', show);
  input.addEventListener('blur', () => setTimeout(() => { ac.hidden = true; }, 150));
  input.addEventListener('keydown', e => {
    if (ac.hidden) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); idx = (idx + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; $$('div', ac).forEach((d, i) => d.classList.toggle('on', i === idx)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (items[idx]) { ac.hidden = true; input.value = items[idx].title; onPick(items[idx]); } }
    else if (e.key === 'Escape') { e.stopPropagation(); ac.hidden = true; }
  });
  ac.addEventListener('mousedown', e => { const d = e.target.closest('[data-i]'); if (!d) return; e.preventDefault(); const n = items[+d.dataset.i]; ac.hidden = true; input.value = n.title; onPick(n); });
}

// ------------------------------------------------------------------ global events
document.addEventListener('click', async e => {
  const t = e.target;
  const nodeEl = t.closest('[data-node]');
  const actEl = t.closest('[data-act]');
  if (t.dataset.dym) { S.searchQ = t.dataset.dym; $('#sq').value = S.searchQ; return doSearch().catch(fail); }
  $$('details.menu[open]').forEach(d => { if (!d.contains(t) || actEl) d.open = false; });   // close the More menu
  try {
    if (actEl) {
      const act = actEl.dataset.act, id = +actEl.dataset.id;
      if (act === 'close-drawer') return closeDrawer();
      if (act === 'capture') return doCapture(false);
      if (act === 'preview') return doCapture(true);
      if (act === 'insert-wiki') return insertConceptLink();
      if (act === 'glossary') return openGlossary();
      if (act === 'issue') return issueAction(actEl);
      if (act === 'req-brief') return setSearchMode('brief', actEl.dataset.q);
      if (act === 'req-log') return reqHistory(actEl.dataset.rid);
      if (act === 'req-csv') return reqCsv();
      if (act === 'req-filter') { S.trace.status = S.trace.status === actEl.dataset.status ? '' : actEl.dataset.status; return render(); }
      if (act === 'quest') return questAction(actEl);
      if (act.startsWith('node-')) return nodeAction(act, actEl);
      if (act === 'new-project') {
        const r = await projectForm(null); if (!r) return;
        const p = await api('POST', '/api/projects', r); await loadState(); setProject(p.id); toast(`Created @${p.handle}`); return;
      }
      if (act === 'edit-project') {
        const r = await projectForm(S.projects.find(p => p.id === id)); if (!r) return;
        if (!r.handle) delete r.handle;
        await api('PATCH', `/api/projects/${id}`, r); await loadState(); return render();
      }
      if (act === 'open-project') { setProject(id); return go('cards'); }
      if (act === 'archive-project') { const p = S.projects.find(x => x.id === id); await api('PATCH', `/api/projects/${id}`, { archived: p.archived ? 0 : 1 }); await loadState(); return render(); }
      if (act === 'delete-project') {
        const p = S.projects.find(x => x.id === id);
        const r = await modal(`<form><h2>Delete @${esc(p.handle)}?</h2><p>This permanently removes ${p.note_count} notes and ${p.node_count} map items. Export first if unsure.</p>
          <label>Type <b>${esc(p.handle)}</b> to confirm</label><input name="h" autofocus autocomplete="off">
          <div class="row" style="justify-content:flex-end;margin-top:14px"><button type="button" data-close>Cancel</button><button class="danger">Delete</button></div></form>`);
        if (!r || r.h !== p.handle) return r && toast('Handle did not match; nothing deleted', true);
        await api('DELETE', `/api/projects/${id}`); S.pid = null; await loadState(); return render();
      }
      if (act === 'undo-note' || act === 'note-delete') {
        if (act === 'note-delete' && !(await confirmBox('Delete this note? Items that only this note mentioned are removed too.', 'Delete', true))) return;
        const r = await api('DELETE', `/api/notes/${id}`);
        toast(`Note removed${r.removed_nodes.length ? `, along with ${r.removed_nodes.length} item${r.removed_nodes.length === 1 ? '' : 's'} only it mentioned` : ''}`);
        if (act === 'undo-note') S.lastCapture = null;
        S.nodes = null; await loadState(); return render();
      }
      if (act === 'note-reuse') {
        const n = ($('#main')._notes || []).find(x => x.id === id); if (!n) return;
        try { localStorage.setItem('atlas.draft', n.text); } catch (_) { /* ignore */ }
        S.lastCapture = null; return go('capture');
      }
      if (act === 'brief-of') return setSearchMode('brief', actEl.dataset.q);
      if (act === 'goto-note') { S.focusNote = id; return go('notes'); }
      if (act === 'brief-md') return download(`/api/projects/${S.pid}/brief?${new URLSearchParams({ q: S.searchQ || '', fmt: 'md' })}`);
      if (act === 'brief-map') { Object.assign(S.map, { focus: id, focusLabel: actEl.dataset.title }); return go('map'); }
      if (act === 'map-clear') { Object.assign(S.map, { focus: null, focusLabel: '' }); return render(); }
      if (act === 'zoom-fit') { S.map.autoFit = true; return fitMap(); }
      if (act === 'zoom') { S.map.autoFit = false; S.map.zoom = Math.max(.3, Math.min(2.5, S.map.zoom + (+actEl.dataset.z) * .15)); $('#zoomLbl').textContent = Math.round(S.map.zoom * 100) + '%'; return applyZoom($('#mapBox'), S.map.zoom); }
      if (act === 'compare-selected') return go('compare');
      if (act === 'compare-remove') { S.selected.delete(id); return render(); }
      if (act === 'compare-clear') { S.selected.clear(); return render(); }
      if (act === 'export') return download(`/api/projects/${S.pid}/export?fmt=${actEl.dataset.fmt}`);
      if (act === 'backup') return download('/api/backup');
      if (act === 'edge-accept') { await api('PATCH', `/api/edges/${id}`, { status: 'active' }); toast('Connection added'); return openCard(S.drawerId, true); }
      if (act === 'edge-reject') { await api('PATCH', `/api/edges/${id}`, { status: 'rejected' }); toast('Suggestion dismissed'); return openCard(S.drawerId, true); }
      if (act === 'toggle-edit-links') { S.editLinks = !S.editLinks; return openCard(S.drawerId, true); }
      if (act === 'toggle-words') { S.showAllWords = !S.showAllWords; return openCard(S.drawerId, true); }
      if (act === 'ref-open-id') { await api('POST', `/api/nodes/${id}/open`, {}); return; }
      if (act === 'clear-target') { S.captureTarget = null; $('.target-banner')?.remove(); return; }
      if (act === 'edge-resolve') { await api('PATCH', `/api/edges/${id}`, { status: 'resolved' }); return openCard(S.drawerId, true); }
      if (act === 'edge-delete') { await api('DELETE', `/api/edges/${id}`); return openCard(S.drawerId, true); }
      if (act === 'alias-remove') { await api('PATCH', `/api/nodes/${S.drawerId}`, { remove_aliases: [actEl.dataset.alias] }); return openCard(S.drawerId, true); }
      if (act === 'tag-remove') { await api('PATCH', `/api/nodes/${S.drawerId}`, { remove_tags: [actEl.dataset.tag] }); return openCard(S.drawerId, true); }
      if (act === 'ref-open' || act === 'ref-reveal') { await api('POST', `/api/nodes/${S.drawerId}/open`, { reveal: act === 'ref-reveal' }); return; }
      if (act === 'use-def') { await api('PATCH', `/api/nodes/${S.drawerId}`, { definition: actEl.dataset.text }); return openCard(S.drawerId, true); }
      return;
    }
    if (nodeEl && nodeEl.dataset.node && !t.closest('[data-sel]') && !t.closest('select')) openCard(+nodeEl.dataset.node);
  } catch (err) { fail(err); }
});

$('#tabs').addEventListener('click', e => { const b = e.target.closest('[data-view]'); if (b) go(b.dataset.view); });
$('#projectSelect').addEventListener('change', e => setProject(+e.target.value));
$('#xpBadge').addEventListener('click', () => go('quests'));
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); if (S.view === 'search') $('#sq')?.select(); else go('search'); }
  else if (e.key === 'Escape' && $('#modal').hidden && $('#drawer').classList.contains('open')) closeDrawer();
});

// Lifecycle: the app runs only while this window is open. A ping every 25 s says "still here";
// closing the window tells the app to exit (a reload reconnects within the grace period).
// Browsers throttle or freeze timers in minimised/background windows, so the window also says when it is
// hidden (the app then waits much longer) and pings at once when it is shown again.
const ping = state => fetch('/api/ping' + (state ? `?state=${state}` : ''), { headers: { 'X-Atlas': '1' }, keepalive: true })
  .catch(() => showOffline());
setInterval(() => ping(), 25000);
document.addEventListener('visibilitychange', () => ping(document.visibilityState === 'hidden' ? 'hidden' : 'visible'));
window.addEventListener('focus', () => ping('visible'));
document.addEventListener('resume', () => ping(document.visibilityState === 'hidden' ? 'hidden' : 'visible'));
if (document.visibilityState === 'hidden') ping('hidden');
window.addEventListener('pagehide', () => { try { navigator.sendBeacon('/api/window-closed'); } catch (_) { /* best effort */ } });

(async function init() {
  try { await loadState(); } catch (e) { fail(e); }
  const params = new URLSearchParams(location.search);
  S.searchQ = params.get('q') || '';                                 // ?q=text#search deep link
  try { if (localStorage.getItem('atlas.smode') === 'brief') S.searchMode = 'brief'; } catch (_) { /* storage blocked */ }
  await render();
  const deep = +params.get('card');                                  // ?card=ID deep link
  if (deep) openCard(deep);
})();
