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
 *   - transcription chunks become two turns and turnComplete returns to listening.
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
  createScriptProcessor: () => ({ connect() {}, disconnect() {}, onaudioprocess: null }),
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
  api_version: 'v1alpha',
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
window.fetch = async (url) => {
  if (url === '/api/voice/session') {
    return { ok: true, status: 200, json: async () => session };
  }
  if (url === '/api/voice/tool') {
    return { ok: true, status: 202, json: async () => ({ message: 'Working on it.', job_id: 7 }) };
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

assert.equal(await Voice.open({ page: 'test' }), true, 'open resolves');
let ws = FakeWebSocket.last;
assert.equal(ws.sent.length, 0, 'nothing is sent before open');
ws.onopen();
assert.equal(ws.sent.length, 1, 'the setup is the first message');
assert.deepEqual(ws.sent[0].setup.inputAudioTranscription, {}, 'transcription is requested');
assert.deepEqual(ws.sent[0].setup.outputAudioTranscription, {}, 'output transcription is requested');
assert.equal(Voice.phase(), 'connecting', 'an open socket is not listening');

message(ws, { setupComplete: {} });
assert.equal(Voice.phase(), 'listening', 'setupComplete is the gate');
assert.equal(text('voice-state'), 'Listening', 'the state line says listening');

message(ws, { serverContent: { inputTranscription: { text: 'hello' } } });
message(ws, { serverContent: { outputTranscription: { text: 'hi' } } });
message(ws, { serverContent: { turnComplete: true } });
assert.deepEqual(bodies(), ['hello', 'hi'], 'one bubble per turn');
assert.equal(Voice.phase(), 'listening', 'turnComplete returns to listening');

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

console.log('VOICE UI OK - the gate, the transcripts and the failure paths');

