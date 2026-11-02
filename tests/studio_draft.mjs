/* The Studio remembers which draft it is editing, without a browser.
 *
 * Run: node tests/studio_draft.mjs
 *
 * The view owns `activeArtifact` only while it is mounted. This store is what survives a
 * tab switch, a reload and a closed voice - if it is wrong, the voice edits the wrong
 * page or creates a duplicate beside it. admin.js is one IIFE, so this reads the real
 * functions out of the file and evaluates them with a fake state and localStorage.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');
const start = source.indexOf('  function studioDraftKey() {');
const end = source.indexOf('  // ---------- Studio: chats on the left', start);
assert.ok(start > 0 && end > start, 'the draft store is in admin.js');
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
    'return { key: studioDraftKey, read: readStudioDraft, write: writeStudioDraft };';
  const factory = new Function('state', 'localStorage', body);
  return factory(state, storage);
}

const user = { id: 3, org: { id: 7 } };
let storage = makeStorage();
let store = load({ user }, storage);

assert.equal(store.key(), 'studio:draft:7:3', 'scoped by client and user');
assert.equal(store.read(), null, 'nothing remembered yet');

store.write({ id: 42 });
assert.deepEqual(store.read(), { kind: 'deliverable', id: 42 });
assert.equal(storage.getItem('studio:draft:7:3'), '{"kind":"deliverable","id":42}');

store.write({ id: 42 });
assert.equal(store.read().id, 42, 'rewriting the same draft is harmless');

store.write(null);
assert.equal(store.read(), null, 'clearing removes it');
assert.equal(storage.getItem('studio:draft:7:3'), null);

storage.setItem('studio:draft:7:3', 'not json');
assert.equal(store.read(), null, 'corrupt JSON is null, not a crash');
storage.setItem('studio:draft:7:3', '{"id":"42"}');
assert.equal(store.read().id, 42, 'a numeric string is accepted');
storage.setItem('studio:draft:7:3', '{"id":0}');
assert.equal(store.read(), null, 'zero is not a draft');
store.write({ id: 0 });
assert.equal(storage.getItem('studio:draft:7:3'), null, 'writing zero clears');

store.write({ id: 42 });
const other = load({ user: { id: 3, org: { id: 8 } } }, storage);
assert.equal(other.key(), 'studio:draft:8:3', 'another client, another key');
assert.equal(other.read(), null);
other.write({ id: 99 });
assert.equal(store.read().id, 42, 'and it cannot overwrite the first one');

const operator = load({ user: { id: 1, org: null } }, storage);
assert.equal(operator.key(), 'studio:draft:op:1', 'the operator with no client uses op');

const broken = {
  getItem() { throw new Error('denied'); },
  setItem() { throw new Error('denied'); },
  removeItem() { throw new Error('denied'); }
};
const safe = load({ user }, broken);
assert.equal(safe.read(), null, 'private mode reads as nothing');
safe.write({ id: 5 });   // must not throw

console.log('STUDIO DRAFT OK - per user and client, cleared on New, quiet in private mode');
