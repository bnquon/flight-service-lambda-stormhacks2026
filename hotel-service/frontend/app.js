const byId = (id) => document.getElementById(id);
const form = byId('search-form');
const connectButton = byId('connect');
const disconnectButton = byId('disconnect');
const startButton = byId('start');
const browserLinks = new Map();
const browserStreams = new Map();
let selectedBrowser = null;
let socket = null;
let busy = false;
let sessionId = null;

function updateControls() {
  const connected = socket?.readyState === WebSocket.OPEN;
  const connecting = socket?.readyState === WebSocket.CONNECTING;
  connectButton.disabled = connected || connecting;
  disconnectButton.disabled = !connected && !connecting;
  byId('websocket-url').disabled = connected || connecting;
  startButton.disabled = !connected || busy;
  for (const input of form.elements) {
    if (input !== startButton) input.disabled = busy;
  }
}

function showError(message) {
  byId('error').textContent = message;
  byId('error').hidden = !message;
}

function log(message, timestamp) {
  const time = new Date(timestamp || Date.now()).toLocaleTimeString();
  const events = byId('events');
  events.textContent += `${events.textContent ? '\n' : ''}${time}  ${message}`;
  events.scrollTop = events.scrollHeight;
}

function renderBrowserPreview() {
  const stream = browserStreams.get(selectedBrowser);
  const preview = byId('live-browser');
  const labels = {
    starting: 'Connecting to live browser…',
    live: 'Live',
    ended: 'Session ended',
    unavailable: 'Live preview unavailable',
    disconnected: 'Disconnected',
  };
  const label = stream
    ? `Hotel browser: ${labels[stream.status] || 'Waiting for live preview…'}${stream.frame && stream.status !== 'live' ? ' — showing the last frame' : ''}`
    : 'Waiting for a live browser…';
  if (byId('browser-status').textContent !== label) byId('browser-status').textContent = label;
  preview.hidden = !stream?.frame;
  if (stream?.frame) {
    if (preview.getAttribute('src') !== stream.frame) preview.src = stream.frame;
    preview.alt = 'Live hotel browser preview';
  } else {
    preview.removeAttribute('src');
  }
}

function renderBrowsers() {
  const browsers = new Set([...browserStreams.keys(), ...browserLinks.keys()]);
  if (!selectedBrowser && browsers.size) selectedBrowser = browsers.values().next().value;
  byId('browser-links').replaceChildren();
  for (const origin of browsers) {
    const entry = document.createElement('div');
    entry.className = 'browser-entry';
    const view = document.createElement('button');
    view.type = 'button';
    view.textContent = 'View hotel browser';
    view.setAttribute('aria-pressed', String(origin === selectedBrowser));
    view.addEventListener('click', () => {
      selectedBrowser = origin;
      renderBrowsers();
    });
    entry.append(view);
    const liveUrl = browserLinks.get(origin);
    if (liveUrl) {
      const link = document.createElement('a');
      link.href = liveUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open in Skyvern';
      entry.append(link);
    }
    byId('browser-links').append(entry);
  }
  renderBrowserPreview();
}

function addBrowser(origin, url) {
  if (!url) return;
  // Only allow browser links to Skyvern; never render a server-provided HTML string.
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' || parsed.hostname !== 'app.skyvern.com') {
    throw new Error('Expected a Skyvern live browser URL.');
  }
  browserLinks.set(origin, parsed.href);
  renderBrowsers();
}

function updateBrowserStream(payload, isFrame = false) {
  if (isFrame && (payload.mime_type !== 'image/jpeg' || typeof payload.data !== 'string' || !payload.data)) return;
  const origin = payload.origin || payload.website || 'booking_com';
  let stream = browserStreams.get(origin);
  if (stream && stream.browserSessionId !== payload.browser_session_id) {
    // A delayed frame from an earlier browser must not replace the current preview.
    if (isFrame || payload.status !== 'starting') return;
    stream = null;
  }
  const newStream = !stream;
  if (!stream) {
    stream = { browserSessionId: payload.browser_session_id, status: 'starting', frame: null };
    browserStreams.set(origin, stream);
  }
  if (isFrame) {
    stream.frame = `data:image/jpeg;base64,${payload.data}`;
    if (stream.status === 'starting' || stream.status === 'live') stream.status = 'live';
    if (newStream || !selectedBrowser) renderBrowsers();
    else if (origin === selectedBrowser) renderBrowserPreview();
  } else {
    stream.status = payload.status;
    // Select the active hotel browser.
    if (payload.status === 'starting') selectedBrowser = origin;
    renderBrowsers();
  }
}

function safeHotelUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:' && parsed.hostname === 'www.booking.com' && parsed.pathname.startsWith('/hotel/')) return parsed.href;
  } catch { /* Display the name without a link if the URL is invalid. */ }
  return null;
}

