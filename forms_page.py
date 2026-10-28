"""The visitor-facing page for a form, and the payload that feeds it.

Two rules shape this file:

* **Scoring never leaves the server.** ``public_question`` strips ``score`` from a question,
  so a quiz's correct answers are not sitting in the HTML for anyone who opens dev tools.
* **The browser does not decide what comes next.** The page asks the server for the next
  page of questions (``payload_for`` runs the same engine the submit endpoint uses), so
  branching has one implementation and cannot be edited in a browser before submitting.
"""

import html
import json

import forms_engine


def _filled(value):
    if isinstance(value, list):
        return bool([item for item in value if str(item).strip()])
    return bool(str(value if value is not None else "").strip())


def public_question(question):
    """One question, with everything a visitor is allowed to see."""
    kind = str(question.get("type") or "text").strip().lower()
    try:
        top = int(question.get("max") or 5)
    except (TypeError, ValueError):
        top = 5
    return {
        "id": question["id"],
        "label": str(question.get("label") or question["id"]),
        "type": kind,
        "required": bool(question.get("required")),
        "options": [opt["label"] for opt in forms_engine.options_of(question)],
        "max": max(1, min(10, top)),
        "help": str(question.get("help") or ""),
    }


def payload_for(form, answers=None):
    """What the page should show next, given the answers so far.

    A "page" is a group of questions the visitor sees together: the walk says which
    questions are live, the first unanswered one decides which group to send, and the
    browser gets nothing it does not need yet.
    """
    answers = answers or {}
    shown = forms_engine.visible_questions(form, answers)
    pending = [q for q in shown if not _filled(answers.get(q["id"]))]
    settings = form.get("settings") or {}
    if not pending:
        return {
            "done": True,
            "questions": [],
            "kind": form.get("kind") or "form",
            "progress": {"step": len(shown), "total": len(shown)},
            "submit_label": "",
        }
    page = forms_engine.page_of(pending[0])
    batch = [q for q in pending if forms_engine.page_of(q) == page]
    return {
        "done": False,
        "questions": [public_question(q) for q in batch],
        "kind": form.get("kind") or "form",
        "progress": {"step": len(shown) - len(pending), "total": len(shown)},
        "submit_label": str(settings.get("submit_label") or "Send"),
    }


def summary(form, result):
    """What a visitor sees when they finish: a score for a quiz, a thank-you otherwise."""
    settings = form.get("settings") or {}
    bucket = result.get("outcome") or {}
    if bucket.get("message"):
        return {"message": bucket["message"], "label": bucket.get("label") or ""}
    if str(form.get("kind") or "") == "quiz":
        marks = result.get("score") or {}
        return {"message": "You scored %d out of %d." % (marks.get("score", 0),
                                                         marks.get("max_score", 0)),
                "label": ""}
    return {"message": str(settings.get("success")
                           or "Thanks - we have your answer."), "label": ""}


