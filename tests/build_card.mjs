/* The guided-build card is the non-technical path; these checks keep it that way.
 *
 * Run: node tests/build_card.mjs
 *
 * It is a static harness: the card is plain DOM and its API contract is tested in
 * test_build_api.py. What matters here is that the four choices exist, the question
 * controls and review are present, and the raw draft editor never appears in this path.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');
const manager = fs.readFileSync(new URL('../manager.py', import.meta.url), 'utf8');
const tool = fs.readFileSync(new URL('../tools/manager.py', import.meta.url), 'utf8');
const css = fs.readFileSync(new URL('../admin/admin.css', import.meta.url), 'utf8');

for (const label of ['Website', 'Blog post', 'Sign-up form', 'Newsletter']) {
  assert.ok(source.includes("'" + label + "'"), `the picker offers ${label}`);
}
assert.ok(source.includes('function openBuildPicker'), 'there is a Create picker');
assert.ok(source.includes('Create something'), 'the Studio has a Create button');
assert.ok(source.includes('Create with AI'), 'the Content tabs default to the guide');
assert.ok(source.includes('function showBuildCard'), 'the card renderer exists');
assert.ok(source.includes('function buildQuestion'), 'question controls exist');
assert.ok(source.includes("build.status === 'asking'"), 'the card paints questions');
assert.ok(source.includes("build.status === 'ready'"), 'the card paints a review');
assert.ok(source.includes('summary.will_build'), 'the review says what will be built');
assert.ok(source.includes('function paintResult'), 'the card paints a result');
assert.ok(source.includes("api('POST', '/api/builds'"), 'the picker starts a build');
assert.ok(source.includes('/answers'), 'answers go to the saved session');
assert.ok(source.includes("/build'"), 'the review can start the build');
assert.ok(source.includes('type === \'choice\''), 'choices are rendered');
assert.ok(source.includes('type === \'multi\''), 'multiple choices are rendered');
assert.ok(source.split("ev.type === 'build'").length - 1 >= 2,
  'the Studio and the Ask sheet both follow a build event');
assert.ok(source.includes('build-required'), 'required questions are marked');
assert.ok(source.includes('Change an answer'), 'the review can be edited');
assert.ok(source.includes('Publish'), 'a built page can be published from the result');
assert.ok(source.includes('Publish site'), 'a multi-page site publishes in one action');
assert.ok(source.includes('Refresh blog page'), 'the blog index can be refreshed after a post');

const start = source.indexOf('function showBuildCard');
const end = source.indexOf('function buildQuestion');
assert.ok(start > 0 && end > start, 'the card source is identifiable');
const card = source.slice(start, end);
for (const forbidden of ['showDeliverableForm', 'Published URL', 'Content', 'Slug']) {
  assert.ok(!card.includes(forbidden), `the guided card does not expose ${forbidden}`);
}

assert.ok(manager.includes('start_build instead of interviewing'),
          'the Manager is told to use the guide');
assert.ok(tool.includes('register("start_build"'), 'the tool is registered');
assert.ok(css.includes('.build-choices'), 'choice styling exists');
assert.ok(css.includes('.build-review'), 'review styling exists');
assert.ok(css.includes('.build-progress'), 'progress styling exists');

console.log('BUILD CARD OK - four simple choices, questions, review, no raw editor');
