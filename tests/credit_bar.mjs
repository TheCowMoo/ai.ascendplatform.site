/* The usage bar under the username, driven without a browser.
 *
 * Run: node tests/credit_bar.mjs
 *
 * creditMeter turns "credits used this month" and "the plan credits" into a width, and
 * every way it can be wrong is silent: an allowance drawn at 0%, usage past 100% hidden,
 * the operator All clients state showing a stray empty bar. admin.js is one IIFE that
 * boots the shell, so this reads the real function out and evaluates it with fakes for
 * api and fmtCredits.
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

function fmtCredits(value) {
  if (value === null || value === undefined) return '-';
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function makeNode() {
  const classes = new Set(['side-credit']);
  const trackClasses = new Set(['side-credit-track', 'hidden']);
  const children = {
    '.side-credit-value': { textContent: '...' },
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
      remove(name) { classes.delete(name); },
      contains: (name) => classes.has(name)
    },
    querySelector: (selector) => children[selector] || null,
    children
  };
}

function load(api) {
  const body = 'var creditCache = { at: 0, data: null };' + String.fromCharCode(10) + code +
    String.fromCharCode(10) + 'return creditMeter;';
  return new Function('api', 'fmtCredits', body)(api, fmtCredits);
}

const tick = () => new Promise((resolve) => { setTimeout(resolve, 0); });
const label = (node) => node.children['.side-credit-value'].textContent;

// ---------- 1,240 used of a 5,000 plan ----------
let node = makeNode();
let calls = 0;
let meter = load(() => {
  calls += 1;
  return Promise.resolve({ balance: 3760, allowance: 5000, spent: 1240, added: 5000,
                           plan: 'growth', low: false });
});
meter(node);
await tick();
assert.equal(calls, 1);
assert.equal(node.hidden, false);
assert.match(label(node), /1,240 \/ 5,000 credits used/);
assert.equal(node.children['.side-credit-fill'].style.width, '24.8%');
assert.equal(node.children['.side-credit-track'].classList.contains('hidden'), false);
assert.equal(node.children['.side-credit-track'].attrs['aria-valuemax'], '5000');
assert.equal(node.children['.side-credit-track'].attrs['aria-valuenow'], '1240');
assert.equal(node.classList.contains('low'), false);
assert.match(node.title, /1,240 credits used this month of 5,000 on the growth plan/);
assert.match(node.title, /3,760 left/);

// ---------- the 15-second cache ----------
node = makeNode();
meter(node);
await tick();
assert.equal(calls, 1, 'a second render inside the window does not refetch');
assert.equal(node.hidden, false);

// ---------- the allowance is used up ----------
node = makeNode();
meter = load(() => Promise.resolve({ balance: -200, allowance: 5000, spent: 5200 }));
meter(node);
await tick();
assert.equal(node.children['.side-credit-fill'].style.width, '100%');
assert.equal(node.classList.contains('low'), true, 'past the plan is red');
assert.match(label(node), /5,200 \/ 5,000 credits used/);

// ---------- a low balance under budget is still a warning ----------
node = makeNode();
meter = load(() => Promise.resolve({ balance: 2, allowance: 5000, spent: 100, low: true }));
meter(node);
await tick();
assert.equal(node.classList.contains('low'), true);

// ---------- no plan: the month additions are the budget ----------
node = makeNode();
meter = load(() => Promise.resolve({ balance: 3760, allowance: 0, spent: 1240, added: 5000 }));
meter(node);
await tick();
assert.match(label(node), /1,240 \/ 5,000 credits used/);
assert.equal(node.children['.side-credit-fill'].style.width, '24.8%');

// ---------- no plan and nothing added: usage alone ----------
node = makeNode();
meter = load(() => Promise.resolve({ balance: 0, allowance: 0, spent: 120, added: 0 }));
meter(node);
await tick();
assert.match(label(node), /^120 credits used this month$/);
assert.equal(node.children['.side-credit-track'].classList.contains('hidden'), true);
assert.equal('aria-valuenow' in node.children['.side-credit-track'].attrs, false);

// ---------- a fresh month on a plan is an empty bar, not a missing one ----------
node = makeNode();
meter = load(() => Promise.resolve({ balance: 5000, allowance: 5000, spent: 0 }));
meter(node);
await tick();
assert.equal(node.hidden, false);
assert.equal(node.children['.side-credit-fill'].style.width, '0%');

// ---------- not on the credit system ----------
node = makeNode();
meter = load(() => Promise.resolve({ balance: null, allowance: 0 }));
meter(node);
await tick();
assert.equal(node.hidden, true, 'no ledger row hides the bar');

// ---------- the operator on All clients ----------
node = makeNode();
meter = load(() => Promise.resolve({ balance: null, orgs: [{ id: 1 }], credits: 120 }));
meter(node);
await tick();
assert.equal(node.hidden, false);
assert.match(label(node), /Usage . pick a client/);
assert.equal(node.children['.side-credit-track'].classList.contains('hidden'), true);
assert.equal(node.classList.contains('low'), false, 'a hint is not a warning');

// ---------- a failed request hides it ----------
node = makeNode();
meter = load(() => Promise.reject(new Error('400')));
meter(node);
await tick();
assert.equal(node.hidden, true, 'a broken meter hides the bar, never the shell');

console.log('CREDIT BAR OK - usage vs the plan, the fallbacks, the operator hint and the cache');
