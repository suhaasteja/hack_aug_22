// Renders bus events as they arrive. Each event type has a handler and a
// stage on the signal chain; unknown types still reach the ticker, so a new
// module shows up here without changes.

const $ = (id) => document.getElementById(id);

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const counts = { segments: 0, ideas: 0, revs: 0, sources: 0, agents: 0 };

// event type -> [chain node, pane] so one map drives both indicators
const STAGES = {
  'transcript.segment': ['listen', 'listen'],
  'idea.detected': ['extract', 'listen'],
  'prd.updated': ['prd', 'prd'],
  'enrichment.found': ['research', 'research'],
  'factory.dispatched': ['factory', 'factory'],
  'build.shipped': ['factory', 'factory'],
  'loop.triggered': ['loop', 'loop'],
  'alert.received': ['loop', 'loop'],
};

function bump(key, el) {
  counts[key] += 1;
  const node = $(el);
  node.textContent = counts[key];
  node.classList.remove('bumped');
  void node.offsetWidth; // restart the animation on consecutive increments
  node.classList.add('bumped');
}

function clearEmpty(pane) {
  const empty = pane.querySelector('.empty');
  if (empty) empty.remove();
}

const atBottom = (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 120;

// Agents answer in long-form markdown. The pane shows the gist; the full reply
// lives in the event and the logs.
function condense(text) {
  return String(text)
    .replace(/```[\s\S]*?```/g, ' ')     // fenced code
    .replace(/^\s*[|#>*-]+\s*/gm, '')    // tables, headings, bullets
    .replace(/[*`_]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

const timers = new Map();
function illuminate(type) {
  const stage = STAGES[type];
  if (!stage) return;
  const [nodeName, paneName] = stage;
  for (const el of [
    document.querySelector(`.node[data-stage="${nodeName}"]`),
    document.querySelector(`.pane[data-pane="${paneName}"]`),
  ]) {
    if (!el) continue;
    el.classList.add('hot');
    el.classList.add('armed');
    clearTimeout(timers.get(el));
    timers.set(el, setTimeout(() => el.classList.remove('hot'), 900));
  }
}

const handlers = {
  'transcript.segment'(p) {
    const pane = $('transcript');
    const stick = atBottom(pane);
    clearEmpty(pane);

    const el = document.createElement('div');
    el.className = 'utt fresh';
    const who = document.createElement('div');
    who.className = 'who';
    who.textContent = p.speaker;
    const what = document.createElement('div');
    what.className = 'what';
    what.textContent = p.text;
    el.append(who, what);
    pane.append(el);
    setTimeout(() => el.classList.remove('fresh'), 2600);

    if (stick) pane.scrollTop = pane.scrollHeight;
    if (p.session_id) $('session-label').textContent = p.session_id;
    bump('segments', 'c-segments');
  },

  'idea.detected'(p) {
    if (!p.is_update) bump('ideas', 'c-ideas');
    const verb = p.status === 'rejected' ? 'dropped' : p.is_update ? 'revised' : 'captured';
    tickDetail(`${verb}: ${p.title}`, p.status === 'rejected' ? 'warn' : 'ok');
  },

  'prd.updated'(p) {
    const pane = $('prd');
    clearEmpty(pane);
    pane.innerHTML = renderPrd(p.doc);
    $('prd-rev').textContent = `rev ${p.rev} · ${p.idea_count} ideas`;
    counts.revs = p.rev;
    $('c-revs').textContent = p.rev;
    $('c-revs').classList.remove('bumped');
    void $('c-revs').offsetWidth;
    $('c-revs').classList.add('bumped');
  },

  'enrichment.found'(p) {
    const pane = $('enrichment');
    clearEmpty(pane);
    const el = document.createElement('div');
    el.className = 'finding';
    const text = document.createElement('div');
    text.textContent = p.finding;
    const link = document.createElement('a');
    link.href = p.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = p.source;
    el.append(text, link);
    pane.append(el);
    pane.scrollTop = pane.scrollHeight;
    bump('sources', 'c-sources');
  },

  'build.shipped'(p) {
    const pane = $('factory');
    clearEmpty(pane);
    const existing = pane.querySelector(`[data-build="${p.repo}"]`);
    const el = existing || document.createElement('div');
    el.dataset.build = p.repo;
    el.className = `build ${p.stage}`;

    if (p.stage === 'shipped') {
      el.innerHTML =
        `<div class="build-head"><b>${esc(p.repo)}</b>` +
        `<span class="badge ok-badge">shipped</span></div>` +
        `<div class="build-summary">${esc(p.summary)}</div>` +
        `<a href="${esc(p.pr_url)}" target="_blank" rel="noopener noreferrer">` +
        `pull request \u2197</a>` +
        `<a href="${esc(p.repo_url)}" target="_blank" rel="noopener noreferrer">` +
        `repository \u2197</a>`;
      tickDetail(`shipped: ${p.repo}`, 'ok');
    } else if (p.stage === 'failed') {
      el.innerHTML =
        `<div class="build-head"><b>${esc(p.repo)}</b>` +
        `<span class="badge warn-badge">failed</span></div>` +
        `<div class="build-summary">${esc(p.error || '')}</div>`;
      tickDetail(`build failed: ${p.repo}`, 'warn');
    } else {
      el.innerHTML =
        `<div class="build-head"><b>${esc(p.repo)}</b>` +
        `<span class="badge">building\u2026</span></div>` +
        `<div class="build-summary">${esc(p.summary || '')}</div>`;
      tickDetail(`building: ${p.repo}`, 'ok');
    }
    if (!existing) pane.prepend(el);
  },

  'factory.dispatched'(p) {
    const pane = $('factory');
    clearEmpty(pane);
    const existing = pane.querySelector(`[data-role="${p.role}"]`);
    const el = existing || document.createElement('div');
    el.dataset.role = p.role;
    el.className = `agent ${p.action === 'retired' ? 'retired' : 'active'}`;
    el.innerHTML = '';

    const head = document.createElement('div');
    head.className = 'agent-head';
    const name = document.createElement('b');
    name.textContent = p.role;
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = p.action === 'retired' ? 'retired' : `rev ${p.rev}`;
    head.append(name, badge);

    const mission = document.createElement('div');
    mission.className = 'agent-mission';
    mission.textContent = p.mission;

    const why = document.createElement('div');
    why.className = 'agent-why';
    why.textContent =
      p.action === 'retired' ? p.reason : (p.justification || []).join(' · ');

    el.append(head, mission, why);
    if (!existing) pane.append(el);

    tickDetail(`${p.action}: ${p.role}`, p.action === 'retired' ? 'warn' : 'ok');
    counts.agents = pane.querySelectorAll('.agent.active').length;
    $('c-agents').textContent = counts.agents;
  },

  'loop.triggered'(p) {
    const pane = $('loop');
    clearEmpty(pane);
    const el = document.createElement('div');

    if (p.stage === 'triaged') {
      el.className = 'incident';
      el.innerHTML =
        `<div class="incident-head"><b>${esc(p.alert)}</b>` +
        `<span class="badge warn-badge">${esc(p.severity)}</span></div>` +
        `<div class="incident-body">${esc(p.brief.summary)}</div>` +
        `<div class="incident-meta">→ ${esc(p.target_role)} · ${esc(p.brief.confidence)} confidence · ` +
        `${p.evidence.logs} logs, ${p.evidence.spans} spans</div>` +
        `<div class="incident-action">${esc(p.brief.recommended_action)}</div>`;
      tickDetail(`triaged: ${p.alert}`, 'warn');
    } else {
      el.className = 'incident answered';
      el.innerHTML =
        `<div class="incident-head"><b>${esc(p.target_role)} replied</b>` +
        `<span class="badge">${p.response.length} chars</span></div>` +
        `<div class="incident-body clamp">${esc(condense(p.response))}</div>`;
      tickDetail(`answered: ${p.target_role}`, 'ok');
    }

    pane.append(el);
    pane.scrollTop = pane.scrollHeight;
  },
};

// The structured PRD is rendered rather than its markdown, so Out of Scope can
// be styled as dropped — the visible proof the document rewrites itself.
function renderPrd(doc) {
  const out = [
    `<h3 class="prd-title">${esc(doc.title)}</h3>`,
    `<p class="prd-summary">${esc(doc.summary)}</p>`,
  ];

  const list = (heading, items, fmt) => {
    if (!items || !items.length) return;
    out.push(`<h4>${heading}</h4><ul>`);
    for (const i of items) out.push(`<li>${fmt ? fmt(i) : esc(i)}</li>`);
    out.push('</ul>');
  };

  list('Problem', doc.problem);
  list('Users', doc.users);
  list('Features', doc.features,
    (f) => `<b>${esc(f.title)}</b> <em class="prio">${esc(f.priority)}</em><br>${esc(f.detail)}`);
  list('Requirements', doc.requirements);
  list('Constraints', doc.constraints);

  if (doc.out_of_scope?.length) {
    out.push('<h4 class="dropped-h">Out of Scope</h4><ul class="dropped">');
    for (const d of doc.out_of_scope) {
      out.push(`<li><b>${esc(d.item)}</b> — ${esc(d.reason)}</li>`);
    }
    out.push('</ul>');
  }

  list('Open Questions', doc.open_questions);
  list('Success Metrics', doc.success_metrics);
  return out.join('');
}

function tick(type) {
  const t = document.createElement('span');
  t.className = 'tick';
  t.innerHTML = `<b>${esc(type)}</b>`;
  $('ticker').prepend(t);
  trimTicker();
}

function tickDetail(text, tone) {
  const t = document.createElement('span');
  t.className = `tick tick-${tone}`;
  t.textContent = text;
  $('ticker').prepend(t);
  trimTicker();
}

function trimTicker() {
  while ($('ticker').children.length > 9) $('ticker').lastChild.remove();
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => $('conn-led').classList.add('live');
  ws.onclose = () => {
    $('conn-led').classList.remove('live');
    setTimeout(connect, 1500); // a pipeline restart shouldn't need a refresh
  };
  ws.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    illuminate(event.type);
    if (!handlers[event.type]) tick(event.type);
    handlers[event.type]?.(event.payload);
  };
}

connect();
