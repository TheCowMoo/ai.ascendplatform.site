/* The voice overlay, exercised without a browser or a key.
 *
 * Run: node tests/voice_ui.mjs
 *
 * The Python suite checks the server's setup JSON and greps the client for the branches.
 * This goes one step further: it loads admin/voice.js against a fake DOM, a fake
 * AudioContext and a fake WebSocket, then proves that
 *   - an open socket is still "connecting",
 *   - setupComplete is what makes it "listening",
 *   - a close before setup says "rejected" and carries the server's reason,
 *   - a server error and a goAway are visible in the overlay,
 *   - transcription chunks become two turns and turnComplete returns to listening,
 *   - a manager question is injected as an internal team update and the answer tool is
 *     relayed, and a finished job is injected as the result.
 *
 * It is deliberately small: the fake DOM implements only what voice.js touches.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

function Node(tag) {
  this.tagName = tag;
  this.children = [];
  this.className = '';
  this.attrs = {};
  this.hidden = false;
  this._text = '';
  this.style = { setProperty: (key, value) => { this.style[key] = value; } };
  this.scrollTop = 0;
  this.scrollHeight = 0;
}
Object.defineProperty(Node.prototype, 'textContent', {
  get() { return this._text; },
  set(value) { this._text = String(value); this.children = []; }
});
Object.defineProperty(Node.prototype, 'innerHTML', {
  set() { this._text = ''; this.children = []; }
});
Object.defineProperty(Node.prototype, 'firstChild', {
  get() { return this.children[0] || null; }
});
Object.defineProperty(Node.prototype, 'lastChild', {
  get() { return this.children[this.children.length - 1] || null; }
});
Node.prototype.appendChild = function (child) {
  child.parentNode = this;
  this.children.push(child);
  return child;
};
Node.prototype.removeChild = function (child) {
  const index = this.children.indexOf(child);
  if (index >= 0) this.children.splice(index, 1);
};
Node.prototype.setAttribute = function (key, value) { this.attrs[key] = value; };
Node.prototype.focus = function () {};
Node.prototype.select = function () {};
Node.prototype.querySelector = function (selector) {
  if (!selector.startsWith('.')) return null;
  const wanted = selector.slice(1);
  let found = null;
  (function walk(node) {
    for (const child of node.children) {
      if ((child.className || '').split(/\s+/).includes(wanted)) { found = child; return; }
      walk(child);
      if (found) return;
    }
  })(this);
  return found;
};
function findAll(node, className, out = []) {
  for (const child of node.children) {
    if ((child.className || '').split(/\s+/).includes(className)) out.push(child);
    findAll(child, className, out);
  }
  return out;
}

const document = {
  body: new Node('body'),
  activeElement: null,
  createElement: (tag) => new Node(tag),
  addEventListener: () => {},
  execCommand: () => true
};

const stream = { getTracks: () => [{ stop() {} }] };
const navigator = { mediaDevices: { getUserMedia: () => Promise.resolve(stream) } };

const context = {
  state: 'running',
  currentTime: 0,
  sampleRate: 48000,
  destination: new Node('destination'),
  resume: () => Promise.resolve(),
  close: () => Promise.resolve(),
  createGain: () => ({ gain: { value: 1 }, connect() {}, disconnect() {} }),
  createAnalyser: () => ({
    fftSize: 512, connect() {}, disconnect() {},
    getFloatTimeDomainData(data) { data.fill(0); }
  }),
  createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
  createScriptProcessor: () => {
    const node = { connect() {}, disconnect() {}, onaudioprocess: null };
    context.lastProcessor = node;
    return node;
  },
  createBuffer: (channels, length, rate) => ({
    duration: length / rate,
    getChannelData: () => new Float32Array(length)
  }),
  createBufferSource: () => ({
    buffer: null, connect() {}, start() {}, stop() {}, onended: null
  })
};

const session = {
  token: 'ephemeral-token',
  endpoint: 'wss://example.test/live',
  model: 'gemini-3.8-live',
  api_version: 'v1beta',
  voice: 'Kore',
  remaining_minutes: 10,
  setup: { setup: {
    model: 'models/gemini-3.8-live',
    inputAudioTranscription: {},
    outputAudioTranscription: {},
    realtimeInputConfig: { automaticActivityDetection: { disabled: false } }
  } }
};

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.sent = [];
    FakeWebSocket.last = this;
  }
  send(payload) { this.sent.push(JSON.parse(payload)); }
  close() { this.readyState = 3; }
}

const window = globalThis;
window.document = document;
Object.defineProperty(window, 'navigator', { value: navigator, configurable: true });
const toolCalls = [];
const jobs = {
  7: {
    job: { id: 7, status: 'done', result: 'Asked the user for the brief.' },
    events: [{ seq: 1, source: 'Manager', type: 'question',
               content: JSON.stringify(['Who is the trip for?']) }]
  },
  8: {
    job: { id: 8, status: 'done', result: 'The quiz is ready.' },
    events: [{ seq: 1, source: 'Manager', type: 'message',
               content: 'The quiz is ready.' }]
  }
};
window.fetch = async (url, options) => {
  if (url === '/api/voice/session') {
    return { ok: true, status: 200, json: async () => session };
  }
  if (url === '/api/voice/tool') {
    const body = JSON.parse((options && options.body) || '{}');
    toolCalls.push(body);
    const jobId = body.name === 'answer_team_question' ? 8 : 7;
    return { ok: true, status: 202,
             json: async () => ({ message: 'Working on it.', job_id: jobId }) };
  }
  if (url.indexOf('/api/jobs/') === 0) {
    const id = Number(url.split('/api/jobs/')[1].split('/')[0]);
    return { ok: true, status: 200, json: async () => jobs[id] || { job: {}, events: [] } };
  }
  if (url.indexOf('/api/approvals') === 0) {
    return { ok: true, status: 200, json: async () => ({ approvals: [] }) };
  }
  throw new Error('unexpected fetch ' + url);
};
window.WebSocket = FakeWebSocket;
window.AudioContext = function () { return context; };
window.webkitAudioContext = window.AudioContext;
window.requestAnimationFrame = () => 1;
window.cancelAnimationFrame = () => {};

const source = fs.readFileSync(new URL('../admin/voice.js', import.meta.url), 'utf8');
new Function('window', 'document', 'navigator', 'fetch', 'WebSocket', 'AudioContext',
             source)(window, document, navigator, window.fetch, FakeWebSocket, window.AudioContext);

const Voice = window.AscendVoice;
const overlay = () => document.body.children.find((node) => node.className === 'voice-overlay');
const text = (className) => {
  const node = overlay().querySelector('.' + className);
  return node ? node.textContent : '';
};
const notes = () => findAll(overlay(), 'voice-system').map((node) => node.textContent);
const bodies = () => findAll(overlay(), 'voice-body').map((node) => node.textContent);

function message(ws, payload) {
  ws.onmessage({ data: JSON.stringify(payload) });
}

function binaryMessage(ws, payload) {
  ws.onmessage({ data: new TextEncoder().encode(JSON.stringify(payload)).buffer });
}

assert.equal(await Voice.open({ page: 'test' }), true, 'open resolves');
let ws = FakeWebSocket.last;
assert.equal(ws.sent.length, 0, 'nothing is sent before open');
ws.onopen();
assert.equal(ws.sent.length, 1, 'the setup is the first message');
assert.deepEqual(ws.sent[0].setup.inputAudioTranscription, {}, 'transcription is requested');
assert.deepEqual(ws.sent[0].setup.outputAudioTranscription, {}, 'output transcription is requested');
assert.equal(Voice.phase(), 'connecting', 'an open socket is not listening');

binaryMessage(ws, { setupComplete: {} });
assert.equal(Voice.phase(), 'listening', 'a binary setupComplete is the gate');
assert.equal(text('voice-state'), 'Listening', 'the state line says listening');

// The microphone path must use realtimeInput.audio, not the generic mediaChunks list.
context.lastProcessor.onaudioprocess({
  inputBuffer: { getChannelData: () => new Float32Array(2048) }
});
const audioFrame = ws.sent.find((item) => item.realtimeInput && item.realtimeInput.audio);
assert.ok(audioFrame, 'the microphone sends a realtime audio frame');
assert.equal(audioFrame.realtimeInput.audio.mimeType, 'audio/pcm;rate=16000',
              'the frame is 16 kHz PCM');
assert.equal('mediaChunks' in audioFrame.realtimeInput, false,
              'audio does not use the generic media chunks field');

message(ws, { serverContent: { inputTranscription: { text: 'hello' } } });
message(ws, { serverContent: { outputTranscription: { text: 'hi' } } });
message(ws, { serverContent: { turnComplete: true } });
assert.deepEqual(bodies(), ['hello', 'hi'], 'one bubble per turn');
assert.equal(Voice.phase(), 'listening', 'turnComplete returns to listening');

// ---------- The team bridge ----------
// A delegated job asks a question. It must reach the Live session as an internal update,
// not as a new user request, and the second tool must carry the answer back.
message(ws, { toolCall: { functionCalls: [{
  id: 'call-1', name: 'delegate_to_team', args: { instruction: 'build a trip quiz' }
}] } });
await new Promise((resolve) => setTimeout(resolve, 20));
assert.equal(toolCalls.length, 1, 'the delegation was sent to the server');
assert.equal(toolCalls[0].name, 'delegate_to_team', 'the first call delegates');

message(ws, { serverContent: { turnComplete: true } });
const questionUpdate = ws.sent.filter((item) => item.clientContent).pop();
assert.ok(questionUpdate, 'the question is injected into the Live session');
assert.match(questionUpdate.clientContent.turns[0].parts[0].text, /Who is the trip for\?/,
             'the injected update carries the question');
assert.match(questionUpdate.clientContent.turns[0].parts[0].text, /\[Team update/,
             'it is marked as an internal team update');
assert.ok(notes().some((line) => line.includes('Who is the trip for?')),
          'the manager question is visible in the overlay');

message(ws, { toolCall: { functionCalls: [{
  id: 'call-2', name: 'answer_team_question', args: { answers: 'For a bakery trip' }
}] } });
await new Promise((resolve) => setTimeout(resolve, 20));
assert.equal(toolCalls.length, 2, 'the answer was sent to the server');
assert.equal(toolCalls[1].name, 'answer_team_question', 'the second call answers');
assert.equal(toolCalls[1].args.answers, 'For a bakery trip', 'the words are passed through');

message(ws, { serverContent: { turnComplete: true } });
const resultUpdate = ws.sent.filter((item) => item.clientContent).pop();
assert.ok(resultUpdate, 'the finished job is injected into the Live session');
assert.match(resultUpdate.clientContent.turns[0].parts[0].text, /quiz is ready/,
             'the result is carried back to the voice');
assert.ok(notes().some((line) => line.includes('The team finished job #8')),
          'the completion is visible in the overlay');

message(ws, { goAway: { timeLeft: '9s' } });
assert.ok(notes().some((line) => line.includes('closes in 9s')), 'goAway is visible');

message(ws, { error: { code: 1008, message: 'model not found' } });
assert.equal(Voice.phase(), 'error', 'a server error is an error');
assert.ok(text('voice-detail').includes('model not found'), 'the server words survive');
Voice.stop();
assert.equal(Voice.phase(), 'idle', 'stop closes the mode');

assert.equal(await Voice.open({ page: 'test' }), true, 'a second session opens');
ws = FakeWebSocket.last;
ws.onopen();
ws.onclose({ code: 1008, reason: 'model not found' });
assert.equal(Voice.phase(), 'error', 'a close before setup is an error');
assert.ok(text('voice-detail').includes('was rejected'), 'it says the session was rejected');
assert.ok(text('voice-detail').includes('model not found'), 'and quotes the reason');
Voice.stop();

// ---------- Docked in the Studio ----------
// The same panel in another parent. Everything above still passes, which is the point:
// docking changes where the panel lives, not how a session, a transcript or a phase works.
const host = new Node('div');
document.body.appendChild(host);
const studioCanvas = { kind: 'deliverable', id: 42 };
assert.equal(await Voice.open({
  page: 'studio', host, getContext: () => studioCanvas
}), true, 'a docked session opens');
ws = FakeWebSocket.last;
ws.onopen();
assert.equal(host.children.length, 1, 'the panel mounted in the host');
assert.equal(overlay().hidden, true, 'the overlay is not raised while docked');
assert.match(host.children[0].className, /voice-docked/,
             'the panel carries the docked class');

binaryMessage(ws, { setupComplete: {} });
assert.equal(Voice.phase(), 'listening', 'the docked panel is the same session');

message(ws, { toolCall: { functionCalls: [{
  id: 'call-dock-1', name: 'delegate_to_team', args: { instruction: 'make it warmer' }
}] } });
await new Promise((resolve) => setTimeout(resolve, 20));
const dockedCall = toolCalls[toolCalls.length - 1];
assert.equal(dockedCall.name, 'delegate_to_team', 'the docked call delegates');
assert.deepEqual(dockedCall.artifact, { kind: 'deliverable', id: 42 },
                 'the request carries the artifact on screen');

// The hook is read when the call is made, not when the session opened: the user may
// pick another draft while the dialogue is still running.
studioCanvas.id = 43;
message(ws, { toolCall: { functionCalls: [{
  id: 'call-dock-2', name: 'delegate_to_team', args: { instruction: 'shorter' }
}] } });
await new Promise((resolve) => setTimeout(resolve, 20));
assert.equal(toolCalls[toolCalls.length - 1].artifact.id, 43,
             'the canvas is read at call time');

Voice.stop();
assert.equal(Voice.phase(), 'idle', 'stop ends the docked session');
assert.equal(host.children.length, 0, 'and detaches the panel from the host');
assert.equal(document.body.children.filter(
  (node) => node.className === 'voice-overlay').length, 1,
  'the overlay is still the one panel owner');

console.log('VOICE UI OK - the gate, the transcripts and the failure paths');