def _accent(value):
    text = str(value or "").strip()
    return text if len(text) == 7 and text.startswith("#") else "#4f46e5"


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--accent:__ACCENT__}
*{box-sizing:border-box}
body{margin:0;background:#f6f7fb;color:#111827;
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:32px 20px 64px}
.card{background:#fff;border-radius:16px;padding:28px;box-shadow:0 10px 30px rgba(15,23,42,.08)}
h1{font-size:22px;margin:0 0 6px}
.lede{color:#6b7280;margin:0 0 22px}
.q{margin-bottom:20px}
.q .label{display:block;font-weight:600;margin-bottom:8px}
.q .help{font-weight:400;color:#6b7280;font-size:14px;margin-top:4px}
input[type=text],input[type=email],input[type=tel],input[type=number],input[type=date],
textarea,select{width:100%;padding:11px 12px;border:1px solid #d1d5db;border-radius:10px;
  font:inherit;background:#fff}
textarea{min-height:96px}
.opts{display:flex;flex-direction:column;gap:8px}
.opt{display:flex;gap:10px;align-items:center;border:1px solid #e5e7eb;border-radius:10px;
  padding:10px 12px;cursor:pointer}
.opt input{accent-color:var(--accent)}
.rating{display:flex;gap:6px;flex-wrap:wrap}
.rating button{width:42px;height:42px;border-radius:10px;border:1px solid #d1d5db;
  background:#fff;font:inherit;cursor:pointer}
.rating button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between;margin-top:24px}
button.primary{background:var(--accent);color:#fff;border:0;border-radius:10px;
  padding:12px 20px;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:#fff;border:1px solid #d1d5db;border-radius:10px;padding:12px 18px;
  font:inherit;cursor:pointer}
.bar{height:6px;background:#e5e7eb;border-radius:99px;overflow:hidden;margin-bottom:22px}
.bar span{display:block;height:100%;background:var(--accent);transition:width .25s}
.error{color:#b91c1c;font-size:14px;min-height:20px;margin-top:10px}
.done{font-size:17px}
.hp{position:absolute;left:-9999px}
</style></head>
<body><main><div class="card" id="app">
<div class="bar"><span id="bar"></span></div>
<h1 id="title"></h1>
<p class="lede" id="lede"></p>
<form id="form" novalidate><div id="qs"></div>
<input class="hp" name="company" tabindex="-1" autocomplete="off" aria-hidden="true">
<div class="error" id="err"></div>
<div class="row"><button type="button" class="ghost" id="back">Back</button>
<button type="submit" class="primary" id="next">Next</button></div>
</form></div></main>
<script>
var STATE = __STATE__;
var answers = {}, history = [], current = null;
var qs = document.getElementById('qs'), err = document.getElementById('err');
var bar = document.getElementById('bar'), nextBtn = document.getElementById('next');
var backBtn = document.getElementById('back');

document.getElementById('title').textContent = STATE.name;
document.getElementById('lede').textContent = STATE.description || '';

function el(tag, cls, text) {
  var node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function field(question) {
  var wrap = el('div', 'q'), label = el('div', 'label', question.label);
  if (question.required) label.appendChild(el('span', null, ' *'));
  wrap.appendChild(label);
  var id = 'q_' + question.id, node;
  if (question.type === 'textarea') {
    node = el('textarea'); node.id = id;
  } else if (question.type === 'select') {
    node = el('select'); node.id = id;
    node.appendChild(el('option', null, 'Choose…'));
    question.options.forEach(function (opt) {
      var item = el('option', null, opt); item.value = opt; node.appendChild(item);
    });
  } else if (question.type === 'radio' || question.type === 'checkbox') {
    node = el('div', 'opts'); node.id = id;
    question.options.forEach(function (opt) {
      var line = el('label', 'opt'), input = el('input');
      input.type = question.type === 'radio' ? 'radio' : 'checkbox';
      input.name = id; input.value = opt;
      line.appendChild(input); line.appendChild(el('span', null, opt));
      node.appendChild(line);
    });
  } else if (question.type === 'rating') {
    node = el('div', 'rating'); node.id = id;
    for (var score = 1; score <= question.max; score++) {
      (function (value) {
        var button = el('button', null, String(value));
        button.type = 'button';
        button.onclick = function () {
          Array.prototype.forEach.call(node.querySelectorAll('button'), function (other) {
            other.classList.toggle('on', other === button);
          });
          answers[question.id] = value;
        };
        node.appendChild(button);
      })(score);
    }
  } else {
    node = el('input'); node.id = id;
    node.type = question.type === 'email' ? 'email'
      : question.type === 'phone' ? 'tel'
      : question.type === 'number' ? 'number'
      : question.type === 'date' ? 'date' : 'text';
  }
  wrap.appendChild(node);
  if (question.help) wrap.appendChild(el('div', 'help', question.help));
  return wrap;
}

function render(payload) {
  current = payload;
  STATE.questions = payload.questions || [];
  if (payload.progress) STATE.progress = payload.progress;
  if (payload.submit_label) STATE.submit_label = payload.submit_label;
  qs.innerHTML = '';
  STATE.questions.forEach(function (question) { qs.appendChild(field(question)); });
  var total = Math.max(1, STATE.progress.total || 1);
  var done = Math.min(total, STATE.progress.step || 0);
  bar.style.width = Math.round(100 * done / total) + '%';
  backBtn.style.visibility = history.length ? 'visible' : 'hidden';
  nextBtn.textContent = STATE.submit_label || 'Next';
  err.textContent = '';
}

function collect() {
  err.textContent = '';
  for (var i = 0; i < STATE.questions.length; i++) {
    var question = STATE.questions[i], id = 'q_' + question.id;
    var node = document.getElementById(id), value;
    if (question.type === 'radio') {
      var picked = qs.querySelector('input[name="' + id + '"]:checked');
      value = picked ? picked.value : '';
    } else if (question.type === 'checkbox') {
      value = Array.prototype.map.call(qs.querySelectorAll('input[name="' + id + '"]:checked'),
        function (input) { return input.value; });
    } else if (question.type === 'rating') {
      value = answers[question.id] || '';
    } else {
      value = node ? node.value : '';
    }
    answers[question.id] = value;
    var filled = Array.isArray(value) ? value.length > 0 : String(value).trim() !== '';
    if (question.required && !filled) {
      err.textContent = 'Please answer: ' + question.label;
      if (node && node.focus) node.focus();
      return false;
    }
  }
  return true;
}

function post(url, done) {
  var body = { answers: answers, company: document.querySelector('.hp input').value };
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify(body) })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) { throw new Error(data.error || 'Something went wrong'); }
        return data;
      });
    })
    .then(done)
    .catch(function (error) { err.textContent = error.message; });
}

function send() {
  history.push(JSON.parse(JSON.stringify(answers)));
  post(STATE.step, function (data) {
    if (data.done) { finish(); return; }
    render(data);
  });
}

function finish() {
  post(STATE.submit, function (data) {
    var app = document.getElementById('app');
    app.innerHTML = '';
    app.appendChild(el('h1', null, STATE.name));
    app.appendChild(el('p', 'done', data.message || 'Thanks - we have your answer.'));
    if (data.score) {
      app.appendChild(el('p', 'lede', data.score.score + ' / ' + data.score.max_score));
    }
  });
}

document.getElementById('form').addEventListener('submit', function (event) {
  event.preventDefault();
  if (collect()) send();
});
backBtn.onclick = function () {
  if (!history.length) return;
  answers = history.pop();
  render({ questions: STATE.questions, progress: STATE.progress });
  post(STATE.step, function (data) { if (data.questions) render(data); });
};

render(STATE);
</script></body></html>
"""


def page(form, payload, step_url, submit_url):
    """The whole visitor page, with its first step baked in.

    Name and description travel as text and are applied with textContent, so a client who
    calls a form ``<script>`` gets a title rather than an injection.
    """
    settings = form.get("settings") or {}
    state = {
        "name": str(form.get("name") or "Form"),
        "description": str(settings.get("description") or ""),
        "step": step_url,
        "submit": submit_url,
        "questions": payload.get("questions") or [],
        "progress": payload.get("progress") or {"step": 0, "total": 0},
        "submit_label": payload.get("submit_label") or "Send",
    }
    return (PAGE
            .replace("__ACCENT__", _accent(settings.get("brand")))
            .replace("__TITLE__", html.escape(state["name"]))
            .replace("__STATE__", json.dumps(state).replace("</", "<\\/")))