function renderResult(result) {
  const hotels = result.hotels || [];
  byId('results-summary').textContent = `${result.status}: ${hotels.length} hotel${hotels.length === 1 ? '' : 's'}. Search ${result.search_id}.`;
  byId('hotels').replaceChildren();
  for (const hotel of hotels) {
    const row = document.createElement('tr');
    const name = document.createElement('td');
    const url = safeHotelUrl(hotel.url);
    if (url) {
      const link = document.createElement('a');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = hotel.name;
      name.append(link);
    } else {
      name.textContent = hotel.name;
    }
    row.append(name);
    for (const value of [
      `${hotel.currency} ${hotel.total_price}`,
      hotel.rating,
      hotel.review_count,
      hotel.price_note,
    ]) {
      const cell = document.createElement('td');
      cell.textContent = String(value ?? '');
      row.append(cell);
    }
    byId('hotels').append(row);
  }
  byId('results-table').hidden = hotels.length === 0;
  byId('result-json').textContent = JSON.stringify(result, null, 2);
  byId('raw-result').hidden = false;
  if (result.error) showError(`${result.error.code}: ${result.error.message}`);
}

function handleMessage(message) {
  if (message.session_id && message.session_id !== sessionId) return;
  const payload = message.payload || message;
  switch (message.type) {
    case 'search.status':
      byId('search-status').textContent = `Search: ${payload.status}`;
      log(payload.status, message.timestamp);
      break;
    case 'browser.live_view':
      addBrowser(payload.origin || payload.website || 'booking_com', payload.url);
      log('Hotel browser is ready', message.timestamp);
      break;
    case 'browser.stream':
      updateBrowserStream(payload);
      log(`Hotel live preview: ${payload.status}`, message.timestamp);
      break;
    case 'browser.frame':
      updateBrowserStream(payload, true);
      break;
    case 'search.result':
      renderResult(payload.result);
      byId('search-status').textContent = `Search: ${payload.result.status}`;
      busy = false;
      updateControls();
      log(`Result received: ${payload.result.hotels.length} hotels`, message.timestamp);
      break;
    case 'search.error': {
      const error = payload.error;
      showError(`${error.code}: ${error.message}${error.details ? `\n${JSON.stringify(error.details, null, 2)}` : ''}`);
      byId('search-status').textContent = error.code === 'SEARCH_BUSY' ? 'A search is still running.' : 'Search failed';
      busy = error.code === 'SEARCH_BUSY';
      updateControls();
      log(`${error.code}: ${error.message}`, message.timestamp);
      break;
    }
    default:
      log(message.type || 'Unknown event', message.timestamp);
  }
}

connectButton.addEventListener('click', () => {
  showError('');
  let newSocket;
  try {
    const url = new URL(byId('websocket-url').value);
    if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('Use a ws:// or wss:// URL.');
    newSocket = new WebSocket(url.href);
  } catch (error) {
    showError(error.message);
    return;
  }
  socket = newSocket;
  byId('connection-status').textContent = 'Connecting…';
  updateControls();
  newSocket.addEventListener('open', () => {
    if (socket !== newSocket) return;
    busy = false;
    byId('connection-status').textContent = 'Connected';
    byId('search-status').textContent = 'Ready to search.';
    updateControls();
    log('Connected');
  });
  newSocket.addEventListener('message', (event) => {
    if (socket !== newSocket) return;
    try { handleMessage(JSON.parse(event.data)); }
    catch (error) { showError(`Could not read server message: ${error.message}`); }
  });
  newSocket.addEventListener('error', () => {
    if (socket !== newSocket) return;
    showError('WebSocket connection failed. Check that the local bridge is running and the URL is correct.');
  });
  newSocket.addEventListener('close', () => {
    if (socket !== newSocket) return;
    byId('connection-status').textContent = 'Disconnected';
    for (const stream of browserStreams.values()) {
      if (stream.status === 'starting' || stream.status === 'live') stream.status = 'disconnected';
    }
    renderBrowserPreview();
    if (busy) {
      byId('search-status').textContent = 'Disconnected during search. Its final status is unknown; reconnect before starting another search.';
    }
    updateControls();
    log('Disconnected');
  });
});

disconnectButton.addEventListener('click', () => socket?.close());

form.addEventListener('submit', (event) => {
  event.preventDefault();
  if (socket?.readyState !== WebSocket.OPEN || busy) return;
  sessionId = crypto.randomUUID();
  const fields = new FormData(form);
  const request = {
    session_id: sessionId,
    destination: fields.get('destination').trim(),
    check_in: fields.get('check_in'),
    check_out: fields.get('check_out'),
    adults: Number(fields.get('adults')),
    rooms: 1,
    currency: 'CAD',
  };
  if (fields.get('budget').trim()) request.budget = Number(fields.get('budget'));
  showError('');
  browserLinks.clear();
  browserStreams.clear();
  selectedBrowser = null;
  renderBrowsers();
  byId('hotels').replaceChildren();
  byId('results-table').hidden = true;
  byId('raw-result').hidden = true;
  byId('result-json').textContent = '';
  byId('results-summary').textContent = 'Waiting for results…';
  byId('events').textContent = '';
  byId('search-status').textContent = 'Search requested…';
  busy = true;
  updateControls();
  socket.send(JSON.stringify({ action: 'search', request }));
  log(`Requested hotels in ${request.destination}`);
});
