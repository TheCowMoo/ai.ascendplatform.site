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

const website = app.find((item) => item.id === 'sites-group');
assert.deepEqual(website.children.map((child) => child.id),
  ['sites', 'domains'], 'Website holds the site and its domain');
assert.equal(website.label, 'Website');

const content = app.find((item) => item.id === 'content');
assert.deepEqual(content.children.map((child) => child.id),
  ['blogs', 'funnels', 'emails', 'library', 'media'],
  'Content holds every page-like tab');
assert.equal(groupFor(app, 'emails'), content, 'a child finds its group');
assert.equal(groupFor(app, 'studio'), null, 'a leaf belongs to no group');
assert.equal(groupFor(app, 'nope'), null, 'an unknown tab belongs to no group');
assert.ok(app.find((item) => item.id === 'automations').clientHidden,
          'Automations stays out of the default client nav');

const customers = app.find((item) => item.id === 'customers');
assert.deepEqual(customers.children.map((child) => child.id),
  ['contacts', 'forms', 'conversations', 'bots']);

const account = app.find((item) => item.id === 'account');
assert.deepEqual(account.children.map((child) => child.id), ['profile', 'team', 'settings']);
assert.equal(account.children.filter((child) => child.adminOnly).length, 2,
             'Team and Settings stay admin-only');

assert.equal(AREAS.platform.nav.length, 10, 'the console stays flat');
assert.equal(flattenNav(AREAS.platform.nav).length, 10);
assert.equal(AREAS.platform.nav.filter((item) => item.id === 'people').length, 1,
             'the agency-wide People directory is one click away');
assert.equal(AREAS.platform.nav.filter((item) => item.id === 'bugs').length, 1,
             'the agency bug tracker is one click away');
assert.equal(AREAS.platform.nav.filter((item) => item.id === 'domains').length, 1,
             'the owner-wide Domains page is one click away');

// The client workspace gets a Studio-first menu and one More disclosure. The operator
// keeps the grouped nav, so these checks are presentation contracts, not data changes.
assert.ok(source.includes('function simpleClientNav'), 'the client nav renderer exists');
assert.ok(source.includes("area === 'app' && !isOwner(state.user)"),
          'only non-owners get the simple workspace menu');
assert.ok(source.includes("data-simple-nav"), 'the simple nav is marked for styling');
assert.ok(source.includes("side-primary"), 'Studio is the primary client link');
assert.ok(source.includes("side-more"), 'the client menu has one disclosure');
assert.ok(source.includes("aria-expanded"), 'the disclosure is accessible');
assert.ok(source.includes("side-more-panel"), 'the disclosure has a panel');
assert.ok(source.includes("More · '"), 'the disclosure names the active page');
assert.ok(source.includes('visibleChildren(item)'), 'client links use the existing role filter');
assert.ok(source.includes('function sidebar(parts)'), 'the sidebar shell still exists');

console.log('NAV GROUPS OK - seven data rows, twenty destinations, Studio-first client menu');
