"""The automation graph is a canvas, not a thumbnail.

Twelve steps used to be squashed into a 320px box, because graphSvg() always fits the whole
graph into the panel width. These checks pin the three things that fix it - a zoom control, a
full-screen mode, and a redraw that puts the view back - without a browser: they read the
source, so a later refactor cannot quietly drop the canvas.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "admin", "admin.css"), encoding="utf-8").read()

FAILED = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILED.append(name)


def main():
    # The canvas itself.
    check("graphShell exists", "function graphShell(" in JS)
    check("zoom drives the viewBox, not a CSS transform",
          "node.setAttribute('viewBox', [view.x, view.y, w, h].join(' '))" in JS)
    check("zoom is anchored at the pointer", "function zoomTo(k, clientX, clientY)" in JS)
    check("zoom is clamped", "Math.max(1, Math.min(MAX_K, k))" in JS)
    check("a redraw re-applies the view",
          "a redraw resets the viewBox to content bounds; put the view back" in JS)
    check("wheel zooms", "'wheel'" in JS and "passive: false" in JS)
    check("pinch zooms", "function begin()" in JS and "Math.hypot(" in JS)
    check("pan moves the view", "view.x = drag.vx -" in JS)
    check("double click fits", "'dblclick'" in JS)
    check("keyboard zoom and escape", "ev.key === 'Escape'" in JS and "ev.key === '0'" in JS)

    # Both call sites use the shell, so a draft can be inspected before it is applied.
    check("the editor uses the canvas", "steps: steps, layout: layout, states: nodeStates" in JS)
    check("the draft preview uses the canvas", "steps: draft.steps || [], layout: layout" in JS)
    check("node dragging survives zoom", "attachDrag(canvas, state.layout || {}, draw);" in JS)

    # The full-screen surface and its toolbar.
    check("full screen class exists in JS", "wrap.classList.toggle('is-full', on)" in JS)
    check("full screen is styled", ".auto-graph.is-full {" in CSS)
    check("full screen lifts the 320px cap",
          ".auto-graph.is-full .auto-svg { height: 100%; max-height: none; }" in CSS)
    check("page scroll is locked while expanded", "body.graph-full { overflow: hidden; }" in CSS)
    check("the toolbar floats over the canvas", ".auto-zoom {" in CSS and "position: absolute" in CSS)
    check("the panel is wider than the old 520px",
          "width: min(760px, 100%)" in CSS and "width: min(520px, 100%)" not in CSS)

    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nGRAPH CANVAS OK - zoom, pan, pinch and full screen are wired to both graphs")


if __name__ == "__main__":
    main()
