// Renders bus events as they stream in. Each event type gets a handler;
// unknown types just land in the ticker, so new stages need no changes here.

const $ = (id) => document.getElementById(id);

const counts = { segments: 0, ideas: 0, revs: 0, sources: 0 };

function bump(key, el) {
  counts[key] += 1;
  $(el).textContent = counts[key];
}

function clearEmpty(pane) {
  const empty = pane.querySelector('.empty');
  if (empty) empty.remove();
}

function atBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
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
    setTimeout(() => el.classList.remove('fresh'), 2500);

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
  },
};

// Renders the structured PRD rather than markdown text, so Out of Scope can be
// styled distinctly — it's the visible proof the document rewrites itself.
function renderPrd(doc) {
  const esc = (s) =>
    String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
  const out = [`<h3 class="prd-title">${esc(doc.title)}</h3>`,
               `<p class="prd-summary">${esc(doc.summary)}</p>`];

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
  t.innerHTML = `<b>${type}</b>`;
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
  while ($('ticker').children.length > 10) $('ticker').lastChild.remove();
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => $('conn-dot').classList.add('live');
  ws.onclose = () => {
    $('conn-dot').classList.remove('live');
    setTimeout(connect, 1500); // pipeline restarts shouldn't require a refresh
  };
  ws.onmessage = (msg) => {
    const event = JSON.parse(msg.data);
    tick(event.type);
    handlers[event.type]?.(event.payload);
  };
}

connect();
