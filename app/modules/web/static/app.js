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
};

function tick(type) {
  const t = document.createElement('span');
  t.className = 'tick';
  t.innerHTML = `<b>${type}</b>`;
  $('ticker').prepend(t);
  while ($('ticker').children.length > 12) $('ticker').lastChild.remove();
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
