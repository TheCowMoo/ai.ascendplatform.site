/* The credit bar under the username, driven without a browser.
 *
 * Run: node tests/credit_bar.mjs
 *
 * `creditMeter` is the only place the sidebar turns a balance into a width, and every
 * way it can be wrong is silent: a 5000-credit allowance drawn as 100%, a bar shown for
 * the operator on "All clients", an overdraft painted green, a request per tab click.
 * admin.js is one IIFE that boots the whole shell, so this reads the real function's
 * source out of the file and evaluates just that, with fakes for `api` and `fmtCredits`.
 * Nothing here duplicates the logic under test - it is the shipped code.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');
const start = source.indexOf('function creditMeter(node) {');
assert.ok(start > 0, 'creditMeter is in admin.js');
let index = source.indexOf('{', start);
let depth = 0;
let end = -1;
for (; index < source.length; index += 1) {
  if (source[index] === '{') depth += 1;
  else if (source[index] === '}') {
    depth -= 1;
    if (!depth) { end = index + 1; break; }
  }
}
assert.ok(end > start, 'creditMeter has balanced braces');
const code = source.slice(start, end);

// The real formatter: three decimals, thousands separators. The width never depends on it.
function fmtCredits(value) {
  if (value === null || value === undefined) return '-';
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function makeNode() {
  const classes = new Set(['side-credit']);
  const trackClasses = new Set(['side-credit-track', 'hidden']);
  const children = {
    '.side-credit-value': { textContent: '…' },
    '.side-credit-track': {
      attrs: {},
      classList: {
        add: (name) => trackClasses.add(name),
        remove: (name) => trackClasses.delete(name),
        contains: (name) => trackClasses.has(name)
      },
      setAttribute(name, value) { this.attrs[name] = value; },
      removeAttribute(name) { delete this.attrs[name]; }
    },
    '.side-credit-fill': { style: { width: '' } }
  };
  return {
    hidden: true,
    title: '',
    classList: {
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      contains: (name) => classes.has(name)
    },
    querySelector: (selector) => children[selector] || null,
    children
  };
}

// A fresh copy of the module state (the 15-second cache lives beside the function).
function load(api) {
  const body = 'var creditCache = { at: 0, data: null };\n' + code +
    '\nreturn creditMeter;';
  return new Function('api', 'fmtCredits', body)(api, fmtCredits);
}

const tick = () => new Promise((resolve) => { setTimeout(resolve, 0); });

// ---------- A client with 1,240 of a 5,000 allowance ----------
let node = makeNode();
let calls = 0;
let creditMeter = load(() => {
  calls += 1;
  return Promise.resolve({ balance: 1240, allowance: 5000, plan: 'growth', low: false });
});
creditMeter(node);
await tick();
assert.equal(calls, 1, 'the first render asks once');
assert.equal(node.hidden, false, 'a balance is shown');
assert.match(node.children['.side-credit-value'].textContent, /1,240 \/ 5,000 credits/);
assert.equal(node.children['.side-credit-fill'].style.width, '24.8%',
             'the fill is the balance over the allowance');
assert.equal(node.children['.side-credit-track'].classList.contains('hidden'), false);
assert.equal(node.children['.side-credit-track'].attrs['aria-valuemax'], '5000');
assert.equal(node.children['.side-credit-track'].attrs['aria-valuenow'], '1240');
assert.equal(node.classList.contains('low'), false, 'plenty left is not red');

// ---------- The 15-second cache ----------
node = makeNode();
creditMeter(node);
await tick();
assert.equal(calls, 1, 'a second render inside the window does not refetch');
assert.equal(node.hidden, false, 'and it still paints');

// ---------- Low balance ----------
node = makeNode();
creditMeter = load(() => Promise.resolve({ balance: 3, allowance: 5000, low: true }));
creditMeter(node);
await tick();
assert.equal(node.classList.contains('low'), true, 'low water is red');
assert.equal(node.children['.side-credit-fill'].style.width, '0.06%');

// ---------- No allowance: the number is the whole story ----------
node = makeNode();
creditMeter = load(() => Promise.resolve({ balance: 12, allowance: 0, low: false }));
creditMeter(node);
await tick();
assert.equal(node.hidden, false);
assert.match(node.children['.side-credit-value'].textContent, /^12 credits$/);
assert.equal(node.children['.side-credit-track'].classList.contains('hidden'), true,
             'no empty track implying a budget');
assert.equal('aria-valuenow' in node.children['.side-credit-track'].attrs, false);

// ---------- A top-up above the allowance, and an overdraft ----------
node = makeNode();
creditMeter = load(() => Promise.resolve({ balance: 9000, allowance: 5000 }));
creditMeter(node);
await tick();
assert.equal(node.children['.side-credit-fill'].style.width, '100%', 'never past full');

node = makeNode();
creditMeter = load(() => Promise.resolve({ balance: -12, allowance: 5000 }));
creditMeter(node);
await tick();
assert.equal(node.children['.side-credit-fill'].style.width, '0%', 'never below empty');
assert.equal(node.classList.contains('low'), true, 'an overdraft is red');
assert.match(node.children['.side-credit-value'].textContent, /^-12 \/ 5,000 credits$/);

// ---------- Nothing honest to draw ----------
for (const payload of [null, { balance: null, allowance: 0 }]) {
  node = makeNode();
  creditMeter = load(() => Promise.resolve(payload));
  creditMeter(node);
  await tick();
  assert.equal(node.hidden, true, 'no ledger row, or "All clients"');
}
node = makeNode();
creditMeter = load(() => Promise.reject(new Error('400')));
creditMeter(node);
await tick();
assert.equal(node.hidden, true, 'a broken meter hides the bar, never the shell');

console.log('CREDIT BAR OK - percent, allowance fallback, low state, cache and the hidden paths');
