const byId = (id) => document.getElementById(id);
const form = byId('search-form');
const connectButton = byId('connect');
const disconnectButton = byId('disconnect');
const startButton = byId('start');
const originStatuses = new Map();
const browserLinks = new Map();
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

function renderOriginStatuses() {
  byId('origin-statuses').replaceChildren();
  for (const [origin, status] of originStatuses) {
    const item = document.createElement('li');
    item.textContent = `${origin}: ${status}`;
    byId('origin-statuses').append(item);
  }
}

function addBrowser(origin, url) {
  // Only allow browser links to Skyvern; never render a server-provided HTML string.
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' || parsed.hostname !== 'app.skyvern.com') {
    throw new Error('Expected a Skyvern live browser URL.');
  }
  browserLinks.set(origin, parsed.href);
  byId('browser-links').replaceChildren();
  for (const [airport, liveUrl] of browserLinks) {
    const entry = document.createElement('div');
    entry.className = 'browser-entry';
    const link = document.createElement('a');
    link.href = liveUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = `Open ${airport} live browser`;
    const view = document.createElement('button');
    view.type = 'button';
    view.textContent = `View ${airport} here`;
    view.addEventListener('click', () => {
      byId('live-browser').src = liveUrl;
      byId('live-browser').hidden = false;
    });
    entry.append(link, view);
    byId('browser-links').append(entry);
  }
  byId('live-browser').src = parsed.href;
  byId('live-browser').hidden = false;
}

function renderResult(result) {
  const flights = result.flights || [];
  byId('results-summary').textContent = `${result.status}: ${flights.length} flight${flights.length === 1 ? '' : 's'}. Search ${result.search_id}.`;
  byId('flights').replaceChildren();
  for (const flight of flights) {
    const row = document.createElement('tr');
    const values = [
      `${flight.origin} → ${flight.destination}`,
      flight.airline,
      flight.outbound_departure_time_text,
      flight.outbound_arrival_time_text,
      flight.outbound_duration_text,
      flight.outbound_stops,
      `${flight.currency} ${flight.price}`,
    ];
    for (const value of values) {
      const cell = document.createElement('td');
      cell.textContent = String(value ?? '');
      row.append(cell);
    }
    byId('flights').append(row);
  }
  byId('results-table').hidden = flights.length === 0;
  byId('result-json').textContent = JSON.stringify(result, null, 2);
  byId('raw-result').hidden = false;
  if (result.error) showError(`${result.error.code}: ${result.error.message}`);
}

function handleMessage(message) {
  if (message.session_id && message.session_id !== sessionId) return;
  const payload = message.payload || message;
  switch (message.type) {
    case 'search.status':
      if (payload.origin) {
        originStatuses.set(payload.origin, payload.status);
        renderOriginStatuses();
      } else {
        byId('search-status').textContent = `Search: ${payload.status}`;
      }
      log(`${payload.origin ? `${payload.origin}: ` : ''}${payload.status}`, message.timestamp);
      break;
    case 'browser.live_view':
      addBrowser(payload.origin || 'search', payload.url);
      log(`${payload.origin || 'Search'} browser is ready`, message.timestamp);
      break;
    case 'search.result':
      renderResult(payload.result);
      byId('search-status').textContent = `Search: ${payload.result.status}`;
      busy = false;
      updateControls();
      log(`Result received: ${payload.result.flights.length} flights`, message.timestamp);
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
    busy = false;
    byId('connection-status').textContent = 'Connected';
    byId('search-status').textContent = 'Ready to search.';
    updateControls();
    log('Connected');
  });
  newSocket.addEventListener('message', (event) => {
    try { handleMessage(JSON.parse(event.data)); }
    catch (error) { showError(`Could not read server message: ${error.message}`); }
  });
  newSocket.addEventListener('error', () => {
    showError('WebSocket connection failed. Check that the local bridge is running and the URL is correct.');
  });
  newSocket.addEventListener('close', () => {
    byId('connection-status').textContent = 'Disconnected';
    if (busy) {
      byId('search-status').textContent = 'Disconnected during search. Its final status is unknown; reconnect before starting another search.';
    }
    updateControls();
    log('Disconnected');
  });
});

disconnectButton.addEventListener('click', () => socket?.close());

form.elements.trip_type.addEventListener('change', () => {
  const roundTrip = form.elements.trip_type.value === 'round_trip';
  byId('return-label').hidden = !roundTrip;
  form.elements.return_date.required = roundTrip;
});

form.addEventListener('submit', (event) => {
  event.preventDefault();
  if (socket?.readyState !== WebSocket.OPEN || busy) return;
  sessionId = crypto.randomUUID();
  const fields = new FormData(form);
  const request = {
    session_id: sessionId,
    origins: fields.get('origins').split(',').map((value) => value.trim().toUpperCase()).filter(Boolean),
    destination: fields.get('destination').trim().toUpperCase(),
    departure_date: fields.get('departure_date'),
    trip_type: fields.get('trip_type'),
    currency: fields.get('currency').trim().toUpperCase(),
  };
  if (request.trip_type === 'round_trip') request.return_date = fields.get('return_date');
  if (fields.get('budget').trim()) request.budget = Number(fields.get('budget'));
  showError('');
  originStatuses.clear();
  browserLinks.clear();
  renderOriginStatuses();
  byId('browser-links').textContent = 'Waiting for a live browser…';
  byId('live-browser').removeAttribute('src');
  byId('live-browser').hidden = true;
  byId('flights').replaceChildren();
  byId('results-table').hidden = true;
  byId('raw-result').hidden = true;
  byId('result-json').textContent = '';
  byId('results-summary').textContent = 'Waiting for results…';
  byId('events').textContent = '';
  byId('search-status').textContent = 'Search requested…';
  busy = true;
  updateControls();
  socket.send(JSON.stringify({ action: 'search', request }));
  log(`Requested ${request.origins.join(', ')} → ${request.destination}`);
});
