/* The publish and history controls, without a browser.
 *
 * Run: node tests/publish_state.mjs
 *
 * The UI is part of the guard: if it stops sending as_home, offering the root choice,
 * showing draft changes, or exposing History/Restore, the server-side protections are
 * still real but the person using the console is back to guessing. This checks the real
 * wiring in admin.js, and that one click still means one POST.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');

assert.ok(source.includes("name: 'where'"), 'the publish dialog asks where the page goes');
assert.ok(source.includes("label: 'Home page (/) - the site front page'"),
  'the home-page choice is named');
assert.ok(source.includes('Subpage ('), 'the exact subpage path is shown');
assert.ok(source.includes('as_home: !!asHome'), 'the console sends the chosen target');
assert.ok(source.includes('replace: true'),
  'the dialog is the confirmation for replacing a live page');
assert.ok(source.includes('This site is ready'),
  'a starter root defaults the choice to the home page');
assert.ok(source.includes('draft changes'),
  'a draft with a live URL is not labelled published');
assert.ok(source.includes('Unpublished changes'),
  'the preview says the live page is the previous version');
assert.ok(source.includes('function showDeliverableHistory'),
  'History is reachable from the preview');
assert.ok(source.includes("/versions/' + version.id + '/restore'"),
  'Restore calls the version endpoint');
assert.ok(source.includes('if (isNew || (item.kind !== \'website\' && kind !== \'website\'))'),
  'a website edit cannot set published without publishing');
assert.equal(source.split("'/publish'").length - 1, 1,
  'one click still has one publish call site');

console.log('PUBLISH STATE OK - home/subpage is explicit, drafts are labelled, history is reachable');
