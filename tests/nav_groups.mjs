/* The grouped sidebar, without a browser.
 *
 * Run: node tests/nav_groups.mjs
 *
 * The nav is data. A group that drops a child, a duplicated hash or a one-child heading
 * is invisible until someone cannot reach a page, so this evaluates the real AREAS
 * literal and the real flattenNav/groupFor from admin.js and checks the shape.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');
const areStart = source.indexOf('  var AREAS = {');
const areEnd = source.indexOf('  // Flattened leaves from one nav', areStart);
assert.ok(areStart > 0 && areEnd > areStart, 'AREAS is in admin.js');
const fnStart = source.indexOf('  function flattenNav(nav) {');
const fnEnd = source.indexOf('  // Which groups are open is per user', fnStart);
assert.ok(fnStart > 0 && fnEnd > fnStart, 'the nav helpers are in admin.js');

const code = source.slice(areStart, areEnd) + String.fromCharCode(10) +
  source.slice(fnStart, fnEnd) +
  String.fromCharCode(10) +
  'return { AREAS: AREAS, flattenNav: flattenNav, groupFor: groupFor };';
const helpers = new Function(code)();
const AREAS = helpers.AREAS;
const flattenNav = helpers.flattenNav;
const groupFor = helpers.groupFor;

const app = AREAS.app.nav;
assert.equal(app.length, 7, 'seven rows in the workspace');
assert.equal(app[0].id, 'studio', 'the Studio is first');
assert.equal(app[0].children, undefined, 'the Studio stays a leaf');
assert.equal(new Set(app.map((item) => item.id)).size, 7, 'row ids are unique');

app.filter((item) => item.children).forEach((group) => {
  assert.ok(group.icon, group.id + ' needs an icon');
  assert.ok(group.children.length > 1, group.id + ' is not a one-child group');
  group.children.forEach((child) => {
    assert.ok(child.hash && child.hash.indexOf('#/app/') === 0, child.id + ' has an app hash');
  });
});

const leaves = flattenNav(app);
assert.equal(leaves.length, 20, 'every old tab is still reachable');
assert.equal(new Set(leaves.map((leaf) => leaf.id)).size, 20, 'leaf ids are unique');
assert.equal(new Set(leaves.map((leaf) => leaf.hash)).size, 20, 'no duplicate hashes');

const sites = app.find((item) => item.id === 'sites-group');
assert.deepEqual(sites.children.map((child) => child.id),
  ['sites', 'domains', 'funnels', 'blogs', 'emails', 'library'], 'Sites holds every page-like tab');
assert.equal(groupFor(app, 'emails'), sites, 'a child finds its group');
assert.equal(groupFor(app, 'studio'), null, 'a leaf belongs to no group');
assert.equal(groupFor(app, 'nope'), null, 'an unknown tab belongs to no group');

const customers = app.find((item) => item.id === 'customers');
assert.deepEqual(customers.children.map((child) => child.id),
  ['contacts', 'forms', 'conversations', 'bots']);

const account = app.find((item) => item.id === 'account');
assert.deepEqual(account.children.map((child) => child.id), ['profile', 'team', 'settings']);
assert.equal(account.children.filter((child) => child.adminOnly).length, 2,
             'Team and Settings stay admin-only');

assert.equal(AREAS.platform.nav.length, 8, 'the console stays flat');
assert.equal(flattenNav(AREAS.platform.nav).length, 8);
assert.equal(AREAS.platform.nav.filter((item) => item.id === 'domains').length, 1,
             'the owner-wide Domains page is one click away');

console.log('NAV GROUPS OK - seven rows, twenty destinations, no broken hashes');
