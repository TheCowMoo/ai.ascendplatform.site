/* A domain row's provisioning state, without a browser.
 *
 * Run: node tests/domains_state.mjs
 *
 * "queued" and "failed" are the two states a user needs to tell apart; a row that shows
 * neither looks identical whether the run is working or died. This evaluates the real
 * provisionState from admin.js and checks the strings, plus the wiring that feeds it.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');
const start = source.indexOf('  function provisionState(job) {');
const end = source.indexOf('  // End provisionState.');
assert.ok(start > 0 && end > start, 'provisionState is in admin.js');
const provisionState = new Function(source.slice(start, end) + '; return provisionState;')();

assert.equal(provisionState(null), '', 'no job, no claim');
assert.equal(provisionState({}), '', 'an unknown status says nothing');
assert.equal(provisionState({ status: 'queued' }), 'provisioning queued');
assert.equal(provisionState({ status: 'running' }), 'provisioning running');
assert.equal(provisionState({ status: 'failed' }), 'last provision failed');
assert.equal(provisionState({ status: 'error' }), 'last provision failed');
assert.equal(provisionState({ status: 'done' }), '', 'a finished run is not "provisioning"');

// The row is only as good as the data behind it.
assert.ok(source.includes("api('GET', '/api/jobs')"), 'the tab reads the job list');
assert.ok(source.includes('row(site, latest[key])'), 'the row is given its run');
assert.ok(source.includes("'View job'"), 'a queued run can be opened');
assert.ok(source.includes('Dry-run: DNS checks and certificates'),
  'the tab says when provisioning is simulated');
assert.ok(source.includes('ERR_SSL_PROTOCOL_ERROR'),
  'the panel explains the failure before provisioning');

console.log('DOMAINS STATE OK - queued, running and failed are distinguishable');