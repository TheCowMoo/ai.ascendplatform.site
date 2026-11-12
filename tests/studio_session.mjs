/* The active chat session, shared by the Studio, the Ask sheet and their voices.
 *
 * Run: node tests/studio_session.mjs
 *
 * The value is only useful if every door reads the same key and "New chat" clears it.
 * This evaluates the real store functions from admin.js with a fake state and storage.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');
const start = source.indexOf('  function studioOrgKey() {');
const end = source.indexOf('  // ---------- Studio: chats on the left', start);
assert.ok(start > 0 && end > start, 'the session store is in admin.js');
const code = source.slice(start, end);

function makeStorage() {
  const map = new Map();
  return {
    map,
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)); },
    removeItem: (key) => { map.delete(key); }
  };
}

function load(state, storage) {
  const body = code + String.fromCharCode(10) +
    'return { key: studioSessionKey, read: readStudioSession, write: writeStudioSession };';
  return new Function('state', 'localStorage', body)(state, storage);
}

const user = { id: 3, org: { id: 7 } };
let storage = makeStorage();
let store = load({ user }, storage);

assert.equal(store.key(), 'studio:session:7:3', 'scoped by client and user');
assert.equal(store.read(), null, 'nothing remembered yet');
assert.equal(store.write(42), 42, 'writing returns the id');
assert.equal(store.read(), 42, 'the Studio and sheet read the same session');
assert.equal(storage.getItem('studio:session:7:3'), '42');
assert.equal(store.write('43'), 43, 'a numeric string is accepted');
store.write(null);
assert.equal(store.read(), null, 'New chat clears it');
assert.equal(storage.getItem('studio:session:7:3'), null);

storage.setItem('studio:session:7:3', 'not a number');
assert.equal(store.read(), null, 'corrupt storage is empty, not a crash');
storage.setItem('studio:session:7:3', '0');
assert.equal(store.read(), null, 'zero is not a session');

const other = load({ user: { id: 3, org: { id: 8 } } }, storage);
assert.equal(other.key(), 'studio:session:8:3', 'another client, another key');
const operator = load({ user: { id: 1, org: null } }, storage);
assert.equal(operator.key(), 'studio:session:op:1', 'the operator with no client uses op');
const acting = load({ user: { id: 1, org: null, acting_org: 5 } }, storage);
assert.equal(acting.key(), 'studio:session:5:1',
             'the operator is scoped to the acting client');

// The floating sheet and the Studio both use these helpers, not their own arrays.
const orb = source.slice(source.indexOf('  function assistOrb() {'),
                         source.indexOf('  // ---------- A first-timer', source.indexOf('  function assistOrb() {')));
assert.ok(orb.includes('ensureChatSession()'), 'the sheet ensures the shared session');
assert.ok(orb.includes('session_id: sessionId'), 'the sheet posts the session id');
assert.ok(orb.includes("context: where"), 'the screen context is a field, not the message');
assert.ok(!orb.includes('history: history.slice'), 'the sheet no longer sends a local history');
assert.ok(orb.includes('loadThread()'), 'opening the sheet restores the thread');
assert.ok(source.includes('var sessionId = readStudioSession();'),
          'the Studio starts from the shared session');
assert.ok(source.includes('saveSessionId(stored)'), 'the Studio adopts a session the sheet made');
assert.ok(source.includes("function startNewChat()"), 'the Studio has the explicit new chat');

console.log('STUDIO SESSION OK - one key, one active thread, cleared only by New chat');
