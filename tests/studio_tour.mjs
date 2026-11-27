/* The four-stop Studio walkthrough is the onboarding contract.
 *
 * Run: node tests/studio_tour.mjs
 *
 * Static harness: the real behavior is in admin.js and the API completion flag is covered
 * by smoke_onboarding.py. This keeps the four anchors, the completion card and the replay
 * path from disappearing in a UI edit.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../admin/admin.js', import.meta.url), 'utf8');
const app = fs.readFileSync(new URL('../app.py', import.meta.url), 'utf8');
const css = fs.readFileSync(new URL('../admin/admin.css', import.meta.url), 'utf8');

for (const title of [
  'Start with a plain request',
  'Name the page',
  'Watch the team work',
  'Review the result'
]) {
  assert.ok(source.includes("title: '" + title + "'"), `walkthrough step: ${title}`);
}

for (const target of [
  'studio-composer', 'studio-working-on', 'studio-live', 'studio-preview',
  'studio-chats', 'studio-create'
]) {
  assert.ok(source.includes(`data-tour', '${target}'`),
            `stable Studio hook: ${target}`);
}
for (const target of [
  'studio-composer', 'studio-working-on', 'studio-live', 'studio-preview'
]) {
  assert.ok(source.includes(`[data-tour="${target}"]`),
            `walkthrough target: ${target}`);
}

assert.ok(source.includes('function studioTourSteps'), 'Studio steps are data');
assert.ok(source.includes('function runTour'), 'there is a shared anchored tour runner');
assert.ok(source.includes('function startStudioTour'), 'the Studio walkthrough starts');
assert.ok(source.includes('function studioTourCurrent'), 'completion is version checked');
assert.ok(source.includes('function markStudioTour'), 'completion is recorded');
assert.ok(source.includes('function showStudioTourComplete'), 'there is a completion card');
assert.ok(source.includes("'How Studio works'"), 'the Studio has a replay button');
assert.ok(source.includes("'Show me the rest'"), 'the completion card offers the wider tour');
assert.ok(source.includes('skipStudio: true'), 'the wider tour skips the Studio repeat');
assert.ok(source.includes("target.classList.add('tour-focus')"), 'targets are highlighted');
assert.ok(source.includes('waitForTarget'), 'the tour waits for a rendered target');
assert.ok(source.includes("'Back'"), 'the walkthrough can go back');
assert.ok(source.includes('/api/auth/studio-tour'), 'completion is server-backed');
assert.ok(app.includes('STUDIO_TOUR_VERSION'), 'server owns the tour version');
assert.ok(css.includes('.tour-focus'), 'the spotlight style exists');
assert.ok(css.includes('.tour-card-anchored'), 'the anchored card style exists');

console.log('STUDIO TOUR OK - four anchors, completion card, replay and server version');