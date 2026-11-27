/* CMBGrowthAI — admin single-page app (no build step). */
(function () {
  'use strict';

  var state = { user: null };

  // Cleanup hooks for views that poll (cleared on every route change).
  var cleanups = [];
  function onCleanup(fn) { cleanups.push(fn); }
  function runCleanups() {
    cleanups.forEach(function (fn) { try { fn(); } catch (e) {} });
    cleanups = [];
  }

  // Overridden from /api/health at boot, so BRAND_NAME in the Settings tab is the
  // single source of truth and a rename needs no code change.
  var BRAND = 'AscendPlatform';

  // Two areas, not one long list. `app` is a client's workspace; `platform` is the
  // operator's console. Mixing them is how a client ended up with "Servers" in their
  // sidebar, so the area now decides the nav *and* the router gates on it.
  var AREAS = {
    app: {
      label: 'Workspace',
      home: '#/app/studio',
      nav: [
        // Seven rows instead of twenty. Items that belong together live under one
        // heading; every child keeps its old hash, so deep links, dashboard tiles, the
        // tour and the Studio links all land where they did.
        { id: 'studio', label: 'Studio', icon: 'studio', hash: '#/app/studio' },
        { id: 'overview', label: 'Home', icon: 'dashboard', children: [
          { id: 'dashboard', label: 'Dashboard', hash: '#/app/dashboard' },
          { id: 'analytics', label: 'Analytics', hash: '#/app/analytics' },
          { id: 'usage', label: 'Usage', hash: '#/app/usage' },
          { id: 'jobs', label: 'Jobs', hash: '#/app/jobs', ownerOnly: true }
        ] },
        { id: 'sites-group', label: 'Website', icon: 'globe', children: [
          { id: 'sites', label: 'Website', hash: '#/app/sites' },
          { id: 'domains', label: 'Domains', hash: '#/app/domains' }
        ] },
        { id: 'content', label: 'Content', icon: 'library', children: [
          { id: 'blogs', label: 'Blog posts', hash: '#/app/blogs' },
          { id: 'funnels', label: 'Landing pages', hash: '#/app/funnels' },
          { id: 'emails', label: 'Emails', hash: '#/app/emails' },
          { id: 'library', label: 'All content', hash: '#/app/library' },
          { id: 'media', label: 'Media', hash: '#/app/media' }
        ] },
        { id: 'customers', label: 'Leads', icon: 'user', children: [
          { id: 'contacts', label: 'Contacts', hash: '#/app/contacts' },
          // Forms list/responses are client_admin-scoped; do not show members a page
          // whose first API call would be a 403.
          { id: 'forms', label: 'Forms', hash: '#/app/forms', adminOnly: true },
          { id: 'conversations', label: 'Conversations', hash: '#/app/conversations' },
          { id: 'bots', label: 'Chatbots', hash: '#/app/bots' }
        ] },
        // Keep the route for anyone who bookmarked it, but do not put a graph editor in
        // front of a client who has not asked for automations.
        { id: 'automations', label: 'Automations', icon: 'cog',
          hash: '#/app/automations', clientHidden: true },
        { id: 'account', label: 'Account', icon: 'sliders', children: [
          { id: 'profile', label: 'Business details', hash: '#/app/profile' },
          { id: 'team', label: 'Team', hash: '#/app/team', adminOnly: true },
          { id: 'settings', label: 'Settings', clientLabel: 'Settings',
            hash: '#/app/settings', adminOnly: true }
        ] }
      ]
    },
    platform: {
      label: 'Platform',
      home: '#/platform/clients',
      nav: [
        { id: 'clients', label: 'Clients', icon: 'store', hash: '#/platform/clients' },
        { id: 'people', label: 'People', icon: 'user', hash: '#/platform/people' },
        { id: 'bugs', label: 'Bug tracker', icon: 'alert', hash: '#/platform/bugs' },
        { id: 'domains', label: 'Domains', icon: 'globe', hash: '#/platform/domains' },
        { id: 'usage', label: 'Usage', icon: 'credits', hash: '#/platform/usage' },
        { id: 'email', label: 'Email & invites', icon: 'mail', hash: '#/platform/email' },
        { id: 'sources', label: 'Data sources', icon: 'dashboard', hash: '#/platform/sources' },
        { id: 'servers', label: 'Servers', icon: 'server', hash: '#/platform/servers' },
        { id: 'agents', label: 'Agents', icon: 'cpu', hash: '#/platform/agents' },
        { id: 'settings', label: 'Platform settings', icon: 'sliders', hash: '#/platform/settings' }
      ]
    }
  };

  // Flattened leaves from one nav: a child is a first-class tab even though it is drawn
  // under a heading, so isSoonTab / viewSoon keep finding it.
  function flattenNav(nav) {
    var leaves = [];
    (nav || []).forEach(function (item) {
      if (item.children && item.children.length) {
        item.children.forEach(function (child) { leaves.push(child); });
      } else if (item.hash) {
        leaves.push(item);
      }
    });
    return leaves;
  }

  function groupFor(nav, tab) {
    for (var i = 0; i < (nav || []).length; i += 1) {
      var item = nav[i];
      if (!item.children) continue;
      for (var j = 0; j < item.children.length; j += 1) {
        if (item.children[j].id === tab) return item;
      }
    }
    return null;
  }

  function visibleChildren(item) {
    return (item.children || []).filter(function (child) {
      if (child.adminOnly && !canAdmin(state.user)) return false;
      if (child.ownerOnly && !isOwner(state.user)) return false;
      if (child.clientHidden && !isOwner(state.user)) return false;
      return true;
    });
  }

  // Which groups are open is per user and per browser: someone who works in Emails
  // should not have to open Sites on every visit.
  function navOpenKey() {
    return 'nav:open:' + ((state.user && state.user.id) || 0);
  }

  function readOpenGroups() {
    try {
      var ids = JSON.parse(localStorage.getItem(navOpenKey()) || '[]');
      return Object.prototype.toString.call(ids) === '[object Array]' ? ids : [];
    } catch (e) { return []; }
  }

  function writeOpenGroup(id, open) {
    var ids = readOpenGroups();
    var index = ids.indexOf(id);
    if (open && index === -1) ids.push(id);
    if (!open && index !== -1) ids.splice(index, 1);
    try { localStorage.setItem(navOpenKey(), JSON.stringify(ids)); }
    catch (e) { /* private mode: it still opens for this visit */ }
  }

  // Flat view of both areas, so the "coming soon" helper keeps working.
  var NAV = flattenNav(AREAS.app.nav).concat(flattenNav(AREAS.platform.nav));

  // Platform operator, or a client's own admin - the two levels that may manage
  // people and settings. Everything else is a member.
  function canAdmin(user) {
    if (!user) return false;
    return isOwner(user) || user.role === 'client_admin';
  }

  function api(method, url, body) {
    var opts = { method: method, credentials: 'same-origin', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        // A session that expired mid-page: send the user back to sign in. Without this the
        // console keeps painting an empty shell over a series of 401 toasts.
        if (res.status === 401 && url.indexOf('/api/auth/') !== 0) {
          sessionExpired();
        }
        if (!res.ok) {
          var e = new Error(data.error || ('HTTP ' + res.status));
          e.status = res.status;
          // Keep the body: a 502 from an invitation still carries the invite and its
          // link, so the console can recover instead of leaving the operator stuck.
          e.data = data;
          throw e;
        }
        return data;
      });
    });
  }

  // Icon reference from the sprite in index.html. `currentColor` means an icon
  // inherits the text colour, so the active nav item tints its own glyph.
  function icon(name, extra) {
    return '<svg class="icon' + (extra ? ' ' + extra : '') + '" aria-hidden="true"><use href="#i-' +
      name + '"/></svg>';
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // A relative form link is still useful in the console (it opens on whichever origin
  // served the page), but a link someone copies should be absolute.
  function absoluteLink(link) {
    return String(link || '').charAt(0) === '/' ? location.origin + link : String(link || '');
  }

  // Credits are stored in milli-credits; the console shows credits with three decimals.
  function fmtCredits(value) {
    if (value === null || value === undefined) return '-';
    var number = Number(value);
    if (!isFinite(number)) return String(value);
    return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // Which build is this? Fetched once per page load and shown in the sidebar and Settings,
  // so "I deployed but nothing changed" is answerable without guessing.
  var versionPromise = null;
  function buildInfo() {
    if (!versionPromise) {
      versionPromise = api('GET', '/api/version').catch(function () { return {}; });
    }
    return versionPromise;
  }

  function toast(msg, ok) {
    var t = el('div', 'toast ' + (ok ? 'toast-success' : 'toast-error'), msg);
    t.setAttribute('role', 'status');
    t.setAttribute('aria-live', 'polite');
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, ok ? 3000 : 6000);
  }

  // Escape closes the topmost modal. All ten of them are a .modal-backdrop with a Close
  // button, so one listener serves every call site without rewriting any of them.
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape') return;
    var open = document.querySelectorAll('.modal-backdrop');
    if (!open.length) return;
    ev.preventDefault();
    open[open.length - 1].remove();
  });

  // Called when the API says the session is gone. Guarded, because several requests can
  // fail at once and only one redirect is wanted.
  var expiring = false;
  function sessionExpired() {
    if (expiring) return;
    expiring = true;
    state.user = null;
    toast('Signed out - please sign in again.', false);
    if (location.hash !== '#/login') location.hash = '#/login';
    setTimeout(function () { expiring = false; }, 1500);
  }

  // ---------- Guided builds: plain questions, then a safe draft ----------
  // The card is shared by the Studio, the floating sheet and the Create button. The
  // server owns the questions and validation; this code only paints them.
  var BUILD_KINDS = [
    { value: 'website', label: 'Website', blurb: 'A simple one-page site with your services and contact details.' },
    { value: 'blog', label: 'Blog post', blurb: 'An article for your audience, ready to review and publish.' },
    { value: 'form', label: 'Sign-up form', blurb: 'Collect names and emails, and get a link to share.' },
    { value: 'newsletter', label: 'Newsletter', blurb: 'An email template, with an optional sign-up form.' }
  ];

  function buildCloseHost(host) {
    var backdrop = host.closest ? host.closest('.modal-backdrop') : null;
    if (backdrop && backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
  }

  function buildGo(hash, host) {
    buildCloseHost(host);
    location.hash = hash;
  }

  function openBuildPicker() {
    var backdrop = el('div', 'modal-backdrop');
    var panel = el('div', 'modal');
    var opened = false;
    backdrop.appendChild(panel);
    document.body.appendChild(backdrop);

    function showKinds() {
      panel.innerHTML = '';
      var close = el('button', 'btn btn-ghost btn-sm', '\u00d7');
      close.type = 'button';
      close.setAttribute('aria-label', 'Close');
      close.onclick = function () { backdrop.remove(); };
      panel.appendChild(close);
      var head = el('div', 'build-head');
      head.innerHTML = '<div class="build-kicker">Build with me</div>' +
        '<h3>What would you like to make?</h3>' +
        '<p class="muted">A few easy questions. You can skip what you are not sure about.</p>';
      panel.appendChild(head);
      var grid = el('div', 'build-kinds');
      BUILD_KINDS.forEach(function (kind) {
        var button = el('button', 'build-kind');
        button.type = 'button';
        button.innerHTML = '<strong>' + esc(kind.label) + '</strong>' +
          '<span>' + esc(kind.blurb) + '</span>';
        button.onclick = function () {
          opened = true;
          button.disabled = true;
          api('POST', '/api/builds', { kind: kind.value }).then(function (d) {
            panel.innerHTML = '';
            var x = el('button', 'btn btn-ghost btn-sm', '\u00d7');
            x.type = 'button';
            x.setAttribute('aria-label', 'Close');
            x.onclick = function () { backdrop.remove(); };
            panel.appendChild(x);
            showBuildCard(panel, d.build.id);
          }).catch(function (e) {
            button.disabled = false;
            toast(e.message, false);
          });
        };
        grid.appendChild(button);
      });
      panel.appendChild(grid);
    }

    api('GET', '/api/builds').then(function (d) {
      if (opened) return;
      var rows = (d.builds || []).filter(function (row) {
        return row.status !== 'cancelled';
      }).slice(0, 5);
      if (!rows.length) return;
      var resume = el('div', 'build-resume');
      resume.appendChild(el('div', 'build-kicker', 'Continue where you left off'));
      rows.forEach(function (row) {
        var button = el('button', 'build-resume-row');
        button.type = 'button';
        button.innerHTML = '<span><strong>' + esc(row.title || 'Untitled build') +
          '</strong><small>' + esc(row.status) + ' \u00b7 ' + (row.step + 1) + ' of ' +
          row.total_steps + '</small></span><span>Continue \u203a</span>';
        button.onclick = function () {
          opened = true;
          panel.innerHTML = '';
          var x = el('button', 'btn btn-ghost btn-sm', '\u00d7');
          x.type = 'button';
          x.setAttribute('aria-label', 'Close');
          x.onclick = function () { backdrop.remove(); };
          panel.appendChild(x);
          showBuildCard(panel, row.id);
        };
        resume.appendChild(button);
      });
      panel.appendChild(resume);
    }).catch(function () { /* the picker still works without the resume list */ });

    showKinds();
  }

  function showBuildCard(host, buildId) {
    var card = el('div', 'panel-card build-card');
    host.appendChild(card);
    var watching = false;

    function load(step) {
      var url = '/api/builds/' + buildId + (step == null ? '' : '?step=' + step);
      api('GET', url).then(function (d) {
        paint(d.build);
      }).catch(function (e) { toast(e.message, false); });
    }

    function paint(build) {
      card.innerHTML = '';
      var head = el('div', 'build-head');
      var progress = build.total_steps
        ? '<div class="build-progress"><span style="width:' +
          Math.round(100 * (build.progress.step / build.progress.total)) + '%"></span></div>'
        : '';
      head.innerHTML = '<div class="build-kicker">Build with me \u00b7 ' +
        esc(build.label || '') + '</div><h3>' + esc(build.title || 'Your build') +
        '</h3><p class="muted">' + esc(build.step_label || '') + '</p>' + progress;
      card.appendChild(head);
      if (build.status === 'asking') paintStep(build);
      else if (build.status === 'ready') paintReview(build);
      else if (build.status === 'building') paintBuilding(build);
      else if (build.status === 'done') paintResult(build);
      else paintBuildError(build);
    }

    function submitStep(build, button) {
      var answers = {};
      (build.questions || []).forEach(function (question) {
        var field = card.querySelector('[data-build-q="' + question.id + '"]');
        if (field && field._buildValue) answers[question.id] = field._buildValue();
      });
      button.disabled = true;
      api('POST', '/api/builds/' + buildId + '/answers',
          { step: build.step, answers: answers }).then(function (d) {
        paint(d.build);
      }).catch(function (e) {
        button.disabled = false;
        toast(e.message, false);
      });
    }

    function startBuild(build, button) {
      button.disabled = true;
      api('POST', '/api/builds/' + buildId + '/build').then(function () {
        load();
      }).catch(function (e) {
        button.disabled = false;
        toast(e.message, false);
      });
    }

    function paintStep(build) {
      var body = el('div', 'build-questions');
      (build.questions || []).forEach(function (question) {
        body.appendChild(buildQuestion(question));
      });
      card.appendChild(body);
      var actions = el('div', 'build-actions');
      if (build.step > 0) {
        var back = el('button', 'btn btn-ghost', 'Back');
        back.type = 'button';
        back.onclick = function () { load(build.step - 1); };
        actions.appendChild(back);
      }
      var next = el('button', 'btn btn-primary', 'Next');
      next.type = 'button';
      next.onclick = function () { submitStep(build, next); };
      actions.appendChild(next);
      card.appendChild(actions);
      card.appendChild(el('p', 'fine-print',
        'Leave anything you are not sure about blank - we will use a sensible default.'));
    }

    function paintReview(build) {
      var summary = build.summary || {};
      var box = el('div', 'build-review');
      function block(title, rows, cls) {
        if (!rows || !rows.length) return;
        var section = el('div', 'build-review-block' + (cls ? ' ' + cls : ''));
        section.appendChild(el('strong', null, title));
        var list = el('ul');
        rows.forEach(function (row) { list.appendChild(el('li', null, row)); });
        section.appendChild(list);
        box.appendChild(section);
      }
      block('I will build', summary.will_build);
      block('I will assume', summary.assumptions);
      block('Still useful later', summary.missing, 'build-missing');
      if (summary.publish_note) box.appendChild(el('p', 'fine-print', summary.publish_note));
      card.appendChild(box);
      var actions = el('div', 'build-actions');
      var change = el('button', 'btn btn-ghost', 'Change an answer');
      change.type = 'button';
      change.onclick = function () { load(0); };
      actions.appendChild(change);
      var buildBtn = el('button', 'btn btn-primary', 'Build it');
      buildBtn.type = 'button';
      buildBtn.onclick = function () { startBuild(build, buildBtn); };
      actions.appendChild(buildBtn);
      card.appendChild(actions);
    }

    function paintBuilding(build) {
      var status = el('span', 'activity running');
      card.appendChild(status);
      if (!watching && build.job_id) {
        watching = true;
        watchJob(build.job_id, status, function () { load(); });
      } else if (!build.job_id) {
        card.appendChild(el('p', 'muted', 'The build is queued.'));
      }
    }

    function paintResult(build) {
      var result = build.result || {};
      var box = el('div', 'build-result');
      box.appendChild(el('p', 'muted',
        'Saved. Nothing is public until you publish or send.'));
      (result.notes || []).forEach(function (note) {
        box.appendChild(el('p', 'fine-print', note));
      });
      card.appendChild(box);
      var actions = el('div', 'build-actions');
      if (result.deliverable_id) {
        var open = el('button', 'btn btn-primary', 'Open in Studio');
        open.type = 'button';
        open.onclick = function () {
          buildCloseHost(card);
          openInStudio('deliverable', result.deliverable_id);
        };
        actions.appendChild(open);
        if (result.site_id) {
          var manyPages = (result.page_ids || []).length > 1;
          var publish = el('button', 'btn', manyPages ? 'Publish site' : 'Publish');
          publish.type = 'button';
          publish.onclick = function () {
            if (manyPages) publishBuildSite(build, result, publish);
            else publishBuildResult(build, result, publish);
          };
          actions.appendChild(publish);
        }
      }
      (result.pages || []).forEach(function (page) {
        if (!page.deliverable_id) return;
        var pageOpen = el('button', 'btn btn-ghost', 'Open ' + (page.title || 'page'));
        pageOpen.type = 'button';
        pageOpen.onclick = function () {
          buildCloseHost(card);
          openInStudio('deliverable', page.deliverable_id);
        };
        actions.appendChild(pageOpen);
      });
      if ((result.pages || []).some(function (page) { return page.page === 'blog'; })) {
        var refreshBlog = el('button', 'btn btn-ghost', 'Refresh blog page');
        refreshBlog.type = 'button';
        refreshBlog.onclick = function () {
          refreshBlog.disabled = true;
          api('POST', '/api/builds/' + build.id + "/refresh-blog").then(function (res) {
            toast('Blog page refreshed' + (res.url ? ' - ' + res.url : ''), true);
            refreshBlog.disabled = false;
          }).catch(function (e) {
            refreshBlog.disabled = false;
            toast(e.message, false);
          });
        };
        actions.appendChild(refreshBlog);
      }
      (result.form_links || []).forEach(function (link) {
        var url = link.public_url || link.url;
        actions.appendChild(buildLinkButton('Open ' + link.label, url));
        actions.appendChild(buildCopyButton(url));
      });
      if (result.form_url) {
        actions.appendChild(buildLinkButton('Open the form', result.form_url));
        actions.appendChild(buildCopyButton(result.form_url));
      }
      if (result.template_id) {
        var templates = el('button', 'btn btn-ghost', 'Open emails');
        templates.type = 'button';
        templates.onclick = function () { buildGo('#/app/emails', card); };
        actions.appendChild(templates);
      }
      if (result.automation_id) {
        var automation = el('button', 'btn btn-ghost', 'Review automation');
        automation.type = 'button';
        automation.onclick = function () { buildGo('#/app/automations', card); };
        actions.appendChild(automation);
      }
      card.appendChild(actions);
      var again = el('button', 'btn btn-ghost btn-sm', 'Create something else');
      again.type = 'button';
      again.onclick = function () {
        buildCloseHost(card);
        openBuildPicker();
      };
      card.appendChild(again);
    }

    function paintBuildError(build) {
      card.appendChild(el('p', 'form-error', build.error || 'The build could not finish.'));
      var actions = el('div', 'build-actions');
      var change = el('button', 'btn btn-ghost', 'Change an answer');
      change.type = 'button';
      change.onclick = function () { load(0); };
      actions.appendChild(change);
      var retry = el('button', 'btn btn-primary', 'Try again');
      retry.type = 'button';
      retry.onclick = function () { startBuild(build, retry); };
      actions.appendChild(retry);
      card.appendChild(actions);
    }

    load();
    return card;
  }

  function buildQuestion(question) {
    var box = el('div', 'build-question');
    box.setAttribute('data-build-q', question.id);
    var label = el('label', 'build-label');
    label.appendChild(el('span', null, question.label));
    if (question.required) label.appendChild(el('em', 'build-required', 'required'));
    box.appendChild(label);
    if (question.help) box.appendChild(el('p', 'build-help', question.help));

    var type = question.type || 'text';
    var value = question.value;
    if (type === 'choice' || type === 'yes_no') {
      var group = el('div', 'build-choices');
      var options = type === 'yes_no'
        ? [{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }]
        : (question.options || []);
      options.forEach(function (option) {
        var choice = el('label', 'build-choice');
        var radio = el('input');
        radio.type = 'radio';
        radio.name = 'b-' + question.id;
        radio.value = option.value;
        if (String(value == null ? '' : value) === String(option.value)) radio.checked = true;
        choice.appendChild(radio);
        choice.appendChild(el('span', null, option.label || option.value));
        group.appendChild(choice);
      });
      box.appendChild(group);
      box._buildValue = function () {
        var checked = group.querySelector('input:checked');
        return checked ? checked.value : '';
      };
    } else if (type === 'multi') {
      var multi = el('div', 'build-choices');
      var selected = Array.isArray(value) ? value : [];
      (question.options || []).forEach(function (option) {
        var choice = el('label', 'build-choice');
        var input = el('input');
        input.type = 'checkbox';
        input.value = option.value;
        if (selected.indexOf(option.value) !== -1) input.checked = true;
        choice.appendChild(input);
        choice.appendChild(el('span', null, option.label || option.value));
        multi.appendChild(choice);
      });
      box.appendChild(multi);
      box._buildValue = function () {
        return Array.prototype.slice.call(multi.querySelectorAll('input:checked'))
          .map(function (input) { return input.value; });
      };
    } else {
      var input = el(type === 'long_text' ? 'textarea' : 'input', 'build-input');
      if (type !== 'long_text') {
        input.type = type === 'color' ? 'color'
          : (type === 'number' ? 'number' : (type === 'email' ? 'email' : 'text'));
      }
      input.value = value == null ? '' : String(value);
      if (question.example) input.placeholder = question.example;
      if (question.example) {
        box.appendChild(el('p', 'build-example', 'e.g. ' + question.example));
      }
      box.appendChild(input);
      box._buildValue = function () { return input.value; };
    }
    return box;
  }

  function buildLinkButton(label, url) {
    var link = el('a', 'btn btn-ghost', label);
    link.href = absoluteLink(url);
    link.target = '_blank';
    link.rel = 'noopener';
    return link;
  }

  function buildCopyButton(url) {
    var button = el('button', 'btn btn-ghost', 'Copy link');
    button.type = 'button';
    button.onclick = function () {
      navigator.clipboard.writeText(absoluteLink(url))
        .then(function () { toast('Link copied', true); })
        .catch(function () { toast('Copy failed', false); });
    };
    return button;
  }

  function publishBuildSite(build, result, button) {
    var domain = result.site_domain || 'the site';
    if (!window.confirm('Publish all pages to ' + domain + '? Existing pages with the ' +
                        'same names will be replaced.')) return;
    button.disabled = true;
    api('POST', '/api/builds/' + build.id + "/publish").then(function (res) {
      toast(res.dry_run
        ? 'Dry-run: the pages are ready, but nothing was written'
        : 'Published to ' + (res.urls || []).join(', '), true);
      button.disabled = false;
    }).catch(function (e) {
      button.disabled = false;
      toast(e.message, false);
    });
  }

  function publishBuildResult(build, result, button) {
    var domain = result.site_domain || 'the site';
    if (!window.confirm('Publish this to ' + domain + '? If a page is already there, ' +
                        'it will be replaced.')) return;
    // One publish call site: the existing helper sends as_home/replace exactly as the
    // publish dialog does, so the two doors cannot drift apart.
    sendPublish({ id: result.deliverable_id }, result.site_id, null, button,
                result.kind === 'website', '');
  }

  function initials(name) {
    var parts = (name || '?').trim().split(/\s+/);
    return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
  }

  function avatarHTML(bot) {
    if (bot.logo_url) return '<img src="' + esc(bot.logo_url) + '" alt="">';
    return esc(initials(bot.name));
  }

  // True for the platform operator. The API sends is_owner; the role fallback
  // covers 'admin', the pre-tenancy spelling, so an older database still works.
  function isOwner(user) {
    if (!user) return false;
    if (typeof user.is_owner === 'boolean') return user.is_owner;
    return user.role === 'admin' || user.role === 'owner';
  }

  // ---------- Shell: sidebar + topbar ----------
  // The bar under the username is this client month: credits used against the plan
  // credits (or what was added when there is no plan). A balance gauge answers the wrong
  // question - a client runs out of an allowance, not out of a wallet - and the operator
  // gets a quiet hint instead of a blank, because the number is per client.
  var creditCache = { at: 0, data: null };

  function creditMeter(node) {
    function paint(d) {
      var balance = d && d.balance;
      var value = node.querySelector('.side-credit-value');
      var track = node.querySelector('.side-credit-track');
      var fill = node.querySelector('.side-credit-fill');

      if ((balance === null || balance === undefined) && d && d.orgs) {
        // The operator on All clients: usage is per client, so say so rather than leave
        // an unexplained gap.
        node.hidden = false;
        node.classList.remove('low');
        value.textContent = 'Usage · pick a client';
        track.classList.add('hidden');
        node.title = 'Credits used this month are per client. Pick one in the workspace ' +
          'switcher, or open Platform → Usage for every client.';
        return;
      }
      if (balance === null || balance === undefined) { node.hidden = true; return; }

      var allowance = Number(d.allowance || 0);
      var spent = Number(d.spent || 0);
      var added = Number(d.added || 0);
      // The month budget: the plan credits, or what was actually put in this month.
      var tank = allowance > 0 ? allowance : added;
      var over = tank > 0 && spent >= tank;
      var low = !!d.low || over || Number(balance) < 0;

      node.hidden = false;
      node.classList.toggle('low', low);
      if (tank > 0) {
        value.textContent = fmtCredits(spent) + ' / ' + fmtCredits(tank) + ' credits used';
        track.classList.remove('hidden');
        // The bar fills as the month is consumed; at 100% the credits are used up.
        fill.style.width = Math.max(0, Math.min(100, (spent / tank) * 100)) + '%';
        track.setAttribute('aria-valuemax', String(tank));
        track.setAttribute('aria-valuenow', String(Math.max(0, spent)));
      } else {
        // No plan and nothing added: the usage number is the whole story.
        value.textContent = fmtCredits(spent) + ' credits used this month';
        track.classList.add('hidden');
        track.removeAttribute('aria-valuemax');
        track.removeAttribute('aria-valuenow');
      }
      node.title = fmtCredits(spent) + ' credits used this month' +
        (tank > 0 ? ' of ' + fmtCredits(tank) +
          (allowance > 0 ? ' on the ' + (d.plan || 'standard') + ' plan' : ' added') : '') +
        ' · ' + fmtCredits(balance) + ' left';
    }
    var now = Date.now();
    if (creditCache.data && now - creditCache.at < 15000) {
      paint(creditCache.data);
      return;
    }
    api('GET', '/api/usage').then(function (d) {
      creditCache = { at: Date.now(), data: d };
      paint(d);
    }).catch(function () {
      node.hidden = true;       // a broken meter must not break the shell
    });
  }

  // A client's sidebar is Studio plus one deliberate disclosure. The old grouped nav is
  // still the operator's fast path; a client should not have to scan six headings before
  // asking for a page.
  var simpleNavCleanup = null;

  function simpleClientNav(tab, navLeaf) {
    if (simpleNavCleanup) {
      simpleNavCleanup();
      simpleNavCleanup = null;
    }
    var nav = el('nav', 'side-nav side-simple');
    var activeGroup = groupFor(AREAS.app.nav, tab);
    var activeLabel = '';
    var activeFound = false;
    AREAS.app.nav.forEach(function (item) {
      var children = item.children ? visibleChildren(item) : [item];
      children.forEach(function (child) {
        if (child.id === tab) {
          activeFound = true;
          activeLabel = child.clientLabel || child.label;
        }
      });
    });

    var studio = AREAS.app.nav.filter(function (item) {
      return item.id === 'studio';
    })[0];
    if (studio) {
      var primary = navLeaf(studio, false);
      primary.classList.add('side-primary');
      primary.setAttribute('data-nav-primary', 'studio');
      nav.appendChild(primary);
    }

    var open = activeFound && !!activeGroup;
    var more = el('button', 'side-more' + (open ? ' open has-active' : ''));
    more.type = 'button';
    more.id = 'side-more-button';
    more.setAttribute('aria-expanded', open ? 'true' : 'false');
    more.setAttribute('aria-controls', 'side-more-panel');
    more.innerHTML = '<span class="side-icon">' + icon('menu') + '</span>' +
      '<span class="side-label">' + (activeLabel ? 'More · ' + esc(activeLabel) : 'More') +
      '</span>' +
      (open ? '<span class="side-dot" aria-hidden="true"></span>' : '') +
      '<span class="side-chevron" aria-hidden="true">›</span>';

    var panel = el('div', 'side-more-panel');
    panel.id = 'side-more-panel';
    panel.hidden = !open;
    AREAS.app.nav.forEach(function (item) {
      if (item.id === 'studio') return;
      if (item.adminOnly && !canAdmin(state.user)) return;
      if (item.ownerOnly && !isOwner(state.user)) return;
      if (item.clientHidden && !isOwner(state.user)) return;
      var children = item.children ? visibleChildren(item) : (item.hash ? [item] : []);
      if (!children.length) return;
      var section = el('div', 'side-more-group');
      section.appendChild(el('div', 'side-more-heading', item.label));
      children.forEach(function (child) {
        var link = navLeaf(child, true);
        link.classList.add('side-more-link');
        link.addEventListener('click', function () { setOpen(false); });
        section.appendChild(link);
      });
      panel.appendChild(section);
    });
    nav.appendChild(more);
    nav.appendChild(panel);

    function onKey(event) {
      if (event.key !== 'Escape') return;
      setOpen(false);
      try { more.focus(); } catch (e) { /* not focusable */ }
    }
    function onOutside(event) {
      if (!nav.contains(event.target)) setOpen(false);
    }
    function detach() {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('click', onOutside);
      if (simpleNavCleanup === detach) simpleNavCleanup = null;
    }
    function setOpen(next) {
      open = !!next;
      panel.hidden = !open;
      more.classList.toggle('open', open);
      more.classList.toggle('has-active', open);
      more.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) {
        if (simpleNavCleanup && simpleNavCleanup !== detach) simpleNavCleanup();
        simpleNavCleanup = detach;
        document.addEventListener('keydown', onKey);
        document.addEventListener('click', onOutside);
      } else {
        detach();
      }
    }
    more.onclick = function () { setOpen(!open); };
    if (open) setOpen(true);
    return nav;
  }

  function sidebar(parts) {
    var area = parts[0] === 'platform' ? 'platform' : 'app';
    var tab = parts[1] || '';
    var aside = el('aside', 'sidebar');
    var brand = el('div', 'side-brand');
    brand.innerHTML = '<img class="logo-img" src="/admin/logo-trim.png" alt="' +
      esc(BRAND) + '" />';
    aside.appendChild(brand);
    aside.appendChild(workspaceSwitcher());

    // Two areas, reachable by clicking. Before this the only way from Platform back to
    // Workspace was editing the URL, which made a whole nav look missing.
    if (isOwner(state.user)) {
      var areas = el('div', 'side-areas');
      ['app', 'platform'].forEach(function (key) {
        var link = el('a', 'side-area' + (key === area ? ' active' : ''), AREAS[key].label);
        link.href = AREAS[key].home;
        areas.appendChild(link);
      });
      aside.appendChild(areas);
    }

    var openGroups = readOpenGroups();
    var activeGroup = groupFor(AREAS[area].nav, tab);
    var simpleClient = area === 'app' && !isOwner(state.user);

    function navLeaf(item, child) {
      var link = el('a', 'side-item' + (child ? ' side-child' : '') +
        (item.id === tab ? ' active' : ''));
      link.href = item.hash;
      if (item.id === tab) link.setAttribute('aria-current', 'page');
      var label = (!isOwner(state.user) && item.clientLabel) ? item.clientLabel : item.label;
      link.innerHTML = (child ? '' : '<span class="side-icon">' + icon(item.icon) + '</span>') +
        '<span class="side-label">' + esc(label) + '</span>' +
        (item.ready === false ? '<span class="side-soon">Soon</span>' : '');
      link.onclick = function () { document.body.classList.remove('nav-open'); };
      return link;
    }

    if (simpleClient) {
      aside.setAttribute('data-simple-nav', 'true');
      aside.appendChild(simpleClientNav(tab, navLeaf));
    } else {
      aside.appendChild(el('div', 'side-heading', AREAS[area].label));
      var nav = el('nav', 'side-nav');
      AREAS[area].nav.forEach(function (item) {
        if (item.adminOnly && !canAdmin(state.user)) return;
        if (item.ownerOnly && !isOwner(state.user)) return;
        if (item.clientHidden && !isOwner(state.user)) return;
        if (!item.children || !item.children.length) {
          nav.appendChild(navLeaf(item, false));
          return;
        }
        var children = visibleChildren(item);
        if (!children.length) return;          // a member with only admin children
        var active = !!(activeGroup && activeGroup.id === item.id);
        var open = active || openGroups.indexOf(item.id) !== -1;
        if (active && openGroups.indexOf(item.id) === -1) {
          // Remember it: a group you were just in should not snap shut when you move on.
          openGroups.push(item.id);
          writeOpenGroup(item.id, true);
        }
        var sub = el('div', 'side-sub');
        sub.id = 'side-sub-' + item.id;
        sub.hidden = !open;
        var group = el('button', 'side-group' + (open ? ' open' : '') +
          (active ? ' has-active' : ''));
        group.type = 'button';
        group.setAttribute('aria-expanded', open ? 'true' : 'false');
        group.setAttribute('aria-controls', sub.id);
        group.innerHTML = '<span class="side-icon">' + icon(item.icon) + '</span>' +
          '<span class="side-label">' + esc(item.label) + '</span>' +
          (active ? '<span class="side-dot" aria-hidden="true"></span>' : '') +
          '<span class="side-chevron" aria-hidden="true">›</span>';
        group.onclick = function () {
          var next = !group.classList.contains('open');
          group.classList.toggle('open', next);
          group.setAttribute('aria-expanded', next ? 'true' : 'false');
          sub.hidden = !next;
          writeOpenGroup(item.id, next);
        };
        children.forEach(function (child) { sub.appendChild(navLeaf(child, true)); });
        nav.appendChild(group);
        nav.appendChild(sub);
      });
      aside.appendChild(nav);
    }

    // The operator works in both areas: their own brand and sites live in the
    // workspace, the clients they host live in the console.
    if (isOwner(state.user)) {
      var other = area === 'platform' ? 'app' : 'platform';
      var swap = el('a', 'side-item side-swap');
      swap.href = AREAS[other].home;
      swap.innerHTML = '<span class="side-icon">' +
        icon(other === 'platform' ? 'arrow-right' : 'arrow-left') + '</span>' +
        '<span class="side-label">' + (other === 'platform' ? 'Platform console' : 'Client workspace') + '</span>';
      aside.appendChild(swap);
    }

    var foot = el('div', 'side-foot');
    var who = el('div', 'side-user');
    var org = state.user.org || null;
    // A client sees which workspace they are in; the operator sees the platform.
    var who2 = org ? esc(org.name) : (isOwner(state.user) ? 'Platform operator' : esc(state.user.role));
    who.innerHTML = '<span class="avatar">' + esc(initials(state.user.name)) + '</span>' +
      '<div><div class="side-user-name">' + esc(state.user.name) + '</div>' +
      '<div class="side-user-role">' + who2 + '</div></div>';
    foot.appendChild(who);
    // The client's own credits, under the name: a number, and a track only when there
    // is a monthly allowance to measure it against. Clicking opens the Usage tab.
    var credit = el('a', 'side-credit');
    credit.href = area === 'platform' ? '#/platform/usage' : '#/app/usage';
    credit.hidden = true;
    credit.innerHTML = '<div class="side-credit-label">' +
      '<span class="side-credit-value">…</span></div>' +
      '<div class="side-credit-track hidden" role="progressbar" ' +
      'aria-label="Credits remaining" aria-valuemin="0">' +
      '<div class="side-credit-fill"></div></div>';
    foot.appendChild(credit);
    creditMeter(credit);
    var logout = el('button', 'btn btn-ghost btn-sm', 'Log out');
    logout.onclick = function () {
      api('POST', '/api/auth/logout').then(function () { location.reload(); });
    };
    foot.appendChild(logout);
    var build = el('div', 'side-build', 'Version …');
    buildInfo().then(function (v) {
      build.textContent = 'Version ' + (v.version || 'unknown');
      build.title = 'commit ' + (v.commit || '?') + ' · built ' + (v.build_time || '?') +
        ' · process started ' + (v.started_at || '?');
    });
    foot.appendChild(build);
    aside.appendChild(foot);
    return aside;
  }

  function topbar() {
    var bar = el('div', 'topbar');
    var inner = el('div', 'topbar-inner');
    var burger = el('button', 'burger');
    burger.innerHTML = icon('menu');
    burger.setAttribute('aria-label', 'Toggle navigation');
    burger.onclick = function () { document.body.classList.toggle('nav-open'); };
    inner.appendChild(burger);
    var brand = el('div', 'brand');
    brand.innerHTML = '<img class="logo-img" src="/admin/logo-trim.png" alt="' +
      esc(BRAND) + '" />';
    inner.appendChild(brand);
    inner.appendChild(el('div', 'spacer'));
    bar.appendChild(inner);
    return bar;
  }

  function navVeil() {
    if (document.getElementById('nav-veil')) return;
    var veil = el('div', 'nav-veil');
    veil.id = 'nav-veil';
    veil.onclick = function () { document.body.classList.remove('nav-open'); };
    document.body.appendChild(veil);
  }

  // ---------- Auth pages ----------
  // Login, forgot password, reset and invitation acceptance share one card and the
  // same password controls. Keeping the markup here means the label, visibility toggle
  // and error behaviour cannot drift between the four doors into the product.
  var authSeq = 0;
  function authField(labelText, input, help) {
    var field = el('div', 'field');
    var label = el('label', null, labelText);
    if (!input.id) input.id = 'auth-field-' + (++authSeq);
    label.htmlFor = input.id;
    field.appendChild(label);
    field.appendChild(input);
    if (help) field.appendChild(el('small', 'auth-help', help));
    return field;
  }

  function authPassword(labelText, placeholder) {
    var wrap = el('div', 'password-wrap');
    var input = el('input');
    input.type = 'password';
    input.placeholder = placeholder || '';
    input.setAttribute('autocomplete', 'new-password');
    var toggle = el('button', 'pw-toggle', 'Show');
    toggle.type = 'button';
    toggle.setAttribute('aria-label', 'Show password');
    toggle.onclick = function () {
      var shown = input.type === 'text';
      input.type = shown ? 'password' : 'text';
      toggle.textContent = shown ? 'Show' : 'Hide';
      toggle.setAttribute('aria-label', shown ? 'Show password' : 'Hide password');
    };
    wrap.appendChild(input);
    wrap.appendChild(toggle);
    return { field: authField(labelText, wrap), input: input };
  }

  function authError() {
    var error = el('div', 'error');
    error.setAttribute('role', 'alert');
    error.setAttribute('aria-live', 'polite');
    return error;
  }

  function authBack(link, text) {
    var anchor = el('a', 'auth-link', text);
    anchor.href = link;
    return anchor;
  }

  function viewLogin() {
    var wrap = el('div', 'auth-wrap');
    var card = el('div', 'auth-card');
    card.innerHTML = '<img class="auth-logo" src="/admin/logo-trim.png" alt="' +
      esc(BRAND) + '" /><h1>' + esc(BRAND) +
      '</h1><p class="sub">Sign in to your growth platform</p>';
    var form = el('form', 'auth-form');
    var err = authError();
    var email = el('input');
    email.type = 'email';
    email.placeholder = 'you@example.com';
    email.setAttribute('autocomplete', 'email');
    var emailField = authField('Email', email);
    var password = el('input');
    password.type = 'password';
    password.setAttribute('autocomplete', 'current-password');
    var passwordField = authField('Password', password);
    var btn = el('button', 'btn btn-primary', 'Sign in');
    btn.type = 'submit';
    form.appendChild(err);
    form.appendChild(emailField);
    form.appendChild(passwordField);
    form.appendChild(btn);
    var forgot = authBack('#/forgot-password', 'Forgot password?');
    var links = el('div', 'auth-links');
    links.appendChild(forgot);
    form.appendChild(links);
    form.onsubmit = function (ev) {
      ev.preventDefault();
      err.textContent = '';
      btn.disabled = true;
      api('POST', '/api/auth/login', { email: email.value, password: password.value })
        .then(function (d) { state.user = d.user; render(); firstRun(); })
        .catch(function (e) { err.textContent = e.message; })
        .finally(function () { btn.disabled = false; });
    };
    card.appendChild(form);
    wrap.appendChild(card);
    return wrap;
  }

  function viewForgotPassword() {
    var wrap = el('div', 'auth-wrap');
    var card = el('div', 'auth-card');
    card.innerHTML = '<img class="auth-logo" src="/admin/logo-trim.png" alt="' +
      esc(BRAND) + '" /><h1>Reset your password</h1>' +
      '<p class="sub">Enter your email and we will send you a reset link.</p>';
    var form = el('form', 'auth-form');
    var err = authError();
    var email = el('input');
    email.type = 'email';
    email.placeholder = 'you@example.com';
    email.setAttribute('autocomplete', 'email');
    form.appendChild(err);
    form.appendChild(authField('Email', email));
    var btn = el('button', 'btn btn-primary', 'Send reset link');
    btn.type = 'submit';
    form.appendChild(btn);
    var back = authBack('#/login', 'Back to sign in');
    var links = el('div', 'auth-links');
    links.appendChild(back);
    form.appendChild(links);
    form.onsubmit = function (ev) {
      ev.preventDefault();
      err.textContent = '';
      btn.disabled = true;
      api('POST', '/api/auth/forgot-password', { email: email.value })
        .then(function (d) {
          card.innerHTML = '<img class="auth-logo" src="/admin/logo-trim.png" alt="' +
            esc(BRAND) + '" /><h1>Check your inbox</h1>' +
            '<p class="sub">' + esc(d.message ||
              'If that email is registered, a reset link is on its way.') +
            '</p><p class="auth-help">The link expires in 60 minutes. If it does not ' +
            'arrive, check your spam folder or ask your administrator.</p>';
          card.appendChild(authBack('#/login', 'Back to sign in'));
        })
        .catch(function (e) { err.textContent = e.message; })
        .finally(function () { btn.disabled = false; });
    };
    card.appendChild(form);
    wrap.appendChild(card);
    return wrap;
  }

  function viewResetPassword(token) {
    var wrap = el('div', 'auth-wrap');
    var card = el('div', 'auth-card');
    var header = '<img class="auth-logo" src="/admin/logo-trim.png" alt="' +
      esc(BRAND) + '" /><h1>Choose a new password</h1>' +
      '<p class="sub">Set a password for your account.</p>';
    card.innerHTML = header;
    var loading = el('p', 'muted', 'Checking your reset link…');
    card.appendChild(loading);

    function invalid(message) {
      card.innerHTML = header;
      card.appendChild(el('div', 'error', message));
      card.appendChild(authBack('#/forgot-password', 'Request a new link'));
      card.appendChild(authBack('#/login', 'Back to sign in'));
    }

    api('GET', '/api/auth/reset/' + encodeURIComponent(token)).then(function (d) {
      var reset = d.reset || {};
      if (reset.status !== 'pending') {
        var message = reset.status === 'used'
          ? 'This reset link has already been used.'
          : reset.status === 'expired'
            ? 'This reset link has expired.'
            : 'This account is not available for a password reset.';
        invalid(message);
        return;
      }
      card.innerHTML = header;
      var who = el('p', 'muted', (reset.name ? reset.name + ' · ' : '') + reset.email);
      card.appendChild(who);
      var form = el('form', 'auth-form');
      var err = authError();
      var password = authPassword('New password', 'At least 8 characters');
      password.input.autocomplete = 'new-password';
      var confirm = authPassword('Confirm password', 'Repeat your new password');
      var btn = el('button', 'btn btn-primary', 'Update password');
      btn.type = 'submit';
      btn.disabled = true;
      function update() {
        var ready = password.input.value.length >= 8 &&
          password.input.value === confirm.input.value;
        btn.disabled = !ready;
        if (password.input.value && password.input.value.length < 8) {
          err.textContent = 'Use at least 8 characters';
        } else if (confirm.input.value && password.input.value !== confirm.input.value) {
          err.textContent = 'The two passwords do not match';
        } else if (err.textContent === 'The two passwords do not match' ||
                   err.textContent === 'Use at least 8 characters') {
          err.textContent = '';
        }
      }
      password.input.oninput = update;
      confirm.input.oninput = update;
      form.appendChild(err);
      form.appendChild(password.field);
      form.appendChild(confirm.field);
      form.appendChild(btn);
      form.appendChild(authBack('#/forgot-password', 'Request a new link'));
      form.onsubmit = function (ev) {
        ev.preventDefault();
        if (!password.input.value || password.input.value !== confirm.input.value) {
          err.textContent = 'The two passwords do not match';
          return;
        }
        btn.disabled = true;
        api('POST', '/api/auth/reset', {
          token: token,
          password: password.input.value,
          confirm_password: confirm.input.value
        }).then(function (d) {
          card.innerHTML = '<img class="auth-logo" src="/admin/logo-trim.png" alt="' +
            esc(BRAND) + '" /><h1>Password updated</h1>' +
            '<p class="sub">' + esc(d.message ||
              'Your password has been updated. You can sign in now.') + '</p>';
          card.appendChild(authBack('#/login', 'Sign in'));
        }).catch(function (e) {
          btn.disabled = false;
          err.textContent = e.message;
        });
      };
      card.appendChild(form);
    }).catch(function (e) {
      card.innerHTML = header;
      card.appendChild(el('div', 'error', e.message));
      card.appendChild(authBack('#/forgot-password', 'Request a new link'));
      card.appendChild(authBack('#/login', 'Back to sign in'));
    });
    wrap.appendChild(card);
    return wrap;
  }

  // ---------- Embed modal ----------
  function showEmbed(bot) {
    // ?v=2 busts the copy a client's page already cached; the route also sends a
    // short max-age so existing snippets pick up a redesign within minutes.
    var snippet = '<script src="' + location.origin + '/widget.js?v=2" data-bot="' + bot.id + '"><\/script>';
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal');
    modal.innerHTML = '<h3>Embed widget</h3><p class="muted">Paste this into the client\'s website, before <code>&lt;/body&gt;</code>:</p>';
    modal.appendChild(el('code', null, snippet));
    var actions = el('div', 'modal-actions');
    var close = el('button', 'btn btn-ghost', 'Close');
    close.onclick = function () { backdrop.remove(); };
    var copy = el('button', 'btn btn-primary', 'Copy');
    copy.onclick = function () {
      navigator.clipboard.writeText(snippet).then(function () { toast('Copied', true); }).catch(function () { toast('Copy failed', false); });
    };
    actions.appendChild(close);
    actions.appendChild(copy);
    modal.appendChild(actions);
    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);
  }

  // ---------- Dashboard ----------
  function statCard(value, label) {
    var card = el('div', 'stat-card');
    card.appendChild(el('div', 'stat-value', String(value)));
    card.appendChild(el('div', 'stat-label', label));
    return card;
  }

  // A recommendation card can hand its action text to the Studio without submitting it.
  // Chips only fill the composer. "Ask AI" is an explicit turn and may auto-send it.
  var pendingStudioPrompt = '';
  var pendingStudioSend = false;
  function openStudioWithPrompt(text, sendAfter) {
    pendingStudioPrompt = String(text || '').trim();
    pendingStudioSend = !!sendAfter;
    location.hash = '#/app/studio';
  }

  function renderChecklist(card, prof, sites, content, bots) {
    var steps = [
      { label: 'Add your business details', done: !!prof.name, href: '#/app/profile' },
      { label: 'Connect your domain', done: sites.length > 0, href: '#/app/domains' },
      { label: 'Create your first page', done: content.length > 0, href: '#/app/studio' },
      { label: 'Create a chatbot', done: bots.length > 0, href: '#/app/bots' }
    ];
    if (canAdmin(state.user)) {
      steps.push({ label: 'Connect email (optional)', done: false, href: '#/app/settings' });
    }
    var allDone = steps.slice(0, 4).every(function (step) { return step.done; });
    card.innerHTML = '<h3>' + (allDone ? 'You are set up' : 'Getting started') + '</h3>' +
      '<p class="muted">' + (allDone
        ? 'Your workspace is ready. You can change any of this later.'
        : 'A few short steps and your workspace is ready.') + '</p>';
    steps.forEach(function (step) {
      var row = el('div', 'top-item');
      row.innerHTML = '<span>' + (step.done ? '<b>✓</b> ' : '<b>○</b> ') +
        esc(step.label) + '</span>';
      var open = el('a', 'btn btn-ghost btn-sm', step.done ? 'Review' : 'Open');
      open.href = step.href;
      row.appendChild(open);
      card.appendChild(row);
    });
    card.appendChild(el('p', 'fine-print',
      'Need help? Ask your agency. They can set up anything you are unsure about.'));
  }

  function recommendationCopy(text, button) {
    if (!(navigator.clipboard && navigator.clipboard.writeText)) {
      toast('Clipboard is not available here', false);
      return;
    }
    navigator.clipboard.writeText(String(text || '')).then(function () {
      var old = button ? button.textContent : '';
      if (button) button.textContent = 'Copied';
      toast('Copied', true);
      if (button) setTimeout(function () { button.textContent = old; }, 1200);
    }).catch(function () { toast('Could not copy', false); });
  }

  function renderRecommendations(host, data, opts) {
    opts = opts || {};
    var rows = (data && data.recommendations) || [];
    host.innerHTML = '';
    host.hidden = !rows.length;
    if (!rows.length) return;
    var head = el('div', 'rec-head');
    var title = el('div');
    title.innerHTML = '<h3>Recommended next steps</h3>' +
      '<p class="muted">Evidence from this workspace' +
      (data.source === 'ai' ? ', ranked by AI' : '') + '. Nothing changes until you ask.</p>';
    head.appendChild(title);
    var refresh = el('button', 'btn btn-ghost btn-sm', 'Refresh');
    refresh.onclick = function () {
      refresh.disabled = true;
      api('POST', '/api/recommendations/refresh').then(function (next) {
        renderRecommendations(host, next, opts);
        if (opts.onRefresh) opts.onRefresh();
      }).catch(function (e) { toast(e.message, false); })
        .finally(function () { refresh.disabled = false; });
    };
    head.appendChild(refresh);
    host.appendChild(head);
    if (data.refreshing) {
      host.appendChild(el('p', 'fine-print', 'Updating the wording with AI...'));
    }
    if (data.stale) {
      host.appendChild(el('p', 'fine-print',
        'The underlying facts changed. Press Refresh for a fresh batch.'));
    }

    var selected = {};
    var list = el('div', 'rec-list');
    var toolbar = el('div', 'rec-toolbar');
    var copySelected = el('button', 'btn btn-ghost btn-sm', 'Copy selected');
    var askSelected = el('button', 'btn btn-primary btn-sm', 'Ask AI with selected');
    function picked() {
      return rows.filter(function (item) { return selected[item.key]; });
    }
    function syncToolbar() {
      var count = picked().length;
      copySelected.disabled = !count;
      askSelected.disabled = !count;
      copySelected.textContent = count ? 'Copy selected (' + count + ')' : 'Copy selected';
    }
    rows.forEach(function (item) {
      var row = el('div', 'rec-row');
      var check = el('input');
      check.type = 'checkbox';
      check.className = 'rec-check';
      check.setAttribute('aria-label', 'Select ' + (item.title || 'recommendation'));
      check.onchange = function () {
        selected[item.key] = check.checked;
        syncToolbar();
      };
      row.appendChild(check);
      var main = el('div', 'rec-main');
      main.appendChild(el('div', 'rec-title', item.title || 'Next step'));
      if (item.why) main.appendChild(el('div', 'rec-why', item.why));
      if ((item.evidence || []).length) {
        main.appendChild(el('div', 'rec-evidence', item.evidence.join(' - ')));
      }
      var actions = el('div', 'rec-actions');
      var copy = el('button', 'btn btn-ghost btn-sm', 'Copy');
      copy.onclick = function () { recommendationCopy(item.action_text || item.title, copy); };
      var ask = el('button', 'btn btn-primary btn-sm', 'Ask AI');
      ask.onclick = function () { openStudioWithPrompt(item.action_text || item.title, true); };
      actions.appendChild(copy);
      actions.appendChild(ask);
      if (item.href) {
        var open = el('button', 'btn btn-ghost btn-sm', 'Open');
        open.onclick = function () { location.hash = item.href; };
        actions.appendChild(open);
      }
      var dismiss = el('button', 'btn btn-ghost btn-sm', 'Not useful');
      dismiss.onclick = function () {
        api('POST', '/api/recommendations/' + encodeURIComponent(item.key) + '/dismiss')
          .then(function (next) {
            renderRecommendations(host, next, opts);
            if (next.refreshing && opts.onRefresh) opts.onRefresh();
          })
          .catch(function (e) { toast(e.message, false); });
      };
      actions.appendChild(dismiss);
      main.appendChild(actions);
      row.appendChild(main);
      list.appendChild(row);
    });
    copySelected.onclick = function () {
      var text = picked().map(function (item) { return '- ' + (item.action_text || item.title); })
        .join('\n');
      recommendationCopy(text, copySelected);
    };
    askSelected.onclick = function () {
      var text = picked().map(function (item) { return '- ' + (item.action_text || item.title); })
        .join('\n');
      openStudioWithPrompt('Please help me with these next steps:\n' + text, true);
    };
    toolbar.appendChild(copySelected);
    toolbar.appendChild(askSelected);
    host.appendChild(toolbar);
    host.appendChild(list);
    syncToolbar();
  }

  function loadDashboardRecommendations(host) {
    var timer = 0;
    function tick() {
      api('GET', '/api/recommendations').then(function (data) {
        renderRecommendations(host, data, { onRefresh: tick });
        if (data.refreshing) {
          timer = setTimeout(tick, 3000);
        }
      }).catch(function () { host.hidden = true; });
    }
    tick();
    onCleanup(function () { if (timer) clearTimeout(timer); });
  }

  function viewDashboard() {
    var container = el('div', 'container');
    var owner = isOwner(state.user);
    var head = el('div', 'page-head');
    head.innerHTML = owner
      ? '<div><h2>Dashboard</h2><p>Your platform at a glance.</p></div>'
      : '<div><h2>Dashboard</h2><p>Your workspace at a glance.</p></div>';
    container.appendChild(head);

    var grid = el('div', 'stats-grid');
    container.appendChild(grid);

    var recHost = el('div', 'panel-card mt');
    recHost.hidden = true;
    container.appendChild(recHost);
    loadDashboardRecommendations(recHost);

    var checklist = null;
    if (!owner) {
      checklist = el('div', 'panel-card mt');
      container.appendChild(checklist);
    }

    var panel = el('div', 'panel-card');
    panel.innerHTML = '<h3>Recent jobs</h3>';
    var recent = el('div');
    panel.appendChild(recent);
    if (owner) container.appendChild(panel);

    var extras = owner
      ? [Promise.resolve({}), Promise.resolve({})]
      : [api('GET', '/api/sites?summary=1'), api('GET', '/api/deliverables?summary=1')];
    Promise.all([api('GET', '/api/bots'), api('GET', '/api/jobs'),
                 api('GET', '/api/profile')].concat(extras))
      .then(function (results) {
        var bots = results[0].bots || [];
        var jobs = results[1].jobs || [];
        var prof = results[2].profile || {};
        var sites = (results[3] || {}).sites || [];
        var content = (results[4] || {}).deliverables || [];
        var messages = bots.reduce(function (sum, b) { return sum + (b.message_count || 0); }, 0);

        grid.innerHTML = '';
        grid.appendChild(statCard(bots.length, 'Chatbots'));
        grid.appendChild(statCard(messages, 'Messages'));
        if (owner) grid.appendChild(statCard(jobs.length, 'Agent jobs'));
        grid.appendChild(statCard(prof.name ? 'Ready' : 'Empty',
                                  owner ? 'Business Profile' : 'Business details'));

        if (!owner) {
          renderChecklist(checklist, prof, sites, content, bots);
          return;
        }
        recent.innerHTML = '';
        if (!jobs.length) {
          recent.appendChild(el('div', 'muted', 'No agent jobs yet.'));
          return;
        }
        jobs.slice(0, 5).forEach(function (job) {
          var row = el('div', 'top-item');
          row.innerHTML = '<span>#' + job.id + ' ' + esc(job.title) + '</span>' +
            '<span class="count">' + statusBadge(job.status) + '</span>';
          row.style.cursor = 'pointer';
          row.onclick = function () { location.hash = '#/jobs/' + job.id; };
          recent.appendChild(row);
        });
      })
      .catch(function (e) { toast(e.message, false); });

    return container;
  }

  // ---------- Chatbots ----------
  function viewBots() {
    var container = el('div', 'container');
    var manager = canAdmin(state.user);
    var head = el('div', 'page-head');
    var hwrap = el('div');
    hwrap.innerHTML = '<h2>Chatbots</h2><p>' +
      (isOwner(state.user)
        ? 'Create and manage chatbots for your clients.'
        : (manager ? 'Create and manage chatbots for your business.'
                   : "View your business's chatbots.")) + '</p>';
    head.appendChild(hwrap);
    if (manager) {
      var newBtn = el('button', 'btn btn-primary', '+ New Chatbot');
      newBtn.onclick = function () { location.hash = '#/bot/new'; };
      head.appendChild(newBtn);
    }
    container.appendChild(head);

    var grid = el('div', 'grid');
    container.appendChild(grid);

    api('GET', '/api/bots').then(function (d) {
      var bots = d.bots || [];
      if (!bots.length) {
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">' + icon('bot') + '</div><strong>No chatbots yet</strong><p>Create one to put an assistant on your site or in your DMs.</p></div>';
        return;
      }
      bots.forEach(function (bot) {
        var card = el('div', 'bot-card');
        var top = el('div', 'bot-card-top');
        var av = el('div', 'bot-avatar');
        av.style.background = bot.brand_color || '#4f46e5';
        av.innerHTML = avatarHTML(bot);
        top.appendChild(av);
        var names = el('div');
        names.innerHTML = '<div class="bot-card-name">' + esc(bot.name) + '</div><div class="bot-card-assistant">' + esc(bot.assistant_name) + '</div>';
        top.appendChild(names);
        card.appendChild(top);
        var meta = el('div', 'bot-card-meta');
        meta.innerHTML = '<span>' + (bot.message_count || 0) + ' msgs</span><span>ID ' + esc(bot.id.slice(0, 10)) + '</span>';
        card.appendChild(meta);
        var actions = el('div', 'card-actions');
        var embed = el('button', 'btn btn-ghost btn-sm', 'Embed');
        embed.onclick = function () { showEmbed(bot); };
        actions.appendChild(embed);
        if (manager) {
          var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
          edit.onclick = function () { location.hash = '#/bot/' + bot.id; };
          actions.appendChild(edit);
        }
        var stats = el('button', 'btn btn-ghost btn-sm', 'Stats');
        stats.onclick = function () { location.hash = '#/bot/' + bot.id + '/stats'; };
        var conv = el('button', 'btn btn-ghost btn-sm', 'Chats');
        conv.onclick = function () { location.hash = '#/bot/' + bot.id + '/conversations'; };
        actions.appendChild(stats);
        actions.appendChild(conv);
        if (manager) {
          var del = el('button', 'btn btn-danger btn-sm', 'Delete');
          del.onclick = function () {
            if (confirm('Delete "' + bot.name + '" and all its messages?')) {
              api('DELETE', '/api/bots/' + bot.id).then(function () { toast('Deleted', true); render(); }).catch(function (e) { toast(e.message, false); });
            }
          };
          actions.appendChild(del);
        }
        card.appendChild(actions);
        grid.appendChild(card);
      });
    }).catch(function (e) { toast(e.message, false); });

    return container;
  }

  // ---------- Meta channels ----------
  var PLATFORM_LABELS = { messenger: 'Messenger', instagram: 'Instagram', whatsapp: 'WhatsApp' };

  function buildChannelsPanel(botId) {
    var owner = isOwner(state.user);
    var card = el('div', 'panel-card');
    card.style.marginTop = '20px';
    card.appendChild(el('h3', null, 'Meta channels (Messenger / Instagram / WhatsApp)'));
    card.appendChild(el('div', 'muted', 'Route DMs from a Facebook Page, Instagram professional account, or WhatsApp Business number to this bot.'));

    // Channel credentials are developer setup. Keep them one disclosure away from a
    // client; the operator can have them open while working.
    var advanced = el('details', 'channel-advanced');
    var summary = el('summary', null, 'Advanced channel setup');
    advanced.appendChild(summary);
    if (owner) advanced.open = true;

    var cbField = el('div', 'field');
    cbField.style.marginTop = '14px';
    cbField.innerHTML = '<label>Webhook callback URL</label>';
    var cbRow = el('div', 'chip-input');
    var cbInput = el('input'); cbInput.readOnly = true; cbInput.value = location.origin + '/webhooks/meta';
    var cbCopy = el('button', 'btn btn-ghost btn-sm', 'Copy');
    cbCopy.onclick = function () {
      navigator.clipboard.writeText(cbInput.value).then(function () { toast('Copied', true); }).catch(function () { toast('Copy failed', false); });
    };
    cbRow.appendChild(cbInput); cbRow.appendChild(cbCopy);
    cbField.appendChild(cbRow);
    cbField.appendChild(el('div', 'hint', 'Paste this as the Callback URL in your Meta app webhook settings, then subscribe to the "messages" field.'));
    advanced.appendChild(cbField);

    var list = el('div');
    card.appendChild(list);

    var form = el('div', 'form-card');
    form.style.marginTop = '16px';
    form.style.background = '#f9fafb';
    form.innerHTML = '<h3>Add channel</h3>';
    function fieldRow(label, input, hint) {
      var f = el('div', 'field');
      f.innerHTML = '<label>' + esc(label) + '</label>';
      f.appendChild(input);
      if (hint) f.appendChild(el('div', 'hint', hint));
      return f;
    }

    var platform = el('select');
    platform.innerHTML = '<option value="messenger">Messenger (Facebook Page)</option>' +
      '<option value="instagram">Instagram</option>' +
      '<option value="whatsapp">WhatsApp</option>';
    var extId = el('input'); extId.placeholder = 'Page ID / IG account ID / Phone number ID';
    var token = el('input'); token.type = 'password'; token.placeholder = 'Access token';
    var appSecret = el('input'); appSecret.type = 'password'; appSecret.placeholder = 'App secret (for webhook signature checks)';
    var verifyToken = el('input'); verifyToken.placeholder = 'Verify token (leave blank to auto-generate)';

    form.appendChild(fieldRow('Platform', platform));
    form.appendChild(fieldRow('Page / Instagram / Phone ID', extId, 'Found in the Meta app dashboard.'));
    form.appendChild(fieldRow('Access token', token, 'Long-lived Page / System User token, or WhatsApp permanent token.'));
    form.appendChild(fieldRow('App secret', appSecret, 'Meta app secret — required to verify incoming webhooks.'));
    form.appendChild(fieldRow('Verify token', verifyToken, "The token you enter in Meta's webhook setup. Auto-generated if left blank."));

    var editingId = null;
    var saveBtn = el('button', 'btn btn-primary', 'Add channel');
    var cancelBtn = el('button', 'btn btn-ghost', 'Cancel');
    cancelBtn.style.display = 'none';
    cancelBtn.onclick = resetForm;
    var actions = el('div', 'card-actions');
    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    form.appendChild(actions);
    advanced.appendChild(form);
    card.appendChild(advanced);

    function resetForm() {
      editingId = null;
      extId.value = ''; token.value = ''; appSecret.value = ''; verifyToken.value = '';
      platform.value = 'messenger';
      token.placeholder = 'Access token';
      appSecret.placeholder = 'App secret (for webhook signature checks)';
      saveBtn.textContent = 'Add channel';
      cancelBtn.style.display = 'none';
      form.querySelector('h3').textContent = 'Add channel';
    }

    saveBtn.onclick = function () {
      var payload = {
        platform: platform.value,
        external_id: extId.value.trim(),
        access_token: token.value.trim(),
        app_secret: appSecret.value.trim(),
        verify_token: verifyToken.value.trim()
      };
      if (!payload.external_id) { toast('Page / Instagram / Phone ID is required', false); return; }
      saveBtn.disabled = true;
      var req = editingId
        ? api('PUT', '/api/bots/' + botId + '/channels/' + editingId, payload)
        : api('POST', '/api/bots/' + botId + '/channels', payload);
      req.then(function () {
        toast(editingId ? 'Channel updated' : 'Channel added', true);
        resetForm();
        loadList();
      }).catch(function (e) { toast(e.message, false); }).finally(function () { saveBtn.disabled = false; });
    };

    function loadList() {
      api('GET', '/api/bots/' + botId + '/channels').then(function (d) {
        renderList(d.channels || []);
      }).catch(function (e) { toast(e.message, false); });
    }

    function renderList(channels) {
      list.innerHTML = '';
      if (!channels.length) {
        list.appendChild(el('div', 'muted', 'No channels linked yet.'));
        return;
      }
      channels.forEach(function (ch) {
        var row = el('div', 'bot-card');
        row.style.marginTop = '12px';
        var head = el('div', 'bot-card-meta');
        head.innerHTML = '<span><b>' + esc(PLATFORM_LABELS[ch.platform] || ch.platform) + '</b></span>' +
          (owner ? '<span>ID ' + esc(ch.external_id) + '</span>' : '') +
          '<span class="badge ' + (ch.enabled ? 'badge-admin' : 'badge-member') + '">' + (ch.enabled ? 'enabled' : 'disabled') + '</span>';
        row.appendChild(head);
        if (owner) {
          var creds = el('div', 'bot-card-assistant');
          creds.textContent = 'Token: ' + (ch.has_token ? 'set' : 'missing') + ' · App secret: ' + (ch.has_app_secret ? 'set' : 'missing');
          row.appendChild(creds);
          var vt = el('div', 'bot-card-assistant');
          vt.innerHTML = 'Verify token: <code>' + esc(ch.verify_token) + '</code>';
          row.appendChild(vt);
        }
        var acts = el('div', 'card-actions');
        acts.style.marginTop = '8px';
        var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
        edit.onclick = function () {
          editingId = ch.id;
          advanced.open = true;
          platform.value = ch.platform;
          extId.value = ch.external_id;
          token.value = ''; appSecret.value = '';
          token.placeholder = ch.has_token ? 'Leave blank to keep current' : 'Access token';
          appSecret.placeholder = ch.has_app_secret ? 'Leave blank to keep current' : 'App secret';
          verifyToken.value = ch.verify_token || '';
          saveBtn.textContent = 'Save changes';
          cancelBtn.style.display = 'inline-flex';
          form.querySelector('h3').textContent = 'Edit channel';
          form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        };
        var toggle = el('button', 'btn btn-ghost btn-sm', ch.enabled ? 'Disable' : 'Enable');
        toggle.onclick = function () {
          api('PUT', '/api/bots/' + botId + '/channels/' + ch.id, { enabled: !ch.enabled })
            .then(function () { toast('Updated', true); loadList(); })
            .catch(function (e) { toast(e.message, false); });
        };
        var del = el('button', 'btn btn-danger btn-sm', 'Delete');
        del.onclick = function () {
          if (confirm('Remove this ' + (PLATFORM_LABELS[ch.platform] || ch.platform) + ' channel?')) {
            api('DELETE', '/api/bots/' + botId + '/channels/' + ch.id)
              .then(function () { toast('Removed', true); loadList(); })
              .catch(function (e) { toast(e.message, false); });
          }
        };
        acts.appendChild(edit); acts.appendChild(toggle); acts.appendChild(del);
        row.appendChild(acts);
        list.appendChild(row);
      });
    }

    loadList();
    return card;
  }

  // ---------- Editor ----------
  function viewEditor(botId) {
    var isNew = !botId;
    if (!canAdmin(state.user)) {
      var denied = el('div', 'container');
      var card = el('div', 'panel-card empty-state');
      card.innerHTML = '<strong>Chatbot settings are managed by an admin.</strong>' +
        '<p>Ask your workspace admin to make changes.</p>';
      denied.appendChild(card);
      return denied;
    }
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    var hwrap = el('div');
    hwrap.innerHTML = '<h2>' + (isNew ? 'New Chatbot' : 'Edit Chatbot') + '</h2>';
    head.appendChild(hwrap);
    var back = el('button', 'btn btn-ghost', '← Back');
    back.onclick = function () { location.hash = '#/'; };
    head.appendChild(back);
    container.appendChild(head);

    var layout = el('div', 'editor-layout');
    var form = el('div', 'form-card');
    form.innerHTML = '<h3>Settings</h3>';

    function field(labelText, inputEl, hint) {
      var f = el('div', 'field');
      f.innerHTML = '<label>' + esc(labelText) + '</label>';
      f.appendChild(inputEl);
      if (hint) f.appendChild(el('div', 'hint', hint));
      return f;
    }

    var name = el('input'); name.placeholder = 'Client / business name';
    var assistant = el('input'); assistant.placeholder = 'e.g. Cheryl';
    var brandRow = el('div', 'color-row');
    var color = el('input'); color.type = 'color'; color.value = '#4f46e5';
    var colorText = el('input'); colorText.placeholder = '#4f46e5'; colorText.style.maxWidth = '130px';
    color.oninput = function () { colorText.value = color.value; syncPreview(); };
    colorText.oninput = function () { if (/^#[0-9a-fA-F]{6}$/.test(colorText.value)) { color.value = colorText.value; syncPreview(); } };
    brandRow.appendChild(color); brandRow.appendChild(colorText);
    var logo = el('input'); logo.type = 'url'; logo.placeholder = 'https://… (optional)';
    var welcome = el('textarea'); welcome.placeholder = 'Hi! How can I help you today?'; welcome.style.minHeight = '64px';
    var kb = el('textarea'); kb.placeholder = 'Paste the knowledge base here…'; kb.style.minHeight = '200px';
    var prompt = el('textarea'); prompt.placeholder = 'Optional: custom AI instructions (leave blank for default)'; prompt.style.minHeight = '80px';
    var webhook = el('input'); webhook.type = 'url'; webhook.placeholder = 'https://hooks.zapier.com/… (optional)';

    var quickReplies = [];
    var chipList = el('div', 'chip-list');
    var chipInput = el('div', 'chip-input');
    var chipText = el('input'); chipText.placeholder = 'Add a suggested question and press Enter';
    var chipAdd = el('button', 'btn btn-ghost btn-sm', 'Add');
    function renderChips() {
      chipList.innerHTML = '';
      quickReplies.forEach(function (q, i) {
        var c = el('div', 'chip');
        c.appendChild(document.createTextNode(q));
        var x = el('button', null, '×');
        x.onclick = function () { quickReplies.splice(i, 1); renderChips(); };
        c.appendChild(x);
        chipList.appendChild(c);
      });
    }
    function addChip(text) {
      var t = (text || '').trim();
      if (!t) return;
      quickReplies.push(t);
      renderChips();
      syncPreview();
    }
    chipAdd.onclick = function () { addChip(chipText.value); chipText.value = ''; };
    chipText.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); addChip(chipText.value); chipText.value = ''; } };
    chipInput.appendChild(chipText); chipInput.appendChild(chipAdd);

    form.appendChild(field('Name', name));
    form.appendChild(field('Assistant name', assistant));
    form.appendChild(field('Brand color', brandRow));
    form.appendChild(field('Logo URL', logo, 'Optional — otherwise initials are shown'));
    form.appendChild(field('Welcome message', welcome));
    form.appendChild(field('Suggested questions', chipList));
    form.appendChild(chipInput);
    form.appendChild(field('Knowledge base', kb, 'The AI answers ONLY from this text.'));
    form.appendChild(field('AI personality (optional)', prompt));
    form.appendChild(field('Webhook URL', webhook, 'Optional — the AI posts its WEBHOOK_PAYLOAD here (instruct it via the knowledge base)'));

    var save = el('button', 'btn btn-primary', isNew ? 'Create Chatbot' : 'Save Changes');
    save.onclick = saveFn;
    form.appendChild(save);
    layout.appendChild(form);

    var pane = el('div', 'preview-pane');
    var frame = el('div', 'preview-frame');
    pane.appendChild(frame);
    pane.appendChild(el('div', 'preview-note', 'Live widget preview'));
    layout.appendChild(pane);
    container.appendChild(layout);

    if (!isNew) {
      container.appendChild(buildChannelsPanel(botId));
    } else {
      var channelNote = el('div', 'muted');
      channelNote.style.marginTop = '16px';
      channelNote.textContent = 'Save this chatbot first, then you can connect Meta channels.';
      container.appendChild(channelNote);
    }

    var currentBot = { name: '', assistant_name: 'Assistant', brand_color: '#4f46e5', logo_url: '', welcome_message: '', quick_replies: [], knowledge_base: '', system_prompt: '' };

    function syncPreview() {
      currentBot.name = name.value;
      currentBot.assistant_name = assistant.value;
      currentBot.brand_color = color.value;
      currentBot.logo_url = logo.value;
      currentBot.welcome_message = welcome.value;
      currentBot.quick_replies = quickReplies;
      renderPreview();
    }

    function renderPreview() {
      var b = currentBot;
      var c = b.brand_color || '#4f46e5';
      frame.innerHTML =
        '<div style="position:absolute;right:16px;bottom:16px;width:60px;height:60px;border-radius:50%;background:' + esc(c) + ';box-shadow:0 8px 24px rgba(0,0,0,.25)"></div>' +
        '<div style="position:absolute;right:16px;bottom:92px;width:320px;max-width:calc(100% - 32px);background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 20px 50px rgba(0,0,0,.25);font-size:13px">' +
          '<div style="background:' + esc(c) + ';color:#fff;padding:12px 14px;font-weight:600">' + esc(b.assistant_name || 'Assistant') + '</div>' +
          '<div style="padding:14px;background:#f8fafc;min-height:120px">' +
            '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:8px 10px;max-width:88%">' + esc(b.welcome_message || 'Hi! How can I help you?') + '</div>' +
            (b.quick_replies && b.quick_replies.length ? '<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">' + b.quick_replies.map(function (q) { return '<span style="background:#eef2ff;color:#4338ca;padding:4px 9px;border-radius:999px;font-size:11px">' + esc(q) + '</span>'; }).join('') + '</div>' : '') +
          '</div>' +
          '<div style="padding:10px;border-top:1px solid #e5e7eb"><div style="border:1px solid #e5e7eb;border-radius:8px;padding:8px 10px;color:#9ca3af">Type your message…</div></div>' +
        '</div>';
    }

    if (isNew) {
      renderPreview();
    } else {
      api('GET', '/api/bots/' + botId).then(function (d) {
        var b = d.bot;
        name.value = b.name; assistant.value = b.assistant_name; color.value = b.brand_color; colorText.value = b.brand_color;
        logo.value = b.logo_url; welcome.value = b.welcome_message; kb.value = b.knowledge_base; prompt.value = b.system_prompt;
        webhook.value = b.webhook_url || '';
        quickReplies = (b.quick_replies || []).slice(); renderChips();
        currentBot = b; renderPreview();
      }).catch(function (e) { toast(e.message, false); location.hash = '#/'; });
    }

    [name, assistant, logo, welcome, kb, prompt].forEach(function (i) { i.oninput = syncPreview; });

    function saveFn() {
      var payload = {
        name: name.value.trim(),
        assistant_name: assistant.value.trim(),
        brand_color: color.value,
        logo_url: logo.value.trim(),
        welcome_message: welcome.value.trim(),
        quick_replies: quickReplies,
        knowledge_base: kb.value,
        system_prompt: prompt.value.trim(),
        webhook_url: webhook.value.trim()
      };
      if (!payload.name) { toast('Name is required', false); return; }
      save.disabled = true;
      var req = isNew ? api('POST', '/api/bots', payload) : api('PUT', '/api/bots/' + botId, payload);
      req.then(function (d) {
        toast(isNew ? 'Chatbot created' : 'Saved', true);
        if (isNew) location.hash = '#/bot/' + d.bot.id;
        else render();
      }).catch(function (e) { toast(e.message, false); }).finally(function () { save.disabled = false; });
    }

    return container;
  }

  // ---------- Stats ----------
  function viewStats(botId) {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Bot stats</h2></div>';
    var back = el('button', 'btn btn-ghost', '← Back');
    back.onclick = function () { location.hash = '#/bot/' + botId; };
    head.appendChild(back);
    container.appendChild(head);

    var loading = el('div', 'empty-state', 'Loading…');
    container.appendChild(loading);

    api('GET', '/api/bots/' + botId + '/stats').then(function (d) {
      loading.remove();
      var grid = el('div', 'stats-grid');
      grid.innerHTML =
        '<div class="stat-card"><div class="stat-value">' + d.total_messages + '</div><div class="stat-label">Total messages</div></div>' +
        '<div class="stat-card"><div class="stat-value">' + d.total_conversations + '</div><div class="stat-label">Conversations</div></div>';
      container.appendChild(grid);

      var chartCard = el('div', 'panel-card');
      chartCard.appendChild(el('h3', null, 'Messages — last 14 days'));
      var bars = el('div', 'bars');
      if (!d.messages_per_day.length) {
        chartCard.appendChild(el('div', 'muted', 'No messages yet.'));
      } else {
        var max = 1;
        d.messages_per_day.forEach(function (r) { if (r.count > max) max = r.count; });
        d.messages_per_day.forEach(function (r) {
          var bar = el('div', 'bar');
          bar.style.height = Math.max(2, Math.round((r.count / max) * 100)) + '%';
          bar.title = r.date + ': ' + r.count;
          bar.appendChild(el('span', null, (r.date || '').slice(5)));
          bars.appendChild(bar);
        });
        chartCard.appendChild(bars);
      }
      container.appendChild(chartCard);

      var topCard = el('div', 'panel-card');
      topCard.appendChild(el('h3', null, 'Most-asked questions'));
      if (!d.top_questions.length) {
        topCard.appendChild(el('div', 'muted', 'No questions recorded yet.'));
      } else {
        d.top_questions.forEach(function (q) {
          var row = el('div', 'top-item');
          row.innerHTML = '<span>' + esc(q.question) + '</span><span class="count">' + q.count + '×</span>';
          topCard.appendChild(row);
        });
      }
      container.appendChild(topCard);
    }).catch(function (e) { loading.textContent = e.message; });

    return container;
  }

  // ---------- People ----------
  // The same table serves the agency directory and a client's Team page. The only
  // difference is whether the row carries a client and whether the owner role is an
  // option. The server enforces the same split; this is not the security boundary.
  function personRoleLabel(role) {
    if (role === 'owner' || role === 'admin') return 'Agency owner';
    if (role === 'client_admin') return 'Client admin';
    if (role === 'member' || role === 'client_member') return 'Member';
    return String(role || '').replace(/_/g, ' ');
  }

  function personRoleBadge(role) {
    return '<span class="badge ' + (role === 'member' || role === 'client_member'
      ? 'badge-member' : 'badge-admin') + '">' + esc(personRoleLabel(role)) + '</span>';
  }

  function personStatusBadge(status) {
    var paused = status === 'suspended';
    return '<span class="badge ' + (paused ? 'badge-danger' : 'badge-success') + '">' +
      (paused ? 'Suspended' : 'Active') + '</span>';
  }

  function linkModal(title, message, link, failed) {
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal');
    modal.appendChild(el('h3', null, title));
    modal.appendChild(el('p', failed ? 'form-error' : 'muted', message));
    if (link) {
      // The database only keeps a hash, so this is the only moment the link exists.
      var input = el('input');
      input.value = link;
      input.readOnly = true;
      modal.appendChild(input);
      var copy = el('button', 'btn btn-primary', 'Copy link');
      copy.onclick = function () { copyText(link, copy); };
      modal.appendChild(copy);
    }
    var close = el('button', 'btn btn-ghost', 'Close');
    close.onclick = function () { backdrop.remove(); };
    modal.appendChild(close);
    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);
  }

  function personModal(person, platformMode, orgs, onSaved, fixedOrgId) {
    var isEdit = !!(person && person.id);
    var fixedOrg = fixedOrgId !== undefined && fixedOrgId !== null && fixedOrgId !== '';
    var roleOptions = platformMode && !fixedOrg
      ? [{ value: 'owner', label: 'Agency owner' },
         { value: 'client_admin', label: 'Client admin' },
         { value: 'member', label: 'Member' }]
      : [{ value: 'client_admin', label: 'Client admin' },
         { value: 'member', label: 'Member' }];
    var fields = [
      { name: 'name', label: 'Name', value: isEdit ? person.name : '' },
      { name: 'email', label: 'Email', type: 'email', value: isEdit ? person.email : '' }
    ];
    if (!isEdit) {
      fields.push({ name: 'password', label: 'Initial password', type: 'password',
        help: 'At least 8 characters. They can change it after signing in.' });
    }
    fields.push({ name: 'role', label: 'Role', type: 'select', options: roleOptions,
      value: isEdit ? person.role : 'member' });
    if (platformMode && !fixedOrg) {
      fields.push({
        name: 'org_id', label: 'Client', type: 'select',
        options: [{ value: '', label: 'No client (agency)' }].concat(
          (orgs || []).map(function (o) { return { value: String(o.id), label: o.name }; })
        ),
        value: isEdit && person.org_id ? String(person.org_id) : '',
        help: 'Agency owners have no client. Client roles need a workspace.'
      });
    }
    formModal(isEdit ? 'Edit person' : 'Add person', fields, function (data, fail, ok) {
      var payload = { name: data.name, email: data.email, role: data.role };
      if (!isEdit) payload.password = data.password;
      if (platformMode) {
        if (fixedOrg) {
          payload.org_id = fixedOrgId;
        } else {
          if (data.role !== 'owner' && !data.org_id) {
            fail('Choose a client for a client-admin or member account.');
            return;
          }
          payload.org_id = data.role === 'owner' ? null : (data.org_id || null);
        }
      }
      var url = platformMode ? '/api/platform/users' : '/api/users';
      if (isEdit) url += '/' + person.id;
      api(isEdit ? 'PUT' : 'POST', url, payload).then(function () {
        ok();
        toast(isEdit ? 'Person updated' : 'Person added', true);
        onSaved();
      }).catch(function (e) { fail(e.message); });
    }, isEdit ? 'Save changes' : 'Add person');
  }

  function passwordModal(person, platformMode, onSaved) {
    formModal('Reset password for ' + person.name, [
      { name: 'new_password', label: 'New password', type: 'password',
        help: 'At least 8 characters. They will use it the next time they sign in.' }
    ], function (data, fail, ok) {
      var url = (platformMode ? '/api/platform/users/' : '/api/users/') +
        person.id + '/password';
      api('POST', url, { new_password: data.new_password }).then(function () {
        ok();
        toast('Password reset', true);
        if (onSaved) onSaved();
      }).catch(function (e) { fail(e.message); });
    }, 'Reset password');
  }

  function changePersonStatus(person, platformMode, onSaved) {
    var next = person.status === 'active' ? 'suspended' : 'active';
    if (next === 'suspended' && !confirm('Suspend ' + person.email +
        '? They will be signed out and cannot sign in until re-enabled.')) return;
    var base = platformMode ? '/api/platform/users/' : '/api/users/';
    api('POST', base + person.id + '/status', { status: next }).then(function () {
      toast(next === 'active' ? 'Person re-enabled' : 'Person suspended', true);
      onSaved();
    }).catch(function (e) { toast(e.message, false); });
  }

  function removePerson(person, platformMode, onSaved) {
    if (!confirm('Remove ' + person.email + '? This cannot be undone.')) return;
    var base = platformMode ? '/api/platform/users/' : '/api/users/';
    api('DELETE', base + person.id).then(function () {
      toast('Person removed', true);
      onSaved();
    }).catch(function (e) { toast(e.message, false); });
  }

  function peopleTable(users, platformMode, orgs, onChanged, fixedOrgId) {
    var fixedOrg = fixedOrgId !== undefined && fixedOrgId !== null && fixedOrgId !== '';
    var showClient = platformMode && !fixedOrg;
    var table = el('table', 'table people-table');
    table.innerHTML = '<thead><tr><th>Name</th><th>Email</th><th>Role</th>' +
      (showClient ? '<th>Client</th>' : '') +
      '<th>Status</th><th>Last login</th><th></th></tr></thead>';
    var tbody = el('tbody');
    if (!users.length) {
      var empty = el('tr');
      empty.innerHTML = '<td colspan="' + (showClient ? 7 : 6) +
        '" class="muted">No people match this view.</td>';
      tbody.appendChild(empty);
    }
    users.forEach(function (u) {
      var tr = el('tr');
      tr.innerHTML = '<td><strong>' + esc(u.name) + '</strong></td>' +
        '<td>' + esc(u.email) + '</td>' +
        '<td>' + personRoleBadge(u.role) + '</td>' +
        (showClient ? '<td class="muted">' + esc(u.org_name || 'Agency') + '</td>' : '') +
        '<td>' + personStatusBadge(u.status) + '</td>' +
        '<td class="muted">' + (u.last_login_at ? esc(shortTime(u.last_login_at)) : 'Never') + '</td>';
      var td = el('td', 'row-actions');
      var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
      edit.onclick = function () {
        personModal(u, platformMode, orgs || [], onChanged, fixedOrgId);
      };
      td.appendChild(edit);
      var pw = el('button', 'btn btn-ghost btn-sm', 'Reset password');
      pw.onclick = function () { passwordModal(u, platformMode, onChanged); };
      td.appendChild(pw);
      var toggle = el('button', 'btn btn-ghost btn-sm',
        u.status === 'active' ? 'Disable' : 'Enable');
      toggle.onclick = function () { changePersonStatus(u, platformMode, onChanged); };
      td.appendChild(toggle);
      if (!state.user || u.id !== state.user.id) {
        var del = el('button', 'btn btn-danger btn-sm', 'Remove');
        del.onclick = function () { removePerson(u, platformMode, onChanged); };
        td.appendChild(del);
      }
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function inviteRows(invites, orgs, onChanged) {
    var wrap = el('div');
    if (!invites.length) {
      wrap.appendChild(el('p', 'muted', 'No pending invitations.'));
      return wrap;
    }
    var table = el('table', 'table');
    table.innerHTML = '<thead><tr><th>Email</th><th>Role</th>' +
      (orgs ? '<th>Client</th>' : '') + '<th>Status</th><th>Expires</th><th></th></tr></thead>';
    var tbody = el('tbody');
    var names = {};
    (orgs || []).forEach(function (o) { names[o.id] = o.name; });
    invites.forEach(function (invite) {
      var tr = el('tr');
      tr.innerHTML = '<td>' + esc(invite.email) + '</td>' +
        '<td>' + personRoleBadge(invite.role) + '</td>' +
        (orgs ? '<td class="muted">' + esc(names[invite.org_id] || '—') + '</td>' : '') +
        '<td><span class="badge">' + esc(invite.status) + '</span></td>' +
        '<td class="muted">' + esc((invite.expires_at || '').slice(0, 10)) + '</td>';
      var td = el('td', 'row-actions');
      if (invite.status !== 'accepted') {
        var resend = el('button', 'btn btn-ghost btn-sm', 'Resend');
        resend.onclick = function () {
          resend.disabled = true;
          api('POST', '/api/invites/' + invite.id + '/resend', {}).then(function (res) {
            linkModal('New link', 'Emailed to ' + res.invite.email + '.', res.link, false);
            onChanged();
          }).catch(function (e) {
            var body = e.data || {};
            if (body.link) {
              linkModal('Email not sent', e.message, body.link, true);
              onChanged();
            } else {
              toast(e.message, false);
              resend.disabled = false;
            }
          });
        };
        td.appendChild(resend);
        if (invite.status === 'pending') {
          var revoke = el('button', 'btn btn-danger btn-sm', 'Revoke');
          revoke.onclick = function () {
            api('POST', '/api/invites/' + invite.id + '/revoke', {}).then(onChanged)
              .catch(function (e) { toast(e.message, false); });
          };
          td.appendChild(revoke);
        }
      } else {
        td.appendChild(el('span', 'muted',
          'accepted ' + (invite.accepted_at || '').slice(0, 10)));
      }
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function invitePersonModal(platformMode, orgs, defaultOrgId, onSaved) {
    var fields = [
      { name: 'email', label: 'Email address', type: 'email' },
      { name: 'name', label: 'Their name (optional)' },
      { name: 'role', label: 'Role', type: 'select',
        options: [{ value: 'client_admin', label: 'Client admin' },
                  { value: 'member', label: 'Member' }] }
    ];
    if (platformMode) {
      fields.push({
        name: 'org_id', label: 'Client', type: 'select',
        options: [{ value: '', label: 'Choose a client…' }].concat(
          (orgs || []).map(function (o) {
            return { value: String(o.id), label: o.name };
          })
        ),
        value: defaultOrgId ? String(defaultOrgId) : ''
      });
    }
    formModal('Invite someone', fields, function (data, fail, ok) {
      var payload = { email: data.email, name: data.name, role: data.role };
      if (platformMode) {
        if (!data.org_id) { fail('Choose a client to invite.'); return; }
        payload.org_id = data.org_id;
      }
      api('POST', '/api/invites', payload).then(function (res) {
        ok();
        linkModal('Invitation sent', 'Emailed to ' + res.invite.email + '.', res.link, false);
        onSaved();
      }).catch(function (e) {
        var body = e.data || {};
        if (body.invite) {
          ok();
          linkModal('Invitation created, email not sent', e.message, body.link, true);
          onSaved();
        } else {
          fail(e.message);
        }
      });
    }, 'Send invitation');
  }

  function viewUsers() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Team</h2><p>People who can sign in to this workspace.</p></div>';
    var actions = el('div', 'page-actions');
    var add = el('button', 'btn btn-primary', '+ Add person');
    add.onclick = function () { personModal(null, false, [], load); };
    var invite = el('button', 'btn btn-ghost', 'Invite someone');
    invite.onclick = function () { invitePersonModal(false, [], null, load); };
    actions.appendChild(add);
    actions.appendChild(invite);
    head.appendChild(actions);
    container.appendChild(head);

    var people = [];
    var invites = [];
    var search = el('input');
    search.type = 'search';
    search.placeholder = 'Search team by name or email';
    search.className = 'search-input';
    var role = el('select');
    role.innerHTML = '<option value="">All roles</option>' +
      '<option value="client_admin">Client admin</option><option value="member">Member</option>';
    var status = el('select');
    status.innerHTML = '<option value="">All statuses</option>' +
      '<option value="active">Active</option><option value="suspended">Suspended</option>';
    var bar = el('div', 'chip-input');
    bar.appendChild(search); bar.appendChild(role); bar.appendChild(status);
    container.appendChild(bar);

    var body = el('div', 'mt');
    var inviteBox = el('div', 'mt');
    container.appendChild(body);
    container.appendChild(inviteBox);

    function draw() {
      var needle = (search.value || '').trim().toLowerCase();
      var shown = people.filter(function (u) {
        if (needle && (u.name + ' ' + u.email).toLowerCase().indexOf(needle) === -1) return false;
        if (role.value && u.role !== role.value) return false;
        if (status.value && u.status !== status.value) return false;
        return true;
      });
      body.innerHTML = '';
      body.appendChild(peopleTable(shown, false, [], load));
      inviteBox.innerHTML = '';
      var heading = el('div', 'page-head');
      heading.innerHTML = '<div><h3>Pending invitations</h3><p class="muted">' +
        'Each link works once. Resending retires the previous link.</p></div>';
      inviteBox.appendChild(heading);
      inviteBox.appendChild(inviteRows(invites, null, load));
    }

    function load() {
      api('GET', '/api/users').then(function (d) {
        people = d.users || [];
        draw();
      }).catch(function (e) { toast(e.message, false); });
      api('GET', '/api/invites').then(function (d) {
        invites = d.invites || [];
        draw();
      }).catch(function () { /* Team still works if invitations are unavailable */ });
    }
    search.oninput = draw;
    role.onchange = draw;
    status.onchange = draw;
    load();
    return container;
  }

  // ---------- Conversations ----------
  function fmtDate(s) {
    if (!s) return '';
    var d = new Date(s.replace(' ', 'T') + 'Z');
    return isNaN(d.getTime()) ? s : d.toLocaleString();
  }

  // Short, human time for a chat row: "just now", "3h ago", "Yesterday", "4d ago".
  function shortTime(value) {
    if (!value) return '';
    var d = new Date(String(value).replace(' ', 'T') + 'Z');
    if (isNaN(d.getTime())) return String(value).slice(0, 10);
    var minutes = Math.floor((Date.now() - d.getTime()) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return minutes + 'm ago';
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + 'h ago';
    var days = Math.floor(hours / 24);
    if (days === 1) return 'Yesterday';
    if (days < 7) return days + 'd ago';
    return String(value).slice(0, 10);
  }

  function viewConversations(botId) {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Conversations</h2><p>Visitor chats and captured leads.</p></div>';
    var back = el('button', 'btn btn-ghost', '← Back');
    back.onclick = function () { location.hash = '#/'; };
    head.appendChild(back);
    container.appendChild(head);

    var allBtn = el('button', 'btn btn-primary btn-sm', 'All');
    var leadsBtn = el('button', 'btn btn-ghost btn-sm', 'Leads only');
    var showLeadsOnly = false;
    var filterBar = el('div');
    filterBar.style.cssText = 'display:flex;gap:8px;margin-bottom:16px;';
    function setFilter(leads) {
      showLeadsOnly = leads;
      allBtn.className = 'btn ' + (leads ? 'btn-ghost' : 'btn-primary') + ' btn-sm';
      leadsBtn.className = 'btn ' + (leads ? 'btn-primary' : 'btn-ghost') + ' btn-sm';
      renderList();
    }
    allBtn.onclick = function () { setFilter(false); };
    leadsBtn.onclick = function () { setFilter(true); };
    filterBar.appendChild(allBtn);
    filterBar.appendChild(leadsBtn);
    container.appendChild(filterBar);

    var listEl = el('div');
    container.appendChild(listEl);
    var conversations = [];

    function renderList() {
      listEl.innerHTML = '';
      var shown = showLeadsOnly ? conversations.filter(function (c) { return c.lead; }) : conversations;
      if (!shown.length) {
        var empty = el('div', 'empty-state');
        empty.innerHTML = '<div class="big">' + icon('chat') + '</div>' +
          '<strong>' + (showLeadsOnly ? 'No leads captured yet.' : 'No conversations yet.') +
          '</strong>' +
          '<p>' + (showLeadsOnly
            ? 'When someone shares their details in a chat, they will appear here.'
            : 'They appear here as soon as someone talks to one of your chatbots.') + '</p>';
        listEl.appendChild(empty);
        return;
      }
      shown.forEach(function (c) {
        var card = el('div', 'bot-card');
        var meta = el('div', 'bot-card-meta');
        meta.innerHTML = '<span>' + fmtDate(c.last_at) + '</span><span>' + c.message_count + ' msgs</span>';
        card.appendChild(meta);
        if (c.lead) {
          var lead = el('div', 'bot-card-assistant');
          lead.innerHTML = '<b>Lead:</b> ' + esc(c.lead.name || '(no name)') + ' &middot; ' + esc(c.lead.email);
          card.appendChild(lead);
        }
        var preview = el('div', 'muted');
        preview.textContent = c.preview || '(no message)';
        preview.style.fontSize = '13px';
        card.appendChild(preview);
        var actions = el('div', 'card-actions');
        var view = el('button', 'btn btn-ghost btn-sm', 'View transcript');
        view.onclick = function () { showTranscript(botId, c.conversation_id); };
        actions.appendChild(view);
        card.appendChild(actions);
        listEl.appendChild(card);
      });
    }

    api('GET', '/api/bots/' + botId + '/conversations').then(function (d) {
      conversations = d.conversations || [];
      renderList();
    }).catch(function (e) { toast(e.message, false); });

    return container;
  }

  function showTranscript(botId, convId) {
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal');
    modal.style.maxWidth = '640px';
    modal.appendChild(el('h3', null, 'Conversation'));
    var body = el('div');
    body.style.cssText = 'max-height:60vh;overflow-y:auto;padding:8px 0;';
    body.appendChild(el('div', 'muted', 'Loading…'));
    modal.appendChild(body);
    var actions = el('div', 'modal-actions');
    var close = el('button', 'btn btn-ghost', 'Close');
    close.onclick = function () { backdrop.remove(); };
    actions.appendChild(close);
    modal.appendChild(actions);
    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);

    api('GET', '/api/bots/' + botId + '/conversations/' + convId).then(function (d) {
      body.innerHTML = '';
      (d.messages || []).forEach(function (m) {
        var row = el('div');
        row.style.marginBottom = '10px';
        var label = el('div', 'bot-card-assistant', (m.role === 'user' ? 'Visitor' : 'Bot') + ' · ' + fmtDate(m.created_at));
        label.style.fontSize = '11px';
        var content = el('div');
        content.textContent = m.content;
        content.style.cssText = 'margin-top:3px;padding:8px 11px;border-radius:10px;font-size:14px;line-height:1.45;white-space:pre-wrap;' + (m.role === 'user' ? 'background:#eef2ff;color:#3730a3;' : 'background:#f3f4f6;color:#1f2937;');
        row.appendChild(label);
        row.appendChild(content);
        body.appendChild(row);
      });
    }).catch(function (e) {
      body.innerHTML = '';
      body.appendChild(el('div', 'muted', e.message));
    });
  }

  // ---------- Settings (operator-managed configuration) ----------
  function sourceBadge(field) {
    if (field.readonly) return '<span class="badge badge-status">.env</span>';
    if (field.source === 'panel') return '<span class="badge badge-done">panel</span>';
    if (field.source === 'environment') return '<span class="badge badge-status">environment</span>';
    return '<span class="badge badge-status">unset</span>';
  }

  function boolFrom(value) {
    var low = String(value == null ? '' : value).trim().toLowerCase();
    return low === '1' || low === 'true' || low === 'yes' || low === 'on';
  }

  function fieldInput(field) {
    var input;
    if (field.kind === 'choice') {
      input = el('select');
      (field.choices || []).forEach(function (value) {
        var opt = el('option', null, value);
        opt.value = value;
        if (value === field.value) opt.selected = true;
        input.appendChild(opt);
      });
    } else if (field.kind === 'bool') {
      input = el('input');
      input.type = 'checkbox';
      input.checked = boolFrom(field.value);
    } else {
      input = el('input');
      if (field.kind === 'int') { input.type = 'number'; input.min = '0'; }
      else input.type = field.secret ? 'password' : 'text';
      if (field.secret) {
        // Never pre-fill a secret: a blank box means "leave it as it is".
        input.placeholder = field.set ? ('saved ' + (field.preview || '')) : 'not set';
        input.autocomplete = 'new-password';
      } else {
        input.value = field.value == null ? '' : field.value;
      }
    }
    if (field.readonly) input.disabled = true;
    return input;
  }

  function clientSettingsCard(group, orgId, refresh) {
    var card = el('div', 'panel-card settings-group');
    var head = el('div', 'settings-group-head');
    head.innerHTML = '<span class="settings-icon">' + icon(group.icon || 'mail') +
      '</span><div><h3>' + esc(group.label) + '</h3><div class="muted">' +
      esc(group.blurb || '') + '</div></div>';
    card.appendChild(head);

    var inputs = {};
    (group.fields || []).forEach(function (field) {
      var row = el('div', 'settings-row');
      var label = el('label', 'settings-label');
      label.setAttribute('for', 'set-' + field.key);
      label.innerHTML = '<span class="settings-name">' + esc(field.label) + '</span>';
      row.appendChild(label);

      var right = el('div', 'settings-input');
      var input = fieldInput(field);
      input.id = 'set-' + field.key;
      inputs[field.key] = input;
      right.appendChild(input);
      row.appendChild(right);
      if (field.help) row.appendChild(el('div', 'hint', field.help));
      card.appendChild(row);
    });

    var actions = el('div', 'card-actions');
    var save = el('button', 'btn btn-primary btn-sm', 'Save');
    save.type = 'button';
    save.onclick = function () { saveGroup(group, inputs, refresh, orgId); };
    actions.appendChild(save);
    if (group.test) {
      var test = el('button', 'btn btn-ghost btn-sm', 'Test connection');
      test.type = 'button';
      test.onclick = function () {
        test.disabled = true;
        api('POST', '/api/settings/test/' + group.id,
            orgId ? { org_id: orgId } : undefined).then(function (r) {
          toast(r.output, !!r.ok);
        }).catch(function (e) { toast(e.message, false); })
          .finally(function () { test.disabled = false; });
      };
      actions.appendChild(test);
    }
    card.appendChild(actions);
    return card;
  }

  function clientSettingsCards(host, orgId, refresh, emptyText) {
    var url = '/api/settings' + (orgId ? '?org_id=' + encodeURIComponent(orgId) : '');
    api('GET', url).then(function (d) {
      host.innerHTML = '';
      var groups = d.groups || [];
      if (!groups.length) {
        host.appendChild(el('p', 'muted',
          emptyText || 'Email settings are managed by your agency.'));
        return;
      }
      groups.forEach(function (group) {
        host.appendChild(clientSettingsCard(group, orgId, refresh));
      });
    }).catch(function (e) {
      host.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
    });
  }

  function viewClientSettings() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Workspace settings</h2><p>Choose the mailbox the platform ' +
      'uses when it sends email for you, and control how much the AI can do in ' +
      'Analytics. Email is optional: leave it blank and we will use the agency ' +
      'default.</p></div>';
    container.appendChild(head);

    var note = el('div', 'panel-card');
    note.innerHTML = '<p class="muted">Your email password is stored securely and is never ' +
      'shown again. Analytics automation needs full setup consent on the Analytics page ' +
      'and can be switched off here or there at any time.</p>' +
      '<p class="fine-print">Need help? Ask your agency to set this up with you.</p>';
    container.appendChild(note);

    var groupsWrap = el('div', 'mt');
    container.appendChild(groupsWrap);

    function refresh() {
      clientSettingsCards(groupsWrap, null, refresh,
        'Workspace settings are managed by your agency.');
    }

    refresh();
    return container;
  }

  function viewSettings() {
    // A client only ever sees the email account they control. The operator keeps the
    // full platform catalogue: model credentials, voice, hosting, safety and audit
    // history belong on the backend screen, not in a client's workspace.
    if (!isOwner(state.user)) return viewClientSettings();

    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Settings</h2><p>Configuration for every subsystem. A value saved ' +
      'here is stored in the database and takes effect immediately; environment variables remain ' +
      'the fallback.</p></div>';
    container.appendChild(head);

    // What build is this? Listed first, because the first question about any oddity below
    // is whether you are looking at the code you think you are.
    var buildCard = el('div', 'panel-card');
    container.appendChild(buildCard);
    buildInfo().then(function (v) {
      buildCard.innerHTML = '<h3 class="card-title">Build</h3>' +
        '<p><strong>Version ' + esc(v.version || 'unknown') + '</strong></p>' +
        '<p class="fine-print">commit <code>' + esc(v.commit || 'unknown') + '</code>' +
        ' · built ' + esc(v.build_time || 'unknown') +
        ' · this process started ' + esc(v.started_at || 'unknown') +
        ' · python ' + esc(v.python || '?') + '</p>' +
        '<p class="fine-print">If that commit is not the one you deployed, the container was ' +
        'not recreated - run <code>./deploy.sh</code>, which recreates it and then checks the ' +
        'site answers.</p>';
    });

    var banner = el('div', 'panel-card');
    container.appendChild(banner);
    var groupsWrap = el('div', 'mt');
    container.appendChild(groupsWrap);
    var historyCard = el('div', 'panel-card mt');
    container.appendChild(historyCard);

    function refresh() {
      api('GET', '/api/settings').then(function (d) {
        banner.innerHTML = '<h3>How this works</h3><div class="muted">' +
          (d.dry_run
            ? 'Safety is set to <b>dry-run</b>: writes, SSH and publishing are simulated.'
            : 'Safety is set to <b>live</b>: side effects are real, and each one still asks for your approval.') +
          '<br>Values saved here <b>override</b> environment variables; use <b>Clear</b> to fall ' +
          'back to <code>.env</code>. Secrets are write-only - once saved they are never shown again.' +
          '</div>';
        groupsWrap.innerHTML = '';
        (d.groups || []).forEach(function (group) {
          groupsWrap.appendChild(groupCard(group, refresh));
        });
        renderHistory(historyCard, d.history || []);
      }).catch(function (e) { toast(e.message, false); });
    }

    refresh();
    return container;
  }

  function groupCard(group, refresh) {
    var card = el('div', 'panel-card settings-group');
    var head = el('div', 'settings-group-head');
    head.innerHTML = '<span class="settings-icon">' + icon(group.icon || 'sliders') + '</span>' +
      '<div><h3>' + esc(group.label) + '</h3>' +
      '<div class="muted">' + esc(group.blurb || '') + '</div></div>';
    card.appendChild(head);

    var inputs = {};
    (group.fields || []).forEach(function (field) {
      var row = el('div', 'settings-row');
      var label = el('label', 'settings-label');
      label.setAttribute('for', 'set-' + field.key);
      label.innerHTML = '<span class="settings-name">' + esc(field.label) + '</span>' +
        sourceBadge(field) +
        (field.applies === 'restart' ? '<span class="badge badge-warn">restart</span>' : '');
      row.appendChild(label);

      var right = el('div', 'settings-input');
      var input = fieldInput(field);
      input.id = 'set-' + field.key;
      inputs[field.key] = input;
      right.appendChild(input);
      if (field.source === 'panel' && !field.readonly) {
        var clear = el('button', 'btn btn-ghost btn-sm', 'Clear');
        clear.type = 'button';
        clear.title = 'Remove this value and fall back to .env';
        clear.onclick = function () { clearOne(field.key, refresh); };
        right.appendChild(clear);
      }
      row.appendChild(right);
      if (field.help) row.appendChild(el('div', 'hint', field.help));
      card.appendChild(row);
    });

    var actions = el('div', 'card-actions');
    var save = el('button', 'btn btn-primary btn-sm', 'Save');
    save.type = 'button';
    save.onclick = function () { saveGroup(group, inputs, refresh); };
    actions.appendChild(save);

    if (group.test) {
      var test = el('button', 'btn btn-ghost btn-sm', 'Test');
      test.type = 'button';
      test.onclick = function () {
        test.disabled = true;
        api('POST', '/api/settings/test/' + group.id).then(function (r) {
          toast(r.output, !!r.ok);
        }).catch(function (e) { toast(e.message, false); });
        test.disabled = false;
      };
      actions.appendChild(test);
    }
    card.appendChild(actions);
    return card;
  }

  function saveGroup(group, inputs, refresh, orgId) {
    var values = {};
    var risky = null;
    (group.fields || []).forEach(function (field) {
      if (field.readonly) return;
      var input = inputs[field.key];
      var value = field.kind === 'bool' ? (input.checked ? '1' : '0') : input.value;
      // A blank secret means "keep what is stored" - the form never holds it.
      if (field.secret && !value) return;
      if (!field.secret && String(value) === String(field.value == null ? '' : field.value)) return;
      if (field.risky) risky = field;
      values[field.key] = value;
    });
    if (!Object.keys(values).length) { toast('Nothing changed', true); return; }

    if (risky) {
      var turningOn = boolFrom(values[risky.key]);
      var ask = risky.label + ' -> ' + (turningOn ? 'ON' : 'OFF') + '\n\n' +
        (risky.help || '') + '\n\nContinue?';
      if (!confirm(ask)) return;
    }

    var payload = { values: values };
    if (orgId) payload.org_id = orgId;
    api('PUT', '/api/settings', payload).then(function (r) {
      toast('Saved ' + ((r.changed || []).length) + ' setting(s)', true);
      refresh();
    }).catch(function (e) { toast(e.message, false); });
  }

  function clearOne(key, refresh, orgId) {
    if (!confirm('Clear ' + key + ' and fall back to the environment?')) return;
    var payload = { clear: [key] };
    if (orgId) payload.org_id = orgId;
    api('PUT', '/api/settings', payload).then(function () {
      toast('Cleared ' + key, true);
      refresh();
    }).catch(function (e) { toast(e.message, false); });
  }

  function renderHistory(card, rows) {
    var html = '<h3>Recent changes</h3>';
    if (!rows.length) {
      card.innerHTML = html + '<div class="muted">Nothing recorded yet.</div>';
      return;
    }
    html += '<table class="table"><thead><tr><th>When</th><th>Key</th><th>Action</th>' +
      '<th>By</th><th>Detail</th></tr></thead><tbody>';
    rows.forEach(function (row) {
      html += '<tr><td>' + esc(fmtDate(row.created_at)) + '</td>' +
        '<td><code>' + esc(row.key) + '</code></td>' +
        '<td>' + esc(row.action) + '</td>' +
        '<td>' + esc(row.actor) + '</td>' +
        '<td>' + esc(row.detail) + '</td></tr>';
    });
    card.innerHTML = html + '</tbody></table>';
  }

  // ---------- Placeholder & index views ----------
  function isSoonTab(id) {
    for (var i = 0; i < NAV.length; i++) {
      if (NAV[i].id === id && NAV[i].ready === false) return true;
    }
    return false;
  }

  function viewSoon(id) {
    var meta = null;
    NAV.forEach(function (item) { if (item.id === id) meta = item; });
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>' + esc(meta ? meta.label : id) + '</h2><p>Coming soon.</p></div>';
    container.appendChild(head);
    var panel = el('div', 'panel-card');
    // meta.icon is a sprite symbol name, not a glyph - rendering it as text was
    // the bug this replaces.
    panel.innerHTML = '<div class="empty-state"><div class="big">' +
      icon(meta && meta.icon ? meta.icon : 'alert') +
      '</div><strong>This section is planned</strong><p>See SYSTEM_PLAN.md for the roadmap.</p></div>';
    container.appendChild(panel);
    return container;
  }

  function viewConversationsIndex() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Conversations</h2><p>Pick a chatbot to review its visitor chats and captured leads.</p></div>';
    container.appendChild(head);

    var grid = el('div', 'grid');
    container.appendChild(grid);

    api('GET', '/api/bots').then(function (d) {
      var bots = d.bots || [];
      if (!bots.length) {
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">' + icon('chat') + '</div><strong>No conversations yet</strong><p>They appear here as soon as someone talks to one of your chatbots.</p></div>';
        return;
      }
      bots.forEach(function (bot) {
        var card = el('div', 'bot-card');
        var top = el('div', 'bot-card-top');
        var av = el('div', 'bot-avatar');
        av.style.background = bot.brand_color || '#4f46e5';
        av.innerHTML = avatarHTML(bot);
        top.appendChild(av);
        var names = el('div');
        names.innerHTML = '<div class="bot-card-name">' + esc(bot.name) + '</div>' +
          '<div class="bot-card-assistant">' + (bot.message_count || 0) + ' messages</div>';
        top.appendChild(names);
        card.appendChild(top);
        var open = el('button', 'btn btn-primary btn-sm', 'View conversations');
        open.onclick = function () { location.hash = '#/bot/' + bot.id + '/conversations'; };
        card.appendChild(open);
        grid.appendChild(card);
      });
    }).catch(function (e) { toast(e.message, false); });

    return container;
  }

  // ---------- Business Profile ----------
  function viewProfile() {
    var container = el('div', 'container');
    var owner = isOwner(state.user);
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Business details</h2><p>What your team knows about ' +
      'your business, and how it should write for you. It can learn writing rules from ' +
      'chat or voice; changes to facts wait for your approval.</p></div>';
    container.appendChild(head);

    var pendingCard = el('div', 'panel-card');
    pendingCard.style.display = 'none';
    container.appendChild(pendingCard);

    var inputs = {};
    var services = [];
    var faq = [];
    var rules = [];
    var SOCIAL_KEYS = ['facebook', 'instagram', 'x', 'linkedin', 'tiktok'];

    function section(title, hint) {
      var card = el('div', 'panel-card');
      card.appendChild(el('h3', null, title));
      if (hint) card.appendChild(el('div', 'muted', hint));
      return card;
    }

    function field(label, name, opts) {
      opts = opts || {};
      var wrap = el('div', 'field');
      wrap.innerHTML = '<label>' + esc(label) + '</label>';
      var input = opts.multiline ? el('textarea', 'prompt-box') : el('input');
      if (opts.placeholder) input.placeholder = opts.placeholder;
      if (opts.type) input.type = opts.type;
      inputs[name] = input;
      wrap.appendChild(input);
      return wrap;
    }

    function row() {
      var r = el('div', 'form-row');
      for (var i = 0; i < arguments.length; i++) r.appendChild(arguments[i]);
      return r;
    }

    var identity = section('Your business',
      'The basics your team should never get wrong.');
    identity.appendChild(row(
      field('Business name', 'name', { placeholder: 'Acme Ltd' }),
      field('Industry', 'industry', { placeholder: 'Retail, SaaS, agency...' })
    ));
    identity.appendChild(field('Tagline', 'tagline', { placeholder: 'One line that sums you up' }));
    identity.appendChild(field('Website', 'website', { placeholder: 'https://example.com' }));
    identity.appendChild(field('About / description', 'description', {
      multiline: true, placeholder: 'What you do, who you help, and why you are different.'
    }));
    container.appendChild(identity);

    var branding = section('Brand appearance (optional)',
      'Used on your chatbot and any pages we generate.');
    branding.appendChild(row(
      field('Brand colour', 'brand_color', { type: 'color' }),
      field('Font family', 'brand_font', { placeholder: 'Inter, Poppins...' })
    ));
    branding.appendChild(field('Logo URL', 'logo_url', { placeholder: 'https://example.com/logo.png' }));
    container.appendChild(branding);

    var voice = section('How your team should write',
      'Who you help, and the voice you want people to hear.');
    voice.appendChild(field('Target audience', 'target_audience', {
      multiline: true, placeholder: 'Small business owners in the UK who want more leads.'
    }));
    voice.appendChild(field('Tone of voice', 'tone_of_voice', {
      multiline: true, placeholder: 'Friendly, concise, never pushy.'
    }));
    voice.appendChild(field('Signature', 'signature', {
      multiline: true, placeholder: 'Nathanael\nFounder, Acme Ltd\nacme.com'
    }));
    container.appendChild(voice);

    // Rules are instructions, not facts. The Manager can add a line automatically when it
    // learns a lasting preference; one row per rule makes that list reviewable by someone
    // who is not technical. A line deleted here stops being applied.
    var preferences = section('Rules your team has learned',
      'Your team adds a rule here when you tell it something lasting in chat or by voice, ' +
      'such as “keep emails short” or “never use exclamation marks.” Review or change ' +
      'anything.');
    var rulesWrap = el('div');
    function renderRules() {
      rulesWrap.innerHTML = '';
      rules.forEach(function (rule, index) {
        var row = el('div', 'repeat-row');
        var input = el('input');
        input.className = 'rule-input';
        input.value = rule || '';
        input.placeholder = 'A rule your team should follow';
        input.oninput = function () { rules[index] = input.value; };
        var del = el('button', 'btn btn-danger btn-sm', 'Remove');
        del.onclick = function () { rules.splice(index, 1); renderRules(); };
        row.appendChild(input);
        row.appendChild(del);
        rulesWrap.appendChild(row);
      });
      if (!rules.length) {
        rulesWrap.appendChild(el('div', 'muted',
          'No rules yet. When you tell your team something lasting in chat or by voice, ' +
          'it will appear here.'));
      }
      var add = el('button', 'btn btn-ghost btn-sm', '+ Add a rule yourself');
      add.onclick = function () { rules.push(''); renderRules(); };
      rulesWrap.appendChild(add);
    }
    preferences.appendChild(rulesWrap);
    container.appendChild(preferences);

    // Offerings
    var offerings = section('What you offer',
      'Services, products or packages your team can mention accurately.');
    var servicesWrap = el('div');
    function renderServices() {
      servicesWrap.innerHTML = '';
      services.forEach(function (item, index) {
        var r = el('div', 'repeat-row');
        var sName = el('input'); sName.placeholder = 'Service'; sName.value = item.name || '';
        var sPrice = el('input'); sPrice.placeholder = 'Price'; sPrice.value = item.price || '';
        var sDesc = el('input'); sDesc.placeholder = 'Short description'; sDesc.value = item.description || '';
        sName.oninput = function () { item.name = sName.value; };
        sPrice.oninput = function () { item.price = sPrice.value; };
        sDesc.oninput = function () { item.description = sDesc.value; };
        var del = el('button', 'btn btn-danger btn-sm', 'Remove');
        del.onclick = function () { services.splice(index, 1); renderServices(); };
        r.appendChild(sName); r.appendChild(sPrice); r.appendChild(sDesc); r.appendChild(del);
        servicesWrap.appendChild(r);
      });
      var add = el('button', 'btn btn-ghost btn-sm', '+ Add service');
      add.onclick = function () { services.push({ name: '', price: '', description: '' }); renderServices(); };
      servicesWrap.appendChild(add);
    }
    offerings.appendChild(servicesWrap);
    offerings.appendChild(field('Pricing notes', 'pricing_notes', {
      multiline: true, placeholder: 'Deposits, custom quotes, VAT...'
    }));
    container.appendChild(offerings);

    var contact = section('How customers reach you');
    contact.appendChild(row(
      field('Support email', 'support_email', { placeholder: 'hello@example.com' }),
      field('Support phone', 'support_phone', { placeholder: '+44 ...' })
    ));
    contact.appendChild(field('Address', 'address', { placeholder: '123 High Street, London' }));
    contact.appendChild(field('Business hours', 'business_hours', { placeholder: 'Mon-Fri 9:00-17:00' }));
    container.appendChild(contact);

    var socialCard = section('Social links (optional)');
    var socialRow = el('div', 'form-row');
    SOCIAL_KEYS.forEach(function (key) {
      socialRow.appendChild(field(key.charAt(0).toUpperCase() + key.slice(1), 'soc_' + key, { placeholder: 'https://' }));
    });
    socialCard.appendChild(socialRow);
    container.appendChild(socialCard);

    var policiesCard = section('Policies (optional)');
    policiesCard.appendChild(field('Refunds, shipping, guarantees', 'policies', { multiline: true }));
    container.appendChild(policiesCard);

    var faqCard = section('Common questions',
      'The questions customers ask most, with the answers you want them to hear.');
    var faqWrap = el('div');
    function renderFaq() {
      faqWrap.innerHTML = '';
      faq.forEach(function (item, index) {
        var box = el('div', 'repeat-block');
        var q = el('input'); q.placeholder = 'Question'; q.value = item.q || '';
        var a = el('textarea', 'prompt-box'); a.placeholder = 'Answer'; a.value = item.a || '';
        q.oninput = function () { item.q = q.value; };
        a.oninput = function () { item.a = a.value; };
        var del = el('button', 'btn btn-danger btn-sm', 'Remove');
        del.onclick = function () { faq.splice(index, 1); renderFaq(); };
        box.appendChild(q); box.appendChild(a); box.appendChild(del);
        faqWrap.appendChild(box);
      });
      var add = el('button', 'btn btn-ghost btn-sm', '+ Add question');
      add.onclick = function () { faq.push({ q: '', a: '' }); renderFaq(); };
      faqWrap.appendChild(add);
    }
    faqCard.appendChild(faqWrap);
    container.appendChild(faqCard);

    var notesCard = section('Anything else your team should know');
    notesCard.appendChild(field('Anything else your team should know', 'extra_notes',
      { multiline: true }));
    container.appendChild(notesCard);

    var previewCard = section('What your team will read',
      'This is the information we combine for your team. You normally only edit the ' +
      'sections above.');
    var previewBody = el('pre', 'kb-preview');
    previewBody.style.display = 'none';
    var previewSummary = el('div', 'muted');
    var previewMeta = el('div', 'muted');
    var toggle = el('button', 'btn btn-ghost btn-sm', 'Show exact text');
    toggle.onclick = function () {
      var hidden = previewBody.style.display === 'none';
      previewBody.style.display = hidden ? 'block' : 'none';
      toggle.textContent = hidden ? 'Hide exact text' : 'Show exact text';
    };
    var refresh = el('button', 'btn btn-ghost btn-sm', 'Refresh');
    refresh.onclick = function () { refreshPreview(); };
    var previewBar = el('div', 'chip-input');
    previewBar.appendChild(toggle);
    previewBar.appendChild(refresh);
    previewCard.appendChild(previewBar);
    previewCard.appendChild(previewSummary);
    previewCard.appendChild(previewMeta);
    previewCard.appendChild(previewBody);
    container.appendChild(previewCard);

    var actions = el('div');
    actions.style.cssText = 'display:flex;gap:8px;margin-bottom:24px;';
    var saveBtn = el('button', 'btn btn-primary', 'Save changes');
    var sampleBtn = el('button', 'btn btn-ghost', 'Load example content');
    var savedMeta = el('span', 'muted');
    actions.appendChild(saveBtn);
    if (owner) actions.appendChild(sampleBtn);
    actions.appendChild(savedMeta);
    container.appendChild(actions);

    function refreshPreview() {
      api('GET', '/api/profile/knowledge').then(function (d) {
        previewBody.textContent = d.knowledge || '(empty - fill in some sections above)';
        previewMeta.textContent = d.chars + ' characters used';
      }).catch(function (e) { previewMeta.textContent = e.message; });
    }

    function collect() {
      var payload = {};
      Object.keys(inputs).forEach(function (key) {
        if (key.indexOf('soc_') === 0) return;
        payload[key] = inputs[key].value;
      });
      payload.social = {};
      SOCIAL_KEYS.forEach(function (key) { payload.social[key] = inputs['soc_' + key].value; });
      payload.services = services;
      payload.faq = faq;
      payload.preferences = rules.map(function (rule) { return String(rule || '').trim(); })
        .filter(Boolean).join('\n');
      return payload;
    }

    function load(prof) {
      prof = prof || {};
      Object.keys(inputs).forEach(function (key) {
        if (key.indexOf('soc_') === 0) return;
        inputs[key].value = prof[key] == null ? '' : prof[key];
      });
      SOCIAL_KEYS.forEach(function (key) {
        inputs['soc_' + key].value = ((prof.social || {})[key]) || '';
      });
      services = (prof.services || []).slice();
      faq = (prof.faq || []).slice();
      rules = String(prof.preferences || '').split('\n')
        .map(function (line) { return line.trim(); }).filter(Boolean);
      savedMeta.textContent = prof.updated_at ? 'Last saved ' + prof.updated_at : '';
      var known = [];
      if (prof.name) known.push('your business name');
      if (prof.description) known.push('what you do');
      if ((prof.services || []).length) known.push('what you offer');
      if (prof.support_email || prof.support_phone || prof.address) {
        known.push('how to reach you');
      }
      if ((prof.faq || []).length) known.push('common questions');
      if (rules.length) known.push('the rules you taught it');
      previewSummary.textContent = known.length
        ? 'This includes ' + known.join(', ') + '.'
        : 'Fill in the sections above and your team will use them here.';
      renderServices();
      renderFaq();
      renderRules();
    }

    saveBtn.onclick = function () {
      saveBtn.disabled = true;
      api('PUT', '/api/profile', collect())
        .then(function (d) {
          toast('Changes saved', true);
          load(d.profile);
          savedMeta.textContent = 'Saved just now';
          refreshPreview();
        })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { saveBtn.disabled = false; });
    };

    sampleBtn.onclick = function () {
      if (!confirm('Replace the current profile with example content?')) return;
      api('POST', '/api/profile/seed-sample')
        .then(function (d) { toast('Example loaded', true); load(d.profile); refreshPreview(); })
        .catch(function (e) { toast(e.message, false); });
    };

    function decidePending(id, decision) {
      api('POST', '/api/approvals/' + id + '/' + decision).then(function () {
        toast(decision === 'approve' ? 'Added to your business details' : 'Suggestion dismissed', true);
        api('GET', '/api/profile').then(function (d) {
          load(d.profile);
          refreshPreview();
        }).catch(function (e) { toast(e.message, false); });
        refreshPending();
      }).catch(function (e) { toast(e.message, false); });
    }

    function refreshPending() {
      api('GET', '/api/profile/suggestions').then(function (d) {
        var rows = d.suggestions || [];
        pendingCard.innerHTML = '';
        pendingCard.style.display = rows.length ? '' : 'none';
        if (!rows.length) return;
        pendingCard.innerHTML = '<h3>Waiting for your approval</h3>' +
          '<p class="muted">Your team suggested a change to these details. ' +
          'Nothing is saved until you approve it.</p>';
        rows.forEach(function (approval) {
          var row = el('div', 'approval-row');
          var summary = el('div');
          summary.appendChild(el('div', null, approval.summary || 'Business details update'));
          var proposed = '';
          try {
            proposed = String((JSON.parse(approval.args_json || '{}') || {}).value || '');
          } catch (e) { proposed = ''; }
          if (proposed) summary.appendChild(el('div', 'muted', 'New value: ' + proposed));
          row.appendChild(summary);
          var buttons = el('div', 'card-actions');
          var yes = el('button', 'btn btn-primary btn-sm', 'Add to my details');
          yes.onclick = function () { yes.disabled = true; decidePending(approval.id, 'approve'); };
          var no = el('button', 'btn btn-ghost btn-sm', 'Not now');
          no.onclick = function () { no.disabled = true; decidePending(approval.id, 'deny'); };
          buttons.appendChild(yes);
          buttons.appendChild(no);
          row.appendChild(buttons);
          pendingCard.appendChild(row);
        });
      }).catch(function () { pendingCard.style.display = 'none'; });
    }

    renderServices();
    renderFaq();
    renderRules();
    api('GET', '/api/profile')
      .then(function (d) { load(d.profile); refreshPreview(); })
      .catch(function (e) { toast(e.message, false); });
    refreshPending();



    return container;
  }


  // ---------- Agents (designatable roster) ----------
  var PROVIDER_INFO = {};
  var ALL_TOOLS = [];

  function providerLabel(name) {
    var info = PROVIDER_INFO[name];
    return info ? info.label : name;
  }

  function viewAgents() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Agents</h2><p>Designate a role, provider, model and tools for each member of the team.</p></div>';
    var addBtn = el('button', 'btn btn-primary', '+ New agent');
    addBtn.onclick = function () { agentEditor(null, refresh); };
    head.appendChild(addBtn);
    container.appendChild(head);

    var intro = el('div', 'panel-card');
    intro.innerHTML = '<h3>Team routing</h3>';
    var meta = el('div', 'muted');
    intro.appendChild(meta);
    container.appendChild(intro);

    var grid = el('div', 'grid');
    container.appendChild(grid);

    function refresh() {
      api('GET', '/api/agents').then(function (d) {
        ALL_TOOLS = d.tools || [];
        PROVIDER_INFO = {};
        (d.providers || []).forEach(function (p) { PROVIDER_INFO[p.name] = p; });
        var configured = (d.providers || []).filter(function (p) { return p.configured; }).length;
        meta.innerHTML = 'Router: <b>' + esc(d.router.provider) + '</b>' +
          (d.router.model ? ' (' + esc(d.router.model) + ')' : ' (default model)') +
          ' · stops after ' + d.max_messages + ' messages or on APPROVE' +
          ' · ' + configured + '/' + (d.providers || []).length + ' providers configured';
        grid.innerHTML = '';
        var agentsList = d.agents || [];
        if (!agentsList.length) {
          grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">' + icon('cpu') + '</div><strong>No agents yet</strong><p>The roster is the team that writes and publishes your content.</p></div>';
          return;
        }
        agentsList.forEach(function (agent) { grid.appendChild(agentCard(agent, refresh)); });
      }).catch(function (e) { toast(e.message, false); });
    }

    refresh();
    return container;
  }

  function agentCard(agent, refresh) {
    var card = el('div', 'bot-card');
    var top = el('div', 'bot-card-top');
    var av = el('div', 'bot-avatar');
    av.style.background = agent.enabled ? '#4f46e5' : '#9ca3af';
    av.innerHTML = esc(initials(agent.name));
    top.appendChild(av);

    var names = el('div');
    names.innerHTML = '<div class="bot-card-name">' + esc(agent.name) +
      (agent.enabled ? '' : ' <span class="badge badge-status">off</span>') + '</div>' +
      '<div class="bot-card-assistant">' + esc(agent.role || '') + '</div>';
    top.appendChild(names);
    card.appendChild(top);

    var info = PROVIDER_INFO[agent.provider] || {};
    var infoRow = el('div', 'bot-card-meta');
    infoRow.innerHTML = '<span>' + esc(providerLabel(agent.provider)) + '</span>' +
      '<span>' + esc(agent.model || info.model || 'default') + '</span>' +
      '<span class="' + (info.configured ? '' : 'warn') + '">' + (info.configured ? 'ready' : 'no key') + '</span>';
    card.appendChild(infoRow);

    if (agent.tools && agent.tools.length) {
      var toolsRow = el('div', 'bot-card-meta muted');
      toolsRow.textContent = 'tools: ' + agent.tools.join(', ');
      card.appendChild(toolsRow);
    }

    var actions = el('div', 'card-actions');
    var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
    edit.onclick = function () { agentEditor(agent, refresh); };
    var toggle = el('button', 'btn btn-ghost btn-sm', agent.enabled ? 'Disable' : 'Enable');
    toggle.onclick = function () {
      api('PUT', '/api/agents/' + agent.id, { enabled: !agent.enabled })
        .then(refresh)
        .catch(function (e) { toast(e.message, false); });
    };
    var del = el('button', 'btn btn-danger btn-sm', 'Delete');
    del.onclick = function () {
      if (!confirm('Delete agent "' + agent.name + '"?')) return;
      api('DELETE', '/api/agents/' + agent.id)
        .then(function () { toast('Deleted', true); refresh(); })
        .catch(function (e) { toast(e.message, false); });
    };
    actions.appendChild(edit);
    actions.appendChild(toggle);
    actions.appendChild(del);
    card.appendChild(actions);
    return card;
  }

  function agentEditor(agent, onSaved) {
    agent = agent || {};
    var isNew = !agent.id;
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide');
    modal.innerHTML = '<h3>' + (isNew ? 'New agent' : 'Edit ' + esc(agent.name)) + '</h3>' +
      '<p class="muted">Each agent becomes one AutoGen AssistantAgent with its own model and tools.</p>';

    function field(label, input) {
      var wrap = el('div', 'field');
      wrap.innerHTML = '<label>' + esc(label) + '</label>';
      wrap.appendChild(input);
      return wrap;
    }

    var name = el('input'); name.value = agent.name || ''; name.placeholder = 'e.g. Image Artist';
    var role = el('input'); role.value = agent.role || ''; role.placeholder = 'What this agent is for';
    var provider = el('select');
    var providerNames = Object.keys(PROVIDER_INFO);
    if (!providerNames.length) providerNames = ['deepseek'];
    provider.innerHTML = providerNames.map(function (p) {
      var info = PROVIDER_INFO[p] || {};
      return '<option value="' + p + '"' + (agent.provider === p ? ' selected' : '') + '>' +
        esc(info.label || p) + (info.configured === false ? ' (no key)' : '') + '</option>';
    }).join('');
    var model = el('input'); model.value = agent.model || ''; model.placeholder = 'blank = provider default';
    var order = el('input'); order.type = 'number'; order.value = (agent.turn_order != null ? agent.turn_order : 0);
    var temp = el('input'); temp.type = 'number'; temp.step = '0.1';
    temp.value = (agent.temperature != null ? agent.temperature : '');
    temp.placeholder = 'e.g. 0.2';
    var prompt = el('textarea', 'prompt-box');
    prompt.value = agent.system_prompt || '';
    prompt.placeholder = 'Instructions for this agent.';

    var selectedTools = (agent.tools || []).slice();
    var toolsBox = el('div', 'chip-list');
    if (!ALL_TOOLS.length) {
      toolsBox.appendChild(el('div', 'muted', 'No tools registered.'));
    }
    ALL_TOOLS.forEach(function (tool) {
      var label = el('label', 'tool-toggle');
      label.title = tool.description || '';
      var box = el('input');
      box.type = 'checkbox';
      box.checked = selectedTools.indexOf(tool.name) >= 0;
      box.onchange = function () {
        var index = selectedTools.indexOf(tool.name);
        if (box.checked && index < 0) selectedTools.push(tool.name);
        if (!box.checked && index >= 0) selectedTools.splice(index, 1);
      };
      label.appendChild(box);
      label.appendChild(document.createTextNode(tool.name));
      toolsBox.appendChild(label);
    });

    modal.appendChild(field('Name', name));
    modal.appendChild(field('Role', role));
    modal.appendChild(field('Provider', provider));
    modal.appendChild(field('Model', model));
    modal.appendChild(field('Turn order', order));
    modal.appendChild(field('Temperature (optional)', temp));
    modal.appendChild(field('System prompt', prompt));
    modal.appendChild(field('Tools', toolsBox));

    var actions = el('div', 'modal-actions');
    var cancel = el('button', 'btn btn-ghost', 'Cancel');
    cancel.onclick = function () { backdrop.remove(); };
    var save = el('button', 'btn btn-primary', 'Save');
    save.onclick = function () {
      var payload = {
        name: name.value,
        role: role.value,
        provider: provider.value,
        model: model.value,
        system_prompt: prompt.value,
        tools: selectedTools,
        turn_order: parseInt(order.value, 10) || 0,
        temperature: temp.value
      };
      save.disabled = true;
      var request = isNew
        ? api('POST', '/api/agents', payload)
        : api('PUT', '/api/agents/' + agent.id, payload);
      request
        .then(function () { toast('Saved', true); backdrop.remove(); if (onSaved) onSaved(); })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { save.disabled = false; });
    };
    actions.appendChild(cancel);
    actions.appendChild(save);
    modal.appendChild(actions);

    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);
  }


  // ---------- Sites (hosting) ----------
  var SITE_STATUS_CLASS = {
    pending_dns: 'badge-queued', dns_ok: 'badge-running', live: 'badge-done', error: 'badge-failed'
  };
  var SITE_TARGETS = [];

  function siteStatusBadge(status, friendly) {
    var label = status;
    if (friendly) {
      label = ({ pending_dns: 'Setting up', dns_ok: 'Connecting', live: 'Live',
                 error: 'Needs attention' })[status] || 'Setting up';
    }
    return '<span class="badge ' + (SITE_STATUS_CLASS[status] || 'badge-status') + '">' +
      esc(label) + '</span>';
  }

  function viewSites() {
    var owner = isOwner(state.user);
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = owner
      ? '<div><h2>Sites</h2><p>Point a domain at this server and the platform serves it over HTTPS.</p></div>'
      : '<div><h2>Website</h2><p>Connect your domain and we will set up the secure site for you.</p></div>';
    var manager = canAdmin(state.user);
    if (manager) {
      var addBtn = el('button', 'btn btn-primary',
        owner ? '+ New site' : '+ Connect a domain');
      addBtn.onclick = function () { siteEditor(null, refresh); };
      head.appendChild(addBtn);
    }
    container.appendChild(head);

    var banner = el('div', 'panel-card');
    container.appendChild(banner);
    var grid = el('div', 'grid');
    container.appendChild(grid);

    function refresh() {
      api('GET', '/api/sites?summary=1').then(function (d) {
        SITE_TARGETS = d.targets || [];
        banner.innerHTML = owner
          ? '<h3>How this works</h3><div class="muted">' +
            (d.dry_run
              ? 'Dry-run: nothing is written to disk or the web server until you set <code>ALLOW_LIVE_ACTIONS=1</code>.'
              : 'Live: provisioning runs the real commands and may ask for approval.') +
            (d.server_ip
              ? '<br>Server IP: <b>' + esc(d.server_ip) + '</b>'
              : '<br>Set <code>SERVER_IP</code> so the DNS hints are accurate.') +
            '</div>'
          : '<h3>Your website</h3><div class="muted">Add the domain you want to use. ' +
            'We will give you the DNS records and set up the certificate automatically.' +
            (d.server_ip ? '<br>Point your domain at <b>' + esc(d.server_ip) + '</b>.'
                         : '') + '</div>';
        grid.innerHTML = '';
        var sites = d.sites || [];
        if (!sites.length) {
          grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">' +
            icon('globe') + '</div><strong>No websites yet</strong><p>' +
            (manager
              ? 'Add a domain and point its DNS at this server to go live.'
              : 'Ask a workspace admin to connect your website.') +
            '</p></div>';
          return;
        }
        sites.forEach(function (site) { grid.appendChild(siteCard(site, refresh)); });
      }).catch(function (e) { toast(e.message, false); });
    }

    refresh();
    container.appendChild(pagesCard());
    return container;
  }

  function siteCard(site, refresh) {
    var owner = isOwner(state.user);
    var manager = canAdmin(state.user);
    var card = el('div', 'bot-card');
    var top = el('div', 'bot-card-top');
    var av = el('div', 'bot-avatar');
    av.style.background = site.status === 'live' ? '#16a34a' : (site.status === 'error' ? '#dc2626' : '#4f46e5');
    av.innerHTML = icon('globe');
    top.appendChild(av);
    var names = el('div');
    names.innerHTML = '<div class="bot-card-name">' + esc(site.domain) + '</div>' +
      '<div class="bot-card-assistant">' +
      (owner ? esc(site.target) + ' target'
             : (site.status === 'live' ? 'Live website'
                : (site.status === 'error' ? 'Needs attention' : 'Setting up'))) +
      '</div>';
    top.appendChild(names);
    card.appendChild(top);

    var meta = el('div', 'bot-card-meta');
    meta.innerHTML = '<span>' + siteStatusBadge(site.status, !owner) + '</span>' +
      '<span>' + (owner ? 'ssl: ' + esc(site.ssl_status)
                        : (site.ssl_status === 'issued' ? 'Secure certificate ready'
                           : 'Certificate pending')) + '</span>';
    card.appendChild(meta);

    if (site.url) {
      var link = el('div');
      link.innerHTML = '<a href="' + esc(site.url) + '" target="_blank" rel="noopener">' + esc(site.url) + '</a>';
      card.appendChild(link);
    }
    if (owner && site.last_error) card.appendChild(el('div', 'muted', site.last_error));
    if (owner && site.dns_last_ip) {
      card.appendChild(el('div', 'muted', 'resolves to ' + esc(site.dns_last_ip)));
    }

    var actions = el('div', 'card-actions');
    var dns = el('button', 'btn btn-ghost btn-sm', owner ? 'DNS & SSL' : 'Domain setup');
    // One implementation, in the Domains tab - this used to open a second, older modal.
    dns.onclick = function () { location.hash = '#/app/domains/' + site.id; };
    var check = el('button', 'btn btn-ghost btn-sm', owner ? 'Check DNS' : 'Check connection');
    check.onclick = function () {
      api('POST', '/api/sites/' + site.id + '/check').then(function (d) {
        toast(d.check.ok ? 'Your domain is connected'
                         : (owner ? 'DNS: ' + d.check.reason : 'The domain is not connected yet'),
              d.check.ok);
        refresh();
      }).catch(function (e) { toast(e.message, false); });
    };
    var provision = el('button', 'btn btn-primary btn-sm', owner ? 'Provision' : 'Publish website');
    provision.onclick = function () {
      provision.disabled = true;
      api('POST', '/api/sites/' + site.id + '/provision').then(function (d) {
        if (owner) {
          toast('Provisioning queued', true);
          location.hash = '#/jobs/' + d.job.id;
        } else {
          toast('We are preparing your website. This can take a moment.', true);
          refresh();
        }
      }).catch(function (e) { toast(e.message, false); })
        .finally(function () { provision.disabled = false; });
    };
    actions.appendChild(dns);
    actions.appendChild(check);
    if (manager) actions.appendChild(provision);
    if (owner) {
      var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
      edit.onclick = function () { siteEditor(site, refresh); };
      actions.appendChild(edit);
    }
    if (manager) {
      var del = el('button', 'btn btn-danger btn-sm', 'Remove domain');
      del.onclick = function () {
        if (!confirm('Remove ' + site.domain + '? ' +
                     'The website will stop being served. Your DNS records are ' +
                     'unchanged, and the website files and history are kept.')) return;
        del.disabled = true;
        api('DELETE', '/api/sites/' + site.id).then(function (res) {
          toast(res.dry_run
            ? (owner ? (res.message || 'Dry-run: nothing was removed')
                     : 'We could not remove the domain right now. Please contact your agency.')
            : (res.message || 'Removed ' + site.domain), true);
          refresh();
        }).catch(function (e) {
          del.disabled = false;
          toast(e.message, false);
        });
      };
      actions.appendChild(del);
    }
    card.appendChild(actions);
    return card;
  }

  function showDns(site) {
    api('GET', '/api/sites/' + site.id + '/dns').then(function (d) {
      var backdrop = el('div', 'modal-backdrop');
      var modal = el('div', 'modal');
      modal.innerHTML = '<h3>DNS for ' + esc(site.domain) + '</h3>' +
        '<p class="muted">Create these records at your DNS provider. Caddy issues the TLS certificate ' +
        'automatically once they resolve to this server.</p>';
      var table = el('table', 'table dns-table');
      table.innerHTML = '<thead><tr><th>Type</th><th>Name</th><th>Value</th><th>TTL</th></tr></thead>';
      var tbody = el('tbody');
      (d.records || []).forEach(function (record) {
        var tr = el('tr');
        tr.innerHTML = '<td>' + esc(record.type) + '</td><td><code>' + esc(record.name) + '</code></td>' +
          '<td><code>' + esc(record.value) + '</code></td><td>' + record.ttl + '</td>';
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      modal.appendChild(table);
      var status = el('div', 'muted');
      status.textContent = d.check.ok
        ? 'This domain already resolves here.'
        : 'Currently resolves to: ' + ((d.check.resolved || []).join(', ') || 'nothing');
      modal.appendChild(status);
      var actions = el('div', 'modal-actions');
      var close = el('button', 'btn btn-ghost', 'Close');
      close.onclick = function () { backdrop.remove(); };
      actions.appendChild(close);
      modal.appendChild(actions);
      backdrop.appendChild(modal);
      backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
      document.body.appendChild(backdrop);
    }).catch(function (e) { toast(e.message, false); });
  }

  function siteEditor(site, onSaved) {
    if (site && site.id && site.content_html === undefined) {
      // The list is a summary now; load the full site before editing its HTML.
      api('GET', '/api/sites/' + site.id).then(function (d) {
        siteEditor(d.site || site, onSaved);
      }).catch(function (e) { toast(e.message, false); });
      return;
    }
    site = site || {};
    var owner = isOwner(state.user);
    var isNew = !site.id;
    var mayLinkBot = !isNew && !!site.org_id && (owner || canAdmin(state.user));
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide');
    modal.innerHTML = owner
      ? '<h3>' + (isNew ? 'New site' : 'Edit ' + esc(site.domain)) + '</h3>' +
        '<p class="muted">The domain must already be pointed here (or be pointed after you save).</p>'
      : '<h3>Connect a domain</h3>' +
        '<p class="muted">Enter the domain you own. We will show you the records to add ' +
        'and set up the secure website.</p>';

    function field(label, input) {
      var wrap = el('div', 'field');
      wrap.innerHTML = '<label>' + esc(label) + '</label>';
      wrap.appendChild(input);
      return wrap;
    }

    var domain = el('input');
    domain.value = site.domain || '';
    domain.placeholder = 'example.com';
    domain.disabled = !isNew;

    var target = el('select');
    target.innerHTML = SITE_TARGETS.map(function (t) {
      return '<option value="' + esc(t.value) + '"' + (site.target === t.value ? ' selected' : '') + '>' +
        esc(t.label) + '</option>';
    }).join('') || '<option value="local">This server (local)</option>';

    var aliases = el('input');
    aliases.value = (site.aliases || []).join(', ');
    aliases.placeholder = 'www.example.com, blog.example.com';

    var docroot = el('input');
    docroot.value = site.docroot || '';
    docroot.placeholder = 'blank = automatic (/srv/sites/<domain>/public_html)';

    var content = el('textarea', 'prompt-box');
    content.value = site.content_html || '';
    content.placeholder = 'Leave blank to generate a landing page from your Business Profile.';

    var linkedBot = el('select');
    linkedBot.setAttribute('aria-label', 'Linked chatbot');
    var noBot = el('option', null, 'No chat assistant');
    noBot.value = '';
    linkedBot.appendChild(noBot);
    var currentBot = site.linked_bot_id || '';
    if (currentBot) {
      var currentOption = el('option', null, currentBot);
      currentOption.value = currentBot;
      linkedBot.appendChild(currentOption);
    }
    linkedBot.value = currentBot;
    api('GET', '/api/bots').then(function (d) {
      var seen = {};
      if (currentBot) seen[currentBot] = true;
      (d.bots || []).forEach(function (bot) {
        if (!bot || !bot.id) return;
        if (site.org_id && bot.org_id && bot.org_id !== site.org_id) return;
        if (seen[bot.id]) return;
        seen[bot.id] = true;
        var option = el('option', null, bot.name || bot.id);
        option.value = bot.id;
        linkedBot.appendChild(option);
      });
      linkedBot.value = currentBot;
    }).catch(function () { /* leave the current value visible */ });

    modal.appendChild(field('Domain', domain));
    if (owner) {
      modal.appendChild(field('Target', target));
      modal.appendChild(field('Aliases', aliases));
      modal.appendChild(field('Docroot', docroot));
      modal.appendChild(field('Content HTML', content));
    }
    if (mayLinkBot) {
      modal.appendChild(field('Linked chatbot', linkedBot));
    }

    var actions = el('div', 'modal-actions');
    var cancel = el('button', 'btn btn-ghost', 'Cancel');
    cancel.onclick = function () { backdrop.remove(); };
    var save = el('button', 'btn btn-primary', owner ? 'Save' : 'Continue');
    save.onclick = function () {
      var payload = owner
        ? {
            domain: domain.value.trim().toLowerCase(),
            target: target.value,
            aliases: aliases.value.split(',').map(function (a) { return a.trim(); }).filter(Boolean),
            docroot: docroot.value,
            content_html: content.value
          }
        : { domain: domain.value.trim().toLowerCase(), target: 'local' };
      if (mayLinkBot) payload.linked_bot_id = linkedBot.value;
      save.disabled = true;
      var request = isNew ? api('POST', '/api/sites', payload) : api('PUT', '/api/sites/' + site.id, payload);
      request
        .then(function () { toast('Saved', true); backdrop.remove(); if (onSaved) onSaved(); })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { save.disabled = false; });
    };
    actions.appendChild(cancel);
    actions.appendChild(save);
    modal.appendChild(actions);

    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);
  }

  // ---------- Servers (SSH targets) ----------
  function viewServers() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Servers</h2><p>Remote hosts the Operator agent may deploy to. Nothing runs without the policy check and your approval.</p></div>';
    var addBtn = el('button', 'btn btn-primary', '+ New server');
    addBtn.onclick = function () { hostEditor(null, refresh); };
    head.appendChild(addBtn);
    container.appendChild(head);

    var banner = el('div', 'panel-card');
    container.appendChild(banner);
    var tableWrap = el('div', 'mt');
    container.appendChild(tableWrap);

    function refresh() {
      api('GET', '/api/ssh-hosts').then(function (d) {
        banner.innerHTML = '<h3>Safety</h3><div class="muted">' +
          (d.dry_run
            ? 'Dry-run: no SSH connection is opened until you set <code>ALLOW_LIVE_ACTIONS=1</code>.'
            : 'Live: commands and uploads require your approval.') +
          '<br>Key-based auth only, host keys are pinned, and every command must pass the policy allowlist.' +
          '</div>';
        var hosts = d.hosts || [];
        if (!hosts.length) {
          tableWrap.innerHTML = '<div class="empty-state"><div class="big">' + icon('server') + '</div><strong>No servers yet</strong><p>Add an SSH target only if you deploy to hardware other than this one.</p></div>';
          return;
        }
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Label</th><th>Host</th><th>User</th><th>Root</th><th>State</th><th></th></tr></thead>';
        var tbody = el('tbody');
        hosts.forEach(function (host) {
          var tr = el('tr');
          tr.innerHTML = '<td>' + esc(host.label) + '</td>' +
            '<td><code>' + esc(host.host) + ':' + host.port + '</code></td>' +
            '<td>' + esc(host.username) + '</td>' +
            '<td><code>' + esc(host.sites_root || host.allowed_root) + '</code></td>' +
            '<td>' + (host.enabled ? '<span class="badge badge-done">enabled</span>'
                                   : '<span class="badge badge-status">off</span>') + '</td>';
          var td = el('td');
          var actions = el('div', 'card-actions');
          var test = el('button', 'btn btn-ghost btn-sm', 'Test');
          test.onclick = function () {
            api('POST', '/api/ssh-hosts/' + host.id + '/test').then(function (r) {
              toast(r.ok ? 'Connected: ' + r.output : (r.dry_run ? r.output : 'Failed: ' + r.output), r.ok);
            }).catch(function (e) { toast(e.message, false); });
          };
          var toggle = el('button', 'btn btn-ghost btn-sm', host.enabled ? 'Disable' : 'Enable');
          toggle.onclick = function () {
            api('PUT', '/api/ssh-hosts/' + host.id, { enabled: !host.enabled })
              .then(refresh).catch(function (e) { toast(e.message, false); });
          };
          var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
          edit.onclick = function () { hostEditor(host, refresh); };
          var del = el('button', 'btn btn-danger btn-sm', 'Delete');
          del.onclick = function () {
            if (!confirm('Delete server ' + host.label + '?')) return;
            api('DELETE', '/api/ssh-hosts/' + host.id)
              .then(function () { toast('Deleted', true); refresh(); })
              .catch(function (e) { toast(e.message, false); });
          };
          actions.appendChild(test);
          actions.appendChild(toggle);
          actions.appendChild(edit);
          actions.appendChild(del);
          td.appendChild(actions);
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        tableWrap.innerHTML = '';
        tableWrap.appendChild(table);
      }).catch(function (e) { toast(e.message, false); });
    }

    refresh();
    return container;
  }

  function hostEditor(host, onSaved) {
    host = host || {};
    var isNew = !host.id;
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide');
    modal.innerHTML = '<h3>' + (isNew ? 'New server' : 'Edit ' + esc(host.label)) + '</h3>' +
      '<p class="muted">Use a dedicated unprivileged deploy user. The private key stays on this server ' +
      'and is never sent to a model.</p>';

    function field(label, input) {
      var wrap = el('div', 'field');
      wrap.innerHTML = '<label>' + esc(label) + '</label>';
      wrap.appendChild(input);
      return wrap;
    }

    var label = el('input'); label.value = host.label || ''; label.placeholder = 'web1';
    var hostname = el('input'); hostname.value = host.host || ''; hostname.placeholder = '203.0.113.10';
    var port = el('input'); port.type = 'number'; port.value = host.port || 22;
    var username = el('input'); username.value = host.username || 'deploy'; username.placeholder = 'deploy';
    var keyPath = el('input'); keyPath.value = host.private_key_path || '';
    keyPath.placeholder = '/opt/ascendplatform/.ssh/id_deploy';
    var fingerprint = el('input'); fingerprint.value = host.host_key_fingerprint || '';
    fingerprint.placeholder = 'SHA256:... (from ssh-keyscan | ssh-keygen -lf -)';
    var sitesRoot = el('input'); sitesRoot.value = host.sites_root || host.allowed_root || '';
    sitesRoot.placeholder = '/home/deploy/sites';

    modal.appendChild(field('Label', label));
    modal.appendChild(field('Host', hostname));
    modal.appendChild(field('Port', port));
    modal.appendChild(field('Username', username));
    modal.appendChild(field('Private key path', keyPath));
    modal.appendChild(field('Host key fingerprint', fingerprint));
    modal.appendChild(field('Sites root (all writes are confined here)', sitesRoot));

    var actions = el('div', 'modal-actions');
    var cancel = el('button', 'btn btn-ghost', 'Cancel');
    cancel.onclick = function () { backdrop.remove(); };
    var save = el('button', 'btn btn-primary', 'Save');
    save.onclick = function () {
      var payload = {
        label: label.value,
        host: hostname.value,
        port: parseInt(port.value, 10) || 22,
        username: username.value,
        private_key_path: keyPath.value,
        host_key_fingerprint: fingerprint.value,
        allowed_root: sitesRoot.value,
        sites_root: sitesRoot.value
      };
      save.disabled = true;
      var request = isNew
        ? api('POST', '/api/ssh-hosts', payload)
        : api('PUT', '/api/ssh-hosts/' + host.id, payload);
      request
        .then(function () { toast('Saved', true); backdrop.remove(); if (onSaved) onSaved(); })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { save.disabled = false; });
    };
    actions.appendChild(cancel);
    actions.appendChild(save);
    modal.appendChild(actions);

    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);
  }



  // ---------- Agent jobs ----------
  // Tasks registered on the server (jobs.TASKS).
  var JOB_TASKS = ['agent_team', 'site_provision', 'echo'];
  var STATUS_CLASS = {
    queued: 'badge-queued',
    running: 'badge-running',
    done: 'badge-done',
    failed: 'badge-failed',
    cancelled: 'badge-cancelled'
  };

  function statusBadge(status) {
    return '<span class="badge ' + (STATUS_CLASS[status] || 'badge-status') + '">' + esc(status) + '</span>';
  }

  // "published" must never describe content the live page does not have. A draft with a
  // URL is the honest state after an edit: the old version is still live, and this one
  // needs Publish.
  function deliverableBadge(item) {
    if (item.status === 'draft' && item.url) {
      return '<span class="badge badge-warn">draft changes</span>';
    }
    return statusBadge(item.status);
  }

  function viewJobs() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Agent jobs</h2><p>Long-running AutoGen runs with a live transcript.</p></div>';
    container.appendChild(head);

    var form = el('div', 'form-card');
    form.innerHTML = '<h3>New job</h3>';
    function f(label, input) { var d = el('div', 'field'); d.innerHTML = '<label>' + label + '</label>'; d.appendChild(input); return d; }

    var prompt = el('textarea', 'prompt-box');
    prompt.placeholder = 'Describe the task, e.g. "Build a landing page for client X with a logo and a launch email"';
    form.appendChild(f('Prompt', prompt));

    var task = el('select');
    task.innerHTML = JOB_TASKS.map(function (t) { return '<option value="' + t + '">' + t + '</option>'; }).join('');
    form.appendChild(f('Task', task));

    var mode = el('select');
    mode.innerHTML = '<option value="selector">selector - a router picks who speaks</option>' +
      '<option value="round_robin">round_robin - fixed turn order</option>';
    form.appendChild(f('Team mode', mode));

    var runBtn = el('button', 'btn btn-primary', 'Run job');
    runBtn.onclick = function () {
      if (!prompt.value.trim()) { toast('Prompt is required', false); return; }
      runBtn.disabled = true;
      api('POST', '/api/jobs', { prompt: prompt.value.trim(), task: task.value, team_mode: mode.value })
        .then(function (d) { location.hash = '#/jobs/' + d.job.id; })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { runBtn.disabled = false; });
    };
    form.appendChild(runBtn);
    container.appendChild(form);

    var tableWrap = el('div', 'mt');
    container.appendChild(tableWrap);

    function refresh() {
      api('GET', '/api/jobs').then(function (d) {
        var jobs = d.jobs || [];
        if (!jobs.length) {
          tableWrap.innerHTML = '<div class="empty-state"><div class="big">' + icon('cog') + '</div><strong>No jobs yet</strong><p>Jobs are what the agent team runs; start one from the box above.</p></div>';
          return;
        }
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>ID</th><th>Title</th><th>Task</th><th>Status</th><th>Created</th><th></th></tr></thead>';
        var tbody = el('tbody');
        jobs.forEach(function (j) {
          var tr = el('tr');
          tr.innerHTML = '<td>' + j.id + '</td><td>' + esc(j.title) + '</td><td>' + esc(j.task) + '</td>' +
            '<td>' + statusBadge(j.status) + '</td><td>' + esc(fmtDate(j.created_at)) + '</td>';
          var td = el('td');
          var open = el('button', 'btn btn-ghost btn-sm', 'Open');
          open.onclick = function () { location.hash = '#/jobs/' + j.id; };
          td.appendChild(open);
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        tableWrap.innerHTML = '';
        tableWrap.appendChild(table);
      }).catch(function (e) { toast(e.message, false); });
    }

    refresh();
    var timer = setInterval(refresh, 3000);
    onCleanup(function () { clearInterval(timer); });

    return container;
  }

  // One destination for "work on this with the canvas open". The hash is the only way in:
  // the Studio owns its own thread and voice host, so a link is a fresh page, and an
  // in-page switch never touches location.hash (that would re-render the shell).
  function openInStudio(kind, id) {
    location.hash = '#/app/studio/' + kind + '/' + id;
  }

  function viewJob(jobId) {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Job #' + esc(jobId) + '</h2><p class="muted">Live transcript</p></div>';
    var back = el('button', 'btn btn-ghost', '← Back');
    back.onclick = function () { location.hash = '#/jobs'; };
    head.appendChild(back);
    var studio = el('button', 'btn btn-primary', 'Open in Studio');
    studio.onclick = function () { openInStudio('job', jobId); };
    head.appendChild(studio);
    container.appendChild(head);

    var meta = el('div', 'panel-card');
    container.appendChild(meta);

    var log = el('div', 'panel-card');
    log.innerHTML = '<h3>Transcript</h3>';
    var transcript = el('div', 'transcript');
    log.appendChild(transcript);
    container.appendChild(log);

    var actions = el('div');
    actions.style.cssText = 'display:flex;gap:8px;';
    var cancel = el('button', 'btn btn-danger btn-sm', 'Cancel job');
    cancel.onclick = function () {
      api('POST', '/api/jobs/' + jobId + '/cancel')
        .then(function () { toast('Cancellation requested', true); poll(); })
        .catch(function (e) { toast(e.message, false); });
    };
    actions.appendChild(cancel);
    container.appendChild(actions);

    var approvalsCard = el('div', 'panel-card');
    approvalsCard.innerHTML = '<h3>Approvals</h3>';
    var approvalsBody = el('div');
    approvalsCard.appendChild(approvalsBody);
    container.appendChild(approvalsCard);

    var approvalTimer = null;

    function decide(id, decision) {
      api('POST', '/api/approvals/' + id + '/' + decision)
        .then(function () { toast('Recorded', true); refreshApprovals(); })
        .catch(function (e) { toast(e.message, false); });
    }

    function refreshApprovals() {
      api('GET', '/api/approvals?status=pending').then(function (d) {
        approvalsBody.innerHTML = '';
        if (d.dry_run) {
          approvalsBody.appendChild(el('div', 'muted',
            'Dry-run mode: write and exec actions are simulated, so nothing needs approval.'));
          return;
        }
        var rows = (d.approvals || []).filter(function (a) {
          return a.job_id == null || Number(a.job_id) === Number(jobId);
        });
        if (!rows.length) {
          approvalsBody.appendChild(el('div', 'muted', 'Nothing waiting for approval.'));
          return;
        }
        rows.forEach(function (approval) {
          var row = el('div', 'approval-row');
          row.appendChild(el('div', null, approval.tool + ' - ' + approval.summary));
          var buttons = el('div', 'card-actions');
          var ok = el('button', 'btn btn-primary btn-sm', 'Approve');
          ok.onclick = function () { decide(approval.id, 'approve'); };
          var no = el('button', 'btn btn-danger btn-sm', 'Deny');
          no.onclick = function () { decide(approval.id, 'deny'); };
          buttons.appendChild(ok);
          buttons.appendChild(no);
          row.appendChild(buttons);
          approvalsBody.appendChild(row);
        });
      }).catch(function () {});
    }

    refreshApprovals();
    approvalTimer = setInterval(refreshApprovals, 3000);
    onCleanup(function () { if (approvalTimer) clearInterval(approvalTimer); });

    var lastSeq = 0;
    var timer = null;
    var source = null;

    function renderMeta(job, artifacts) {
      meta.innerHTML = '<div class="event-head"><span>Status: ' + statusBadge(job.status) +
        '</span><span>Task: ' + esc(job.task) + '</span></div>' +
        '<div class="muted" style="font-size:13px;margin-top:8px;">' + esc(job.prompt) + '</div>' +
        (job.result ? '<div class="mt"><b>Result:</b> ' + esc(job.result) + '</div>' : '') +
        (job.error ? '<div class="mt" style="color:#991b1b;"><b>Error:</b> ' + esc(job.error) + '</div>' : '') +
        (artifacts && artifacts.length ? '<div class="mt"><b>Artifacts:</b> ' + artifacts.length + '</div>' : '');
    }

    function appendEvents(events) {
      events.forEach(function (ev) {
        lastSeq = Math.max(lastSeq, ev.seq);
        var node = el('div', 'event type-' + ev.type);
        var eh = el('div', 'event-head');
        eh.innerHTML = '<span>#' + ev.seq + ' · ' + esc(ev.source) + '</span><span>' + esc(ev.type) + '</span>';
        node.appendChild(eh);
        node.appendChild(el('div', 'event-body', ev.content));
        transcript.appendChild(node);
      });
      transcript.scrollTop = transcript.scrollHeight;
    }

    function poll() {
      api('GET', '/api/jobs/' + jobId + '/events?after=' + lastSeq).then(function (d) {
        renderMeta(d.job, d.artifacts);
        appendEvents(d.events || []);
      }).catch(function (e) { toast(e.message, false); });
    }

    function closeStream() {
      if (source) { source.close(); source = null; }
      if (timer) { clearInterval(timer); timer = null; }
    }

    // Live transcript over Server-Sent Events, with a polling fallback.
    function startStream() {
      if (typeof EventSource === 'undefined') {
        timer = setInterval(poll, 1500);
        return;
      }
      source = new EventSource('/api/jobs/' + jobId + '/stream?after=' + lastSeq);
      ['message', 'system', 'error', 'tool_call', 'tool_result'].forEach(function (kind) {
        source.addEventListener(kind, function (evt) {
          try { appendEvents([JSON.parse(evt.data)]); } catch (e) {}
        });
      });
      source.addEventListener('end', function () {
        closeStream();
        poll();
      });
      source.onerror = function () {
        closeStream();
        toast('Live stream interrupted - polling instead', false);
        timer = setInterval(poll, 1500);
      };
    }

    // Catch up on existing events, then stream anything new.
    api('GET', '/api/jobs/' + jobId + '/events?after=0').then(function (d) {
      renderMeta(d.job, d.artifacts);
      appendEvents(d.events || []);
      if (['done', 'failed', 'cancelled'].indexOf(d.job.status) === -1) startStream();
    }).catch(function (e) { toast(e.message, false); });

    onCleanup(closeStream);

    return container;
  }

  // ---------- Shared form modal ----------
  // fields: [{name, label, type, value, options, placeholder, help}]. onSubmit gets
  // (data, fail, ok), so a rejected save shows the server's message in place.
  function formModal(title, fields, onSubmit, submitLabel) {
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal');
    modal.appendChild(el('h3', null, title));
    var form = el('form', 'form');
    var inputs = {};
    fields.forEach(function (f) {
      var wrap = el('label', 'field');
      wrap.appendChild(el('span', null, f.label));
      var input;
      if (f.type === 'select') {
        input = el('select');
        (f.options || []).forEach(function (opt) {
          var isObj = opt && typeof opt === 'object';
          var option = el('option', null, isObj ? (opt.label || opt.value) : opt);
          option.value = isObj ? opt.value : opt;
          input.appendChild(option);
        });
        input.value = f.value || '';
      } else if (f.type === 'textarea') {
        input = el('textarea');
        input.value = f.value || '';
      } else {
        input = el('input');
        input.type = f.type || 'text';
        input.value = f.value || '';
      }
      if (f.placeholder) input.placeholder = f.placeholder;
      inputs[f.name] = input;
      wrap.appendChild(input);
      if (f.help) wrap.appendChild(el('small', 'muted', f.help));
      form.appendChild(wrap);
    });
    var error = el('div', 'form-error');
    form.appendChild(error);
    var actions = el('div', 'modal-actions');
    var cancel = el('button', 'btn btn-ghost', 'Cancel');
    cancel.type = 'button';
    cancel.onclick = function () { backdrop.remove(); };
    var save = el('button', 'btn btn-primary', submitLabel || 'Save');
    save.type = 'submit';
    actions.appendChild(cancel);
    actions.appendChild(save);
    form.appendChild(actions);
    form.onsubmit = function (ev) {
      ev.preventDefault();
      var data = {};
      Object.keys(inputs).forEach(function (key) { data[key] = inputs[key].value; });
      save.disabled = true;
      error.textContent = '';
      onSubmit(data, function (message) {
        save.disabled = false;
        error.textContent = message || 'That did not work';
      }, function () {
        backdrop.remove();
      });
    };
    modal.appendChild(form);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    return backdrop;
  }

  // ---------- Platform: every connected domain, including deleted clients ----------
  function viewPlatformDomains() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Domains</h2><p class="muted">Every connected domain, ' +
      'who owns it, and whether it is still being served. Domains whose client was ' +
      'deleted stay here so their web-server config can be cleaned up.</p></div>';
    var reload = el('button', 'btn btn-ghost', 'Refresh');
    reload.onclick = function () { load(); };
    head.appendChild(reload);
    container.appendChild(head);

    var banner = el('div', 'panel-card mt');
    container.appendChild(banner);
    var search = el('input');
    search.type = 'search';
    search.placeholder = 'Search domain or client';
    search.className = 'search-input';
    var bar = el('div', 'chip-input');
    bar.appendChild(search);
    container.appendChild(bar);
    var body = el('div', 'mt');
    container.appendChild(body);
    var all = [];

    function remove(site, button) {
      if (!confirm('Remove ' + site.domain + '? ' +
                   'The website will stop being served. DNS records at the registrar are ' +
                   'unchanged, and website files and history are kept.')) return;
      button.disabled = true;
      api('DELETE', '/api/sites/' + site.id).then(function (res) {
        toast(res.dry_run ? (res.message || 'Dry-run: nothing was removed')
                          : (res.message || 'Removed ' + site.domain), true);
        load();
      }).catch(function (e) {
        button.disabled = false;
        toast(e.message, false);
      });
    }

    function render() {
      body.innerHTML = '';
      var needle = (search.value || '').trim().toLowerCase();
      var rows = all.filter(function (site) {
        if (!needle) return true;
        return (site.domain || '').toLowerCase().indexOf(needle) >= 0 ||
          (site.org_name || '').toLowerCase().indexOf(needle) >= 0;
      });
      if (!rows.length) {
        body.appendChild(el('p', 'muted', all.length
          ? 'No domains match that search.'
          : 'No domains are connected yet.'));
        return;
      }
      var table = el('table', 'table');
      table.innerHTML = '<thead><tr><th>Domain</th><th>Client</th><th>Status</th>' +
        '<th>SSL</th><th>Target</th><th>Points at</th><th>Website</th><th></th></tr></thead>';
      var tbody = el('tbody');
      rows.forEach(function (site) {
        var tr = el('tr');
        var client = site.orphaned
          ? '<span class="badge badge-failed">Deleted client</span>' +
            '<div class="muted">org #' + esc(site.org_id == null ? '-' : site.org_id) + '</div>'
          : '<a href="#/platform/clients/' + site.org_id + '">' + esc(site.org_name) +
            '</a>';
        var url = site.url
          ? '<a href="' + esc(site.url) + '" target="_blank" rel="noopener">Open</a>'
          : '<span class="muted">not live</span>';
        tr.innerHTML = '<td><strong>' + esc(site.domain || '(missing)') + '</strong></td>' +
          '<td>' + client + '</td>' +
          '<td>' + siteStatusBadge(site.status) + '</td>' +
          '<td class="fine-print">' + esc(site.ssl_status || 'not issued') + '</td>' +
          '<td><code>' + esc(site.target || 'local') + '</code></td>' +
          '<td><code>' + esc(site.dns_last_ip || 'not checked') + '</code></td>' +
          '<td>' + url + '</td>';
        var actions = el('td', 'row-actions');
        if (!site.orphaned) {
          var openClient = el('a', 'btn btn-ghost btn-sm', 'Open client');
          openClient.href = '#/platform/clients/' + site.org_id;
          actions.appendChild(openClient);
        }
        var records = el('a', 'btn btn-ghost btn-sm', 'Records');
        records.href = '#/app/domains/' + site.id;
        actions.appendChild(records);
        var removeButton = el('button', 'btn btn-danger btn-sm', 'Remove domain');
        removeButton.onclick = function () { remove(site, removeButton); };
        actions.appendChild(removeButton);
        tr.appendChild(actions);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      body.appendChild(table);
    }

    function load() {
      api('GET', '/api/platform/domains').then(function (d) {
        all = d.domains || [];
        var counts = d.counts || {};
        banner.innerHTML = '<h3 class="card-title">' + (counts.total || 0) + ' domain' +
          (counts.total === 1 ? '' : 's') + '</h3>' +
          '<p class="muted">' + (counts.live || 0) + ' live · ' +
          (counts.pending || 0) + ' waiting · ' + (counts.error || 0) + ' with errors · ' +
          '<strong>' + (counts.orphaned || 0) + ' deleted-client</strong></p>' +
          (d.dry_run ? '<p><span class="badge badge-queued">dry run</span> ' +
            'Removing a domain will only pretend to write the web-server config.</p>' : '') +
          '<p class="fine-print">Server address ' + esc(d.server_ip || 'unknown') +
          '. Removing a domain stops the site; it does not change DNS at the registrar.</p>';
        render();
      }).catch(function (e) { toast(e.message, false); });
    }

    search.oninput = render;
    load();
    return container;
  }

  // ---------- Clients (platform console: onboarding) ----------
  var PLANS = ['trial', 'standard', 'pro', 'enterprise'];
  var ORG_STATUSES = ['active', 'suspended', 'cancelled'];

  function deleteClient(org, done) {
    if (!confirm('Delete "' + org.name + '"? Their logins must be removed first. ' +
                 'Connected domains must be disconnected in Platform → Domains ' +
                 'before the client can be deleted.')) return;
    api('DELETE', '/api/orgs/' + org.id)
      .then(function () { toast('Deleted', true); if (done) done(); })
      .catch(function (e) { toast(e.message, false); });
  }

  function viewClients() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.appendChild(el('h1', null, 'Clients'));
    var add = el('button', 'btn btn-primary', '+ New client');
    add.onclick = function () { showClientForm(null, refresh); };
    head.appendChild(add);
    container.appendChild(head);
    container.appendChild(el('p', 'muted',
      'Each client gets their own workspace: their sites, content, chatbots and team. ' +
      'Creating one also creates the first admin login, so a client is never left unable to sign in.'));
    var body = el('div');
    container.appendChild(body);

    function refresh() {
      api('GET', '/api/orgs').then(function (d) {
        body.innerHTML = '';
        var orgs = d.orgs || [];
        if (!orgs.length) {
          body.appendChild(el('p', 'muted', 'No clients yet. Create the first one.'));
          return;
        }
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Client</th><th>Plan</th><th>Status</th>' +
          '<th>Created</th><th></th></tr></thead>';
        var tbody = el('tbody');
        orgs.forEach(function (org) {
          var tr = el('tr');
          tr.innerHTML = '<td><a href="#/platform/clients/' + org.id + '">' + esc(org.name) +
            '</a><div class="muted">' + esc(org.slug) + '</div></td>' +
            '<td><span class="badge">' + esc(org.plan) + '</span></td>' +
            '<td><span class="badge badge-' + (org.status === 'active' ? 'admin' : 'member') + '">' +
            esc(org.status) + '</span></td>' +
            '<td class="muted">' + esc((org.created_at || '').slice(0, 10)) + '</td>';
          var td = el('td', 'row-actions');
          var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
          edit.onclick = function () { showClientForm(org, refresh); };
          td.appendChild(edit);
          var del = el('button', 'btn btn-danger btn-sm', 'Delete');
          del.onclick = function () { deleteClient(org, refresh); };
          td.appendChild(del);
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
      }).catch(function (e) { toast(e.message, false); });
    }
    refresh();
    return container;
  }

  function showClientForm(org, done) {
    var isNew = !org;
    var fields = [
      { name: 'name', label: 'Client name', value: org ? org.name : '', placeholder: 'Acme Ltd' }
    ];
    if (isNew) {
      fields.push({ name: 'slug', label: 'Workspace ID', value: '',
        help: 'A short identifier used by support. Generated from the name when blank.' });
    }
    fields.push(
      { name: 'plan', label: 'Plan', type: 'select', options: PLANS, value: org ? org.plan : 'standard' },
      { name: 'status', label: 'Status', type: 'select', options: ORG_STATUSES, value: org ? org.status : 'active' },
      { name: 'notes', label: 'Notes', type: 'textarea', value: org ? org.notes : '' }
    );
    if (isNew) {
      fields.push({ name: 'email', label: 'First admin email', type: 'email', placeholder: 'owner@acme.com' });
      fields.push({ name: 'password', label: 'First admin password', type: 'password',
        help: 'At least 8 characters. They can change it once signed in.' });
      fields.push({ name: 'contact_name', label: 'Their name', placeholder: 'Jane Doe' });
    }
    formModal(isNew ? 'New client' : 'Edit ' + org.name, fields, function (data, fail, ok) {
      var request = isNew
        ? api('POST', '/api/orgs', data)
        : api('PUT', '/api/orgs/' + org.id, data);
      request.then(function () {
        ok();
        toast(isNew ? 'Client created' : 'Client saved', true);
        done();
      }).catch(function (e) { fail(e.message); });
    });
  }

  var CLIENT_TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'team', label: 'Team' },
    { id: 'websites', label: 'Websites' },
    { id: 'usage', label: 'Usage' },
    { id: 'support', label: 'Support' },
    { id: 'settings', label: 'Settings' }
  ];

  function clientStatusBadge(status) {
    var cls = status === 'active' ? 'badge-success'
      : (status === 'suspended' ? 'badge-warn' : 'badge-cancelled');
    var label = String(status || 'unknown').replace(/^[a-z]/, function (c) {
      return c.toUpperCase();
    });
    return '<span class="badge ' + cls + '">' + esc(label) + '</span>';
  }

  function clientEmptyState(iconName, title, text) {
    var box = el('div', 'panel-card empty-state');
    box.innerHTML = '<div class="big">' + icon(iconName || 'alert') + '</div>' +
      '<strong>' + esc(title) + '</strong><p>' + esc(text) + '</p>';
    return box;
  }

  function clientStat(value, label) {
    var card = el('div', 'client-stat');
    card.innerHTML = '<strong>' + esc(value) + '</strong><span>' + esc(label) + '</span>';
    return card;
  }

  function openClientWorkspace(org) {
    api('POST', '/api/session/org', { org_id: Number(org.id) }).then(function () {
      if (state.user) state.user.acting_org = Number(org.id);
      creditCache.at = 0;
      discardStudio();
      toast('Now working in ' + org.name + "'s workspace", true);
      location.hash = '#/app/dashboard';
    }).catch(function (e) { toast(e.message, false); });
  }


  function viewClient(orgId, tab) {
    var container = el('div', 'container');
    var head = el('div', 'page-head client-page-head');
    var tabs = el('nav', 'client-tabs');
    tabs.setAttribute('aria-label', 'Client sections');
    var body = el('div', 'client-tab-body');
    var active = (CLIENT_TABS.filter(function (item) {
      return item.id === tab;
    })[0] || CLIENT_TABS[0]).id;
    container.appendChild(head);
    container.appendChild(tabs);
    container.appendChild(body);

    function paintHead(org) {
      head.innerHTML = '<div>' +
        '<div class="client-back"><a href="#/platform/clients">← All clients</a></div>' +
        '<div class="client-title-row"><h1>' + esc(org.name) + '</h1>' +
        clientStatusBadge(org.status) +
        '<span class="badge">' + esc(org.plan) + '</span></div>' +
        '<p>Workspace ID <code>' + esc(org.slug) + '</code>' +
        (org.created_at ? ' · added ' + esc(String(org.created_at).slice(0, 10)) : '') +
        '</p></div>';
      var actions = el('div', 'page-actions');
      var open = el('button', 'btn btn-primary', 'Open workspace');
      open.onclick = function () { openClientWorkspace(org); };
      var edit = el('button', 'btn btn-ghost', 'Edit client');
      edit.onclick = function () { showClientForm(org, load); };
      actions.appendChild(open);
      actions.appendChild(edit);
      head.appendChild(actions);
    }

    function paintTabs() {
      tabs.innerHTML = CLIENT_TABS.map(function (item) {
        var current = item.id === active;
        return '<a class="client-tab' + (current ? ' active' : '') + '" href="' +
          '#/platform/clients/' + encodeURIComponent(orgId) + '/' + item.id + '"' +
          (current ? ' aria-current="page"' : '') + '>' + esc(item.label) + '</a>';
      }).join('');
    }

    function paintActive(org, users) {
      body.innerHTML = '';
      var painters = {
        overview: paintClientOverview,
        team: paintClientTeam,
        websites: paintClientWebsites,
        usage: paintClientUsage,
        support: paintClientSupport,
        settings: paintClientSettings
      };
      painters[active](body, org, users, load);
    }

    function load() {
      api('GET', '/api/orgs/' + orgId).then(function (d) {
        var org = d.org;
        paintHead(org);
        paintTabs();
        paintActive(org, d.users || []);
      }).catch(function (e) {
        head.innerHTML = '<div><div class="client-back">' +
          '<a href="#/platform/clients">← All clients</a></div>' +
          '<h1>Client not found</h1><p>' + esc(e.message) + '</p></div>';
        tabs.innerHTML = '';
        body.innerHTML = '';
        body.appendChild(clientEmptyState('store', 'That client could not be opened.',
          'Return to the client list and try again.'));
      });
    }

    head.innerHTML = '<div><div class="client-back">' +
      '<a href="#/platform/clients">← All clients</a></div>' +
      '<h1>Client</h1><p>Loading client details…</p></div>';
    paintTabs();
    body.appendChild(el('p', 'muted', 'Loading client details…'));
    load();
    return container;
  }

  function paintClientOverview(host, org, users, reload) {
    host.appendChild(el('p', 'muted', 'Loading overview…'));
    function quiet(promise) {
      return promise.catch(function () { return null; });
    }
    Promise.all([
      quiet(api('GET', '/api/orgs/' + org.id + '/usage')),
      quiet(api('GET', '/api/platform/domains?org_id=' + encodeURIComponent(org.id))),
      quiet(api('GET', '/api/platform/bugs?org_id=' + encodeURIComponent(org.id)))
    ]).then(function (rows) {
      var usage = rows[0] || {};
      var domainData = rows[1] || {};
      var bugData = rows[2] || {};
      var bugs = bugData.bugs || [];
      var assignees = bugData.assignees || [];
      var domains = domainData.domains || [];
      var openIssues = bugs.filter(function (bug) {
        return bug.status === 'open' || bug.status === 'in_progress';
      }).length;
      var admin = users.filter(function (user) {
        return user.role === 'client_admin';
      })[0];

      host.innerHTML = '';
      var stats = el('div', 'client-stats');
      stats.appendChild(clientStat(users.length, 'Team logins'));
      stats.appendChild(clientStat(rows[1] ? domains.length : '—', 'Websites'));
      stats.appendChild(clientStat(rows[2] ? openIssues : '—', 'Open support issues'));
      stats.appendChild(clientStat(
        usage.balance === null || usage.balance === undefined
          ? '—' : fmtCredits(usage.balance) + ' credits', 'Credits available'));
      host.appendChild(stats);

      var grid = el('div', 'client-overview-grid');
      var details = el('div', 'panel-card');
      details.innerHTML = '<h3>Client details</h3>' +
        '<div class="client-facts">' +
        '<div><span>Plan</span><strong>' + esc(org.plan) + '</strong></div>' +
        '<div><span>Status</span><strong>' + esc(org.status) + '</strong></div>' +
        '<div><span>Primary admin</span><strong>' +
        (admin ? esc(admin.name) + '<small>' + esc(admin.email) + '</small>'
          : 'No login yet') + '</strong></div>' +
        '<div><span>Notes</span><strong>' + esc(org.notes || 'No notes yet.') +
        '</strong></div></div>';
      var edit = el('button', 'btn btn-ghost btn-sm', 'Edit client details');
      edit.onclick = function () { showClientForm(org, reload); };
      details.appendChild(edit);
      grid.appendChild(details);

      var support = el('div', 'panel-card');
      var supportHead = el('div', 'panel-head');
      supportHead.appendChild(el('h3', null, 'Recent support'));
      var supportLink = el('a', 'btn btn-ghost btn-sm', 'Open Support');
      supportLink.href = '#/platform/clients/' + org.id + '/support';
      supportHead.appendChild(supportLink);
      support.appendChild(supportHead);
      if (!bugs.length) {
        support.appendChild(el('p', 'muted', 'No support issues for this client yet.'));
      } else {
        var mini = el('div', 'client-mini-list');
        bugs.slice(0, 4).forEach(function (bug) {
          var row = el('button', 'client-mini-row');
          row.type = 'button';
          row.innerHTML = '<span><strong>#' + esc(String(bug.id)) + ' ' +
            esc(bug.title) + '</strong><small>' + esc(shortTime(bug.updated_at)) +
            '</small></span>' + bugStatusBadge(bug.status);
          row.onclick = function () {
            openBugDetail(bug.id, {
              orgs: [org], assignees: assignees, defaultOrgId: org.id,
              lockClient: true, onSaved: reload
            });
          };
          mini.appendChild(row);
        });
        support.appendChild(mini);
      }
      grid.appendChild(support);
      host.appendChild(grid);

      var quick = el('div', 'panel-card client-quick-actions');
      quick.appendChild(el('h3', null, 'Quick actions'));
      var actions = el('div', 'card-actions');
      var addLogin = el('button', 'btn btn-ghost btn-sm', 'Add a login');
      addLogin.onclick = function () { showOrgUserForm(org, reload); };
      var report = el('button', 'btn btn-ghost btn-sm', 'Report a support issue');
      report.onclick = function () {
        openBugModal(null, [], {
          orgs: [org], assignees: assignees, defaultOrgId: org.id,
          lockClient: true, onSaved: reload, openAfterSave: true
        });
      };
      var credits = el('button', 'btn btn-ghost btn-sm', 'Add credits');
      credits.onclick = function () { grantCreditsModal(org, reload); };
      actions.appendChild(addLogin);
      actions.appendChild(report);
      actions.appendChild(credits);
      quick.appendChild(actions);
      host.appendChild(quick);
    });
  }


  function paintClientTeam(host, org, users, reload) {
    var card = el('div', 'panel-card');
    var head = el('div', 'panel-head');
    head.appendChild(el('h3', null, 'Team'));
    var actions = el('div', 'page-actions');
    var add = el('button', 'btn btn-primary btn-sm', '+ Add login');
    add.onclick = function () { showOrgUserForm(org, reload); };
    var invite = el('button', 'btn btn-ghost btn-sm', 'Send invitation');
    invite.onclick = function () { invitePersonModal(true, [org], org.id, reload); };
    actions.appendChild(add);
    actions.appendChild(invite);
    head.appendChild(actions);
    card.appendChild(head);
    var body = el('div');
    card.appendChild(body);
    host.appendChild(card);
    body.appendChild(el('p', 'muted', 'Loading team…'));

    api('GET', '/api/platform/users?org_id=' + encodeURIComponent(org.id)).then(function (d) {
      body.innerHTML = '';
      var people = d.users || [];
      if (!people.length) {
        body.appendChild(clientEmptyState('user', 'No one can sign in yet.',
          'Add the first login so this client can use their workspace.'));
        return;
      }
      body.appendChild(peopleTable(people, true, [org], reload, org.id));
    }).catch(function (e) {
      body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
    });
  }


  function paintClientWebsites(host, org, users, reload) {
    var card = el('div', 'panel-card');
    var head = el('div', 'panel-head');
    head.appendChild(el('h3', null, 'Websites'));
    var manage = el('a', 'btn btn-ghost btn-sm', 'Manage all domains');
    manage.href = '#/platform/domains';
    head.appendChild(manage);
    card.appendChild(head);
    card.appendChild(el('p', 'muted',
      'Connected websites and their DNS status. Domain removal stays in Platform → Domains.'));
    var body = el('div');
    card.appendChild(body);
    host.appendChild(card);
    body.appendChild(el('p', 'muted', 'Loading websites…'));

    api('GET', '/api/platform/domains?org_id=' + encodeURIComponent(org.id))
      .then(function (d) {
        body.innerHTML = '';
        var rows = d.domains || [];
        if (!rows.length) {
          body.appendChild(clientEmptyState('globe', 'No website is connected yet.',
            'The client can connect one from their Website tab.'));
          return;
        }
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Website</th><th>Status</th><th>SSL</th>' +
          '<th>Points at</th><th>Target</th><th></th></tr></thead>';
        var tbody = el('tbody');
        rows.forEach(function (site) {
          var tr = el('tr');
          tr.innerHTML = '<td><strong>' + esc(site.domain || '(missing)') +
            '</strong><div class="muted">' + esc(site.org_name || org.name) + '</div></td>' +
            '<td>' + siteStatusBadge(site.status) + '</td>' +
            '<td class="fine-print">' + esc(site.ssl_status || 'not issued') + '</td>' +
            '<td><code>' + esc(site.dns_last_ip || 'not checked') + '</code></td>' +
            '<td><code>' + esc(site.target || 'local') + '</code></td>';
          var td = el('td', 'row-actions');
          if (site.url) {
            var open = el('a', 'btn btn-ghost btn-sm', 'Open');
            open.href = site.url;
            open.target = '_blank';
            open.rel = 'noopener';
            td.appendChild(open);
          }
          var records = el('a', 'btn btn-ghost btn-sm', 'Records');
          records.href = '#/app/domains/' + site.id;
          td.appendChild(records);
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
      }).catch(function (e) {
        body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
      });
  }


  function paintClientUsage(host, org, users, reload) {
    var card = el('div', 'panel-card');
    var head = el('div', 'panel-head');
    head.appendChild(el('h3', null, 'Credits and usage'));
    var actions = el('div', 'page-actions');
    var add = el('button', 'btn btn-primary btn-sm', 'Add credits');
    add.onclick = function () { grantCreditsModal(org, reload); };
    var all = el('a', 'btn btn-ghost btn-sm', 'All client usage');
    all.href = '#/platform/usage';
    actions.appendChild(add);
    actions.appendChild(all);
    head.appendChild(actions);
    card.appendChild(head);
    var body = el('div');
    card.appendChild(body);
    host.appendChild(card);
    body.appendChild(el('p', 'muted', 'Loading usage…'));

    api('GET', '/api/orgs/' + org.id + '/usage').then(function (d) {
      body.innerHTML = '';
      var stats = el('div', 'client-stats');
      stats.appendChild(clientStat(
        d.balance === null || d.balance === undefined ? '—' : fmtCredits(d.balance),
        'Credits available'));
      stats.appendChild(clientStat(
        d.allowance ? fmtCredits(d.allowance) : '—', 'Monthly allowance'));
      stats.appendChild(clientStat(fmtCredits(d.spent || 0), 'Used this month'));
      stats.appendChild(clientStat(fmtCredits(d.added || 0), 'Added this month'));
      body.appendChild(stats);
      if (d.low) {
        body.appendChild(el('p', 'form-error',
          'This client is below the low-balance threshold.'));
      }

      var recent = el('div', 'panel-card');
      recent.appendChild(el('h3', null, 'Recent activity'));
      var items = d.recent || [];
      if (!items.length) {
        recent.appendChild(el('p', 'muted', 'No usage recorded for this client yet.'));
      } else {
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>When</th><th>Activity</th>' +
          '<th>Team</th><th>Credits</th></tr></thead>';
        var tbody = el('tbody');
        items.slice(0, 12).forEach(function (item) {
          var tr = el('tr');
          tr.innerHTML = '<td class="muted">' + esc((item.created_at || '').slice(0, 16)) +
            '</td><td>' + esc(usageKind(item.kind)) + '</td>' +
            '<td>' + esc(item.label || '—') + '</td>' +
            '<td>' + (item.credits_milli === null
              ? '<span class="badge badge-queued">unpriced</span>'
              : esc(fmtCredits((item.credits_milli || 0) / 1000))) + '</td>';
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        recent.appendChild(table);
      }
      host.appendChild(recent);
    }).catch(function (e) {
      body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
    });
  }


  function paintClientSupport(host, org, users, reload) {
    var card = el('div', 'panel-card');
    var head = el('div', 'panel-head');
    head.appendChild(el('h3', null, 'Support issues'));
    var actions = el('div', 'page-actions');
    var report = el('button', 'btn btn-primary btn-sm', '+ Report issue');
    report.onclick = function () {
      openBugModal(null, [], {
        orgs: [org], assignees: supportAssignees, defaultOrgId: org.id,
        lockClient: true, onSaved: reload, openAfterSave: true
      });
    };
    var tracker = el('a', 'btn btn-ghost btn-sm', 'Open bug tracker');
    tracker.href = '#/platform/bugs';
    actions.appendChild(report);
    actions.appendChild(tracker);
    head.appendChild(actions);
    card.appendChild(head);
    card.appendChild(el('p', 'muted',
      'Internal support issues for this client. The client never sees this tab.'));
    var summary = el('div', 'bug-summary');
    var body = el('div');
    card.appendChild(summary);
    card.appendChild(body);
    host.appendChild(card);
    body.appendChild(el('p', 'muted', 'Loading support issues…'));

    var supportAssignees = [];
    api('GET', '/api/platform/bugs?org_id=' + encodeURIComponent(org.id))
      .then(function (d) {
        var bugs = d.bugs || [];
        supportAssignees = d.assignees || [];
        summary.innerHTML = '';
        var counts = { open: 0, in_progress: 0, resolved: 0, all: bugs.length };
        bugs.forEach(function (bug) {
          if (counts[bug.status] !== undefined) counts[bug.status] += 1;
        });
        [['Open', 'open'], ['In progress', 'in_progress'],
         ['Resolved', 'resolved'], ['Total', 'all']].forEach(function (pair) {
          var chip = el('div', 'bug-count');
          chip.innerHTML = '<strong>' + esc(String(counts[pair[1]] || 0)) + '</strong>' +
            '<span>' + pair[0] + '</span>';
          summary.appendChild(chip);
        });
        body.innerHTML = '';
        if (!bugs.length) {
          body.appendChild(clientEmptyState('alert', 'No support issues for this client.',
            'Report the first issue if something needs attention.'));
          return;
        }
        var table = el('table', 'table bug-table');
        table.innerHTML = '<thead><tr><th>Issue</th><th>Priority</th><th>Status</th>' +
          '<th>Assignee</th><th>Updated</th></tr></thead>';
        var tbody = el('tbody');
        bugs.forEach(function (bug) {
          var tr = el('tr');
          tr.className = 'bug-row';
          tr.title = 'Open issue #' + bug.id;
          tr.innerHTML = '<td><strong>#' + esc(String(bug.id)) + ' ' + esc(bug.title) +
            '</strong><div class="muted">' + esc(bug.reporter_name || 'Agency') +
            (bug.page ? ' · ' + esc(bug.page) : '') + '</div></td>' +
            '<td>' + bugPriorityBadge(bug.priority) + '</td>' +
            '<td>' + bugStatusBadge(bug.status) + '</td>' +
            '<td class="muted">' + esc(bug.assignee_name || 'Unassigned') + '</td>' +
            '<td class="muted">' + esc(shortTime(bug.updated_at)) + '</td>';
          tr.onclick = function () {
            openBugDetail(bug.id, {
              orgs: [org], assignees: supportAssignees,
              defaultOrgId: org.id, lockClient: true, onSaved: reload
            });
          };
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
      }).catch(function (e) {
        body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
      });
  }


  function paintClientSettings(host, org, users, reload) {
    var profile = el('div', 'panel-card');
    var profileHead = el('div', 'panel-head');
    profileHead.appendChild(el('h3', null, 'Client details'));
    var edit = el('button', 'btn btn-ghost btn-sm', 'Edit client');
    edit.onclick = function () { showClientForm(org, reload); };
    profileHead.appendChild(edit);
    profile.appendChild(profileHead);
    var facts = el('div', 'client-facts');
    facts.innerHTML = '<div><span>Workspace ID</span><strong><code>' + esc(org.slug) +
      '</code></strong></div>' +
      '<div><span>Plan</span><strong>' + esc(org.plan) + '</strong></div>' +
      '<div><span>Status</span><strong>' + esc(org.status) + '</strong></div>' +
      '<div><span>Notes</span><strong>' + esc(org.notes || 'No notes yet.') +
      '</strong></div>';
    profile.appendChild(facts);
    host.appendChild(profile);

    var email = el('div', 'panel-card');
    email.appendChild(el('h3', null, 'Email sending'));
    email.appendChild(el('p', 'muted',
      'Optional. Leave these blank to use the agency default. Passwords are stored ' +
      'securely and are never shown again.'));
    var emailBody = el('div');
    email.appendChild(emailBody);
    host.appendChild(email);
    clientSettingsCards(emailBody, org.id, reload,
      'Using the agency default. Add this client’s mailbox only if they should send ' +
      'from their own address.');

    var danger = el('div', 'panel-card client-danger');
    danger.appendChild(el('h3', null, 'Danger zone'));
    danger.appendChild(el('p', 'muted',
      'Deleting a client is permanent. Their logins must be removed first, and every ' +
      'connected domain must be disconnected in Platform → Domains.'));
    if (users.length) {
      danger.appendChild(el('p', 'fine-print',
        users.length + ' login(s) still attached. Remove them in the Team tab first.'));
    }
    var remove = el('button', 'btn btn-danger', 'Delete client permanently');
    remove.onclick = function () {
      deleteClient(org, function () { location.hash = '#/platform/clients'; });
    };
    danger.appendChild(remove);
    host.appendChild(danger);
  }


  function showOrgUserForm(org, done) {
    formModal('Add a login to ' + org.name, [
      { name: 'name', label: 'Name' },
      { name: 'email', label: 'Email', type: 'email' },
      { name: 'password', label: 'Password', type: 'password', help: 'At least 8 characters.' },
      { name: 'role', label: 'Role', type: 'select', options: ['client_admin', 'member'], value: 'member' }
    ], function (data, fail, ok) {
      api('POST', '/api/orgs/' + org.id + '/users', data)
        .then(function () { ok(); toast('Login added', true); done(); })
        .catch(function (e) { fail(e.message); });
    });
  }

  function showPasswordForm(user, done) {
    formModal('Set a password for ' + user.name, [
      { name: 'new_password', label: 'New password', type: 'password', help: 'At least 8 characters.' }
    ], function (data, fail, ok) {
      api('POST', '/api/users/' + user.id + '/password', data)
        .then(function () { ok(); toast('Password set', true); if (done) done(); })
        .catch(function (e) { fail(e.message); });
    });
  }

  // ---------- Content: Websites, Blogs, Funnels, Emails, Library ----------
  // Websites is the existing sites view. The other four are one table filtered by
  // kind; the Library is the same list unfiltered, because "everything the team
  // produced" is exactly the union of them.
  var CONTENT_TABS = {
    blogs: { kind: 'blog', title: 'Blog posts', blurb: 'Articles written for you and published onto a website.' },
    funnels: { kind: 'funnel', title: 'Landing pages', blurb: 'Opt-in pages and the steps that follow them.' },
    emails: { kind: 'email', title: 'Emails', blurb: 'Campaigns and sequences sent to your list.' },
    library: { kind: null, title: 'All content', blurb: 'Everything the team has produced for you, in one place.' }
  };

  // ---------- AI inside the builder tabs ----------
  // The output has to land in the tab it belongs to, so each content view carries its
  // own prompt box - otherwise people run a job, read the transcript, and copy the
  // result back by hand, which is what "it generates it but doesn't add it in" meant.
  var KIND_BRIEF = {
    email: 'Write an email. When it is finished, save it with save_deliverable using kind "email".',
    blog: 'Write a blog post. When it is finished, save it with save_deliverable using kind "blog".',
    funnel: 'Write a funnel page. When it is finished, save it with save_deliverable using kind "funnel".',
    website: 'Build a website page as one complete HTML document. When it is finished, save it with save_deliverable using kind "website".'
  };

  var QUOTES = [
    'Herding the ducks…',
    'Negotiating with the cows…',
    'Teaching the pixels to behave…',
    'Untangling semicolons…',
    'Feeding the hamsters that power this…',
    'Asking the reviewer to be nice…',
    'Counting to three, twice…',
    'Polishing the buttons…'
  ];

  // The artifact's own loading state: a skeleton shaped like the document, with the
  // activity strip underneath. Mounted over the region the result will occupy, so the
  // page stays usable and the overlay never traps the cursor.
  function documentCurtain(host, jobId, onDone) {
    if (!host) return null;
    host.classList.add('curtain-host');
    var curtain = el('div', 'doc-curtain');
    curtain.innerHTML =
      '<div class="doc-skel" aria-hidden="true">' +
      '<i class="h"></i><i class="t"></i><i class="t t2"></i><i class="t t3"></i>' +
      '<i class="img"></i><i class="t"></i><i class="t t2"></i><i class="f"></i></div>';
    var strip = el('span', 'activity running');
    curtain.appendChild(strip);
    host.appendChild(curtain);

    watchJob(jobId, strip, function () {
      curtain.classList.add('leaving');
      setTimeout(function () {
        if (curtain.parentNode) curtain.parentNode.removeChild(curtain);
        if (onDone) onDone();
      }, 220);
    });
    return curtain;
  }

  // A failed job already carries the reason in its transcript. Saying "No answer" while
  // the actual error sits one click away is how a missing provider looked like silence.
  function jobFailure(events) {
    var failed = (events || []).filter(function (ev) { return ev.type === 'error'; }).pop();
    if (!failed) return '';
    var lines = String(failed.content || '').split('\n').filter(function (line) {
      return line.trim();
    });
    return lines.length ? lines[lines.length - 1].trim() : '';
  }

  function watchJob(jobId, statusEl, onFinish) {
    var lastSeq = 0;
    var lastText = '';
    var ticks = 0;
    var timer = setInterval(function () {
      api('GET', '/api/jobs/' + jobId + '/events?after=' + lastSeq).then(function (d) {
        (d.events || []).forEach(function (ev) {
          lastSeq = Math.max(lastSeq, ev.seq);
          if (ev.content) lastText = String(ev.content).replace(/\s+/g, ' ').slice(0, 100);
        });
        ticks += 1;
        var job = d.job || {};
        var quip = QUOTES[Math.floor(ticks / 2) % QUOTES.length];
        var running = ['done', 'failed', 'cancelled'].indexOf(job.status) === -1;
        var stuck = job.status === 'queued' && ticks > 45;
        statusEl.className = running ? 'activity running' : 'activity';
        statusEl.innerHTML =
          '<i class="activity-dot"></i>' +
          '<span class="activity-text">' +
            esc(stuck
              ? 'Still queued after 90s - is the worker running? (job #' + jobId + ')'
              : (job.status || 'running') + ' · job #' + jobId +
                (lastText ? ' · ' + lastText : ' · ' + quip)) +
          '</span>' +
          (running ? '<span class="activity-bar"><b></b></span>' : '');
        if (['done', 'failed', 'cancelled'].indexOf(job.status) !== -1) {
          clearInterval(timer);          // stop before onFinish, so a re-render cannot
          if (job.status === 'done') {   // leave this timer polling a finished job
            toast('Finished - the new draft is listed below', true);
          } else {
            toast((job.error || ('Job ' + job.status)).slice(0, 200), false);
          }
          onFinish();
        }
      }).catch(function () { /* keep polling through a blip */ });
    }, 2000);
    onCleanup(function () { clearInterval(timer); });
  }

  function aiComposer(kind, blurb, onFinish, host) {
    var card = el('div', 'form-card ai-composer');
    card.appendChild(el('h3', null, 'Ask the team'));
    card.appendChild(el('p', 'muted', blurb));
    var prompt = el('textarea', 'prompt-box');
    prompt.placeholder = kind === 'website'
      ? 'e.g. a one-page site for the spring offer: headline, three benefits, a sign-up form'
      : 'e.g. a launch email for our new pricing, warm tone, one clear call to action';
    card.appendChild(prompt);
    var actions = el('div', 'row-actions');
    var btn = el('button', 'btn btn-primary', 'Generate');
    var status = el('span', 'muted', '');
    btn.onclick = function () {
      var text = prompt.value.trim();
      if (!text) { toast('Describe what you want first', false); return; }
      btn.disabled = true;
      status.textContent = 'Starting…';
      var brief = KIND_BRIEF[kind];
      api('POST', '/api/jobs', {
        prompt: (brief ? brief + '\n\n' : '') + text,
        task: 'agent_team',
        team_mode: 'selector'
      }).then(function (d) {
        status.textContent = 'Running as job #' + d.job.id + '…';
        var finish = function () {
          btn.disabled = false;
          prompt.value = '';
          onFinish();
        };
        var target = typeof host === 'function' ? host() : host;
        if (target) documentCurtain(target, d.job.id, finish);
        else watchJob(d.job.id, status, finish);
      }).catch(function (e) {
        btn.disabled = false;
        status.textContent = '';
        toast(e.message, false);
      });
    };
    actions.appendChild(btn);
    actions.appendChild(status);
    card.appendChild(actions);
    return card;
  }

  function copyText(text, button) {
    if (!(navigator.clipboard && navigator.clipboard.writeText)) {
      toast('Clipboard is not available here', false);
      return;
    }
    navigator.clipboard.writeText(text || '').then(function () {
      toast('HTML copied', true);
      if (button) button.textContent = 'Copied';
    }).catch(function () { toast('Could not copy', false); });
  }

  function previewDeliverable(item) {
    if (item && item.id && item.content === undefined) {
      // A summary list omits the document; load the one draft being previewed.
      api('GET', '/api/deliverables/' + item.id).then(function (d) {
        previewDeliverable(d.deliverable || item);
      }).catch(function (e) { toast(e.message, false); });
      return;
    }
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide');
    modal.appendChild(el('h3', null, item.title || 'Preview'));
    if (item.status === 'draft' && item.url) {
      var banner = el('p', 'fine-print');
      banner.textContent = 'Unpublished changes. The live page at ' + item.url +
        ' still has the previous version until you Publish.';
      modal.appendChild(banner);
    }
    // sandbox="" - no scripts, no forms, no same-origin access. This markup came from
    // a model, so the console renders it without trusting or executing any of it.
    var frame = el('iframe', 'preview-frame');
    frame.setAttribute('sandbox', '');
    frame.srcdoc = item.content ||
      '<p style="font-family:system-ui;padding:24px;color:#667">Nothing to preview yet.</p>';
    modal.appendChild(frame);
    var actions = el('div', 'row-actions');
    var copy = el('button', 'btn btn-ghost', 'Copy HTML');
    copy.onclick = function () { copyText(item.content, copy); };
    var history = el('button', 'btn btn-ghost', 'History');
    history.onclick = function () { showDeliverableHistory(item, frame); };
    var studio = el('button', 'btn btn-ghost', 'Open in Studio');
    studio.onclick = function () {
      document.body.removeChild(backdrop);
      openInStudio('deliverable', item.id);
    };
    var close = el('button', 'btn btn-primary', 'Close');
    close.onclick = function () { document.body.removeChild(backdrop); };
    actions.appendChild(copy);
    actions.appendChild(history);
    actions.appendChild(studio);
    actions.appendChild(close);
    modal.appendChild(actions);

    // Refine in place: the job updates this same draft, so the preview reloads rather
    // than spawning a second version to compare by hand.
    var refineBox = el('div', 'refine-box');
    var say = el('input');
    say.placeholder = 'Ask for a change - "make the subject shorter", "warmer tone"…';
    var go = el('button', 'btn btn-primary', 'Refine');
    var note = el('div', 'muted', '');
    go.onclick = function () {
      var text = (say.value || '').trim();
      if (!text) { toast('Say what to change', false); return; }
      go.disabled = true;
      note.textContent = 'Sending to the team…';
      api('POST', '/api/deliverables/' + item.id + '/refine', { instruction: text })
        .then(function (d) {
          watchJob(d.job_id, note, function () {
            api('GET', '/api/deliverables/' + item.id).then(function (fresh) {
              var updated = fresh.deliverable || {};
              frame.srcdoc = updated.content || frame.srcdoc;
              item.content = updated.content || item.content;
              say.value = '';
              go.disabled = false;
              toast('Draft updated', true);
            }).catch(function () { go.disabled = false; });
          });
        })
        .catch(function (e) { go.disabled = false; note.textContent = e.message; });
    };
    refineBox.appendChild(say);
    refineBox.appendChild(go);
    modal.appendChild(refineBox);
    modal.appendChild(note);

    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) document.body.removeChild(backdrop); };
    document.body.appendChild(backdrop);
  }

  // The undo a review job cannot bypass. Every content change is snapshotted server-side;
  // this is the human way to put one back without touching a database.
  function showDeliverableHistory(item, frame) {
    api('GET', '/api/deliverables/' + item.id + '/versions').then(function (d) {
      var versions = d.versions || [];
      var backdrop = el('div', 'modal-backdrop');
      var modal = el('div', 'modal modal-wide');
      modal.appendChild(el('h3', null, 'History - ' + (item.title || 'page')));
      if (!versions.length) {
        modal.appendChild(el('p', 'muted',
          'No earlier versions yet. Every change from now on is kept here.'));
      } else {
        modal.appendChild(el('p', 'fine-print',
          'Restoring keeps the current content in this list too, so the undo is itself undoable.'));
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Saved</th><th>Status</th><th>Job</th><th>Size</th><th></th></tr></thead>';
        var tbody = el('tbody');
        versions.forEach(function (version) {
          var tr = el('tr');
          tr.innerHTML = '<td>' + esc((version.created_at || '').slice(0, 16)) + '</td>' +
            '<td>' + esc(version.status || '') + '</td>' +
            '<td class="muted">' + (version.job_id ? '#' + esc(version.job_id) : '') + '</td>' +
            '<td class="muted">' + esc(version.bytes || 0) + ' chars</td>';
          var td = el('td', 'row-actions');
          var restore = el('button', 'btn btn-ghost btn-sm', 'Restore');
          restore.onclick = function () {
            if (!confirm('Restore the version from ' + (version.created_at || 'that save') +
                         '? The current content is kept in History first.')) return;
            restore.disabled = true;
            api('POST', '/api/deliverables/' + item.id + '/versions/' + version.id + '/restore', {})
              .then(function (res) {
                var restored = res.deliverable || {};
                item.content = restored.content || item.content;
                item.status = restored.status || 'draft';
                if (frame) frame.srcdoc = item.content;
                backdrop.remove();
                toast('Restored. The page is a draft until you publish it.', true);
              })
              .catch(function (e) { restore.disabled = false; toast(e.message, false); });
          };
          td.appendChild(restore);
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        modal.appendChild(table);
      }
      var actions = el('div', 'row-actions');
      var close = el('button', 'btn btn-primary', 'Close');
      close.onclick = function () { backdrop.remove(); };
      actions.appendChild(close);
      modal.appendChild(actions);
      backdrop.appendChild(modal);
      backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
      document.body.appendChild(backdrop);
    }).catch(function (e) { toast(e.message, false); });
  }

  function sendPublish(item, siteId, onDone, button, asHome, path) {
    if (button) button.disabled = true;
    api('POST', '/api/deliverables/' + item.id + '/publish', {
      site_id: siteId,
      as_home: !!asHome,
      // The explicit subpage target is independent of the draft's slug. A home-slug
      // draft can be published to /about.html without replacing the live front page.
      path: asHome ? '' : (path || ''),
      // The publish dialog names the exact target and is the confirmation. The server
      // still refuses a replacement that no dialog confirmed.
      replace: true
    })
      .then(function (res) {
        if (res.dry_run) {
          toast('Dry-run: would write ' + res.path + ' (set ALLOW_LIVE_ACTIONS=1)', true);
        } else {
          toast('Published to ' + res.url + (res.warning ? ' - ' + res.warning : ''),
                !res.warning);
        }
        if (onDone) onDone(res);
      })
      .catch(function (e) { toast(e.message, false); })
      .finally(function () { if (button) button.disabled = false; });
  }

  function publishDeliverable(item, onDone, button) {
    api('GET', '/api/sites?summary=1').then(function (d) {
      var sites = d.sites || [];
      if (!sites.length) {
        if (button) button.disabled = false;
        toast('Add a domain in the Website tab first - a page needs somewhere to live', false);
        return;
      }
      var slug = (item.slug || 'page').trim();
      var slugIsHome = !slug || slug === 'home' || slug === 'index';
      var defaultPath = slugIsHome ? 'about.html' : (slug + '.html');
      var first = sites[0];
      var starter = first.starter !== undefined
        ? !!first.starter
        : /This site is ready/i.test(first.content_html || '');
      var defaultWhere = (slugIsHome || starter) ? 'home' : 'subpage';
      var fields = [];
      if (sites.length > 1) {
        fields.push({
          name: 'site', label: 'Site', type: 'select',
          options: sites.map(function (s) { return { label: s.domain, value: String(s.id) }; })
        });
      }
      fields.push({
        name: 'where', label: 'Where should it go?', type: 'select', value: defaultWhere,
        options: [{ label: 'Home page (/) - the site front page', value: 'home' },
                  { label: 'Subpage (for example /about.html)', value: 'subpage' }],
        help: 'Publishes to ' + first.domain + '. The exact URL appears in the toast. ' +
              'A live page with different content is replaced only after this confirmation.'
      });
      fields.push({
        name: 'path', label: 'Subpage path', value: defaultPath,
        help: 'Used when you choose Subpage. A home-slug draft can go to /about.html ' +
              'without replacing the live home page. Use one name, such as about.'
      });
      // One publish, not two: this used to POST here and again inside sendPublish, which
      // wrote the page twice and asked for two approvals.
      formModal('Publish "' + (item.title || 'page') + '"', fields, function (data, fail, ok) {
        var chosen = sites.filter(function (s) {
          return String(s.id) === String(data.site || first.id);
        })[0] || first;
        if (data.where === 'subpage' && !(data.path || '').trim()) {
          fail('Give the subpage a name, such as about.');
          return;
        }
        ok();
        sendPublish(item, chosen.id, onDone, button, data.where === 'home', data.path);
      }, 'Publish');
    }).catch(function (e) {
      if (button) button.disabled = false;
      toast(e.message, false);
    });
  }

  function pagesCard() {
    var card = el('div', 'mt');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Pages</h2><p>Full pages the team has written. Preview one, then publish it to a site above.</p></div>';
    var newBtn = el('button', 'btn btn-ghost', '+ New page');
    newBtn.onclick = function () { showDeliverableForm('website', null, load); };
    head.appendChild(newBtn);
    card.appendChild(head);
    card.appendChild(aiComposer('website',
      'Describe the page you want. It is saved as a draft here, and you choose when to publish it.',
      load));
    var body = el('div');
    card.appendChild(body);

    function load() {
      api('GET', '/api/deliverables?kind=website&summary=1').then(function (d) {
        var items = d.deliverables || [];
        // The endpoint returns at most 200. Say so, because a list that stops without
        // saying so is how someone concludes a draft was never saved.
        if (items.length >= 200) {
          body.appendChild(el('p', 'fine-print',
            'Showing the 200 most recent. Older drafts are in the client Library view.'));
        }
        body.innerHTML = '';
        if (!items.length) {
          body.appendChild(el('p', 'muted', 'No pages yet. Ask the team above.'));
          return;
        }
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Title</th><th>Slug</th><th>Status</th><th></th></tr></thead>';
        var tbody = el('tbody');
        items.forEach(function (item) {
          var tr = el('tr');
          tr.innerHTML = '<td>' + esc(item.title) +
            (item.url ? '<div class="muted">' + esc(item.url) + '</div>' : '') +
            '</td><td class="muted">' + esc(item.slug || '') + '</td>' +
            '<td>' + deliverableBadge(item) + '</td>';
          var td = el('td', 'row-actions');
          var prev = el('button', 'btn btn-ghost btn-sm', 'Preview');
          prev.onclick = function () { previewDeliverable(item); };
          var studio = el('button', 'btn btn-primary btn-sm', 'Studio');
          studio.onclick = function () { openInStudio('deliverable', item.id); };
          var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
          edit.onclick = function () { showDeliverableForm('website', item, load); };
          td.appendChild(prev);
          td.appendChild(studio);
          // Only a website page has somewhere to be published. The button used to sit on
          // every kind and answered "Only website pages can be published".
          if (item.kind === 'website') {
            var pub = el('button', 'btn btn-ghost btn-sm', 'Publish');
            pub.onclick = function () { publishDeliverable(item, load, pub); };
            td.appendChild(pub);
          }
          td.appendChild(edit);
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
      }).catch(function (e) { toast(e.message, false); });
    }

    load();
    return card;
  }

  // Older drafts can have no client: they were saved before the client switcher was
  // required, so they appear in no client's tab. This is the operator's way to put them
  // where their owner will find them, without needing the repair_orphans.py script.
  function unownedDraftsCard(onAssigned) {
    if (!isOwner(state.user)) return null;
    var host = el('div');

    function load() {
      host.innerHTML = '';
      api('GET', '/api/deliverables/unowned').then(function (d) {
        var rows = d.deliverables || [];
        if (!rows.length) return;
        var card = el('div', 'panel-card mt');
        card.appendChild(el('h3', 'card-title', 'Unowned drafts (' + rows.length + ')'));
        card.appendChild(el('p', 'muted',
          'These were saved while no client was selected, so they appear in no ' +
          'client\'s tab. Assign them to the client they belong to, then switch to ' +
          'that client in the workspace switcher to see them in its tabs.'));
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th class="check"></th><th>Title</th><th>Kind</th>' +
          '<th>Created</th></tr></thead>';
        var tbody = el('tbody');
        var boxes = [];
        rows.forEach(function (item) {
          var tr = el('tr');
          var tick = el('td', 'check');
          var box = el('input');
          box.type = 'checkbox';
          box.value = String(item.id);
          boxes.push(box);
          tick.appendChild(box);
          tr.appendChild(tick);
          tr.appendChild(el('td', null, item.title || '(untitled)'));
          tr.appendChild(el('td', null, item.kind));
          tr.appendChild(el('td', 'muted', (item.created_at || '').slice(0, 16)));
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        card.appendChild(table);

        var actions = el('div', 'row-actions');
        var pick = el('select');
        pick.style.width = '220px';
        pick.style.minHeight = '36px';
        var blank = el('option', null, 'Choose a client…');
        blank.value = '';
        pick.appendChild(blank);
        api('GET', '/api/orgs').then(function (o) {
          (o.orgs || []).forEach(function (org) {
            var opt = el('option', null, org.name);
            opt.value = String(org.id);
            pick.appendChild(opt);
          });
        }).catch(function () { /* the picker stays empty */ });
        var go = el('button', 'btn btn-primary btn-sm', 'Assign selected');
        go.onclick = function () {
          var ids = boxes.filter(function (box) { return box.checked; }).map(function (box) {
            return Number(box.value);
          });
          if (!ids.length) { toast('Select at least one draft', false); return; }
          if (!pick.value) { toast('Choose the client they belong to', false); return; }
          go.disabled = true;
          api('POST', '/api/deliverables/unowned/assign', {
            org_id: Number(pick.value), ids: ids
          }).then(function (r) {
            var n = r.assigned || 0;
            var name = pick.options[pick.selectedIndex].text;
            toast(n ? 'Assigned ' + n + ' draft(s) to ' + name
                    : 'Those drafts were already assigned', !!n);
            load();
            if (onAssigned) onAssigned();
          }).catch(function (e) { go.disabled = false; toast(e.message, false); });
        };
        actions.appendChild(pick);
        actions.appendChild(go);
        card.appendChild(actions);
        host.appendChild(card);
      }).catch(function () { /* the tab still works */ });
    }

    load();
    return host;
  }

  function viewDeliverables(tab) {
    var meta = CONTENT_TABS[tab] || CONTENT_TABS.library;
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>' + esc(meta.title) + '</h2>' +
      '<p class="muted">' + esc(meta.blurb) + '</p></div>';
    var createBuild = el('button', 'btn btn-primary', 'Create with AI');
    createBuild.type = 'button';
    createBuild.onclick = function () { openBuildPicker(); };
    head.appendChild(createBuild);
    var add = el('button', 'btn btn-ghost', '+ Blank draft');
    add.onclick = function () { showDeliverableForm(meta.kind || 'blog', null, refresh); };
    add.disabled = true;         // enabled once we know there is a client to own the draft
    head.appendChild(add);
    container.appendChild(head);
    var unowned = unownedDraftsCard(refresh);
    if (unowned) container.appendChild(unowned);
    var composerHost = el('div');
    container.appendChild(composerHost);
    var templatesHost = el('div');
    container.appendChild(templatesHost);

    // Templates live with the Emails tab because that is where someone thinks of them: the
    // wording a send step points at, so an automation never carries an email inside itself.
    function loadTemplates() {
      api('GET', '/api/templates').then(function (d) {
        templatesHost.innerHTML = '';
        var card = el('div', 'panel-card');
        card.appendChild(el('h3', 'card-title', 'Templates'));
        card.appendChild(el('p', 'fine-print', 'A send step points at one of these. ' +
          '{{contact.name}} and {{contact.email}} are filled in from the contact.'));
        var rows = d.templates || [];
        if (!rows.length) {
          card.appendChild(el('p', 'muted', 'No templates yet - a sequence can only send ' +
            'freehand text until you add one.'));
        }
        rows.forEach(function (t) {
          var row = el('div', 'auto-proposal');
          var label = el('div');
          label.innerHTML = '<strong>' + esc(t.name) + '</strong>' +
            '<div class="fine-print">' + esc(t.subject || '(no subject)') + ' · #' +
            esc(String(t.id)) + '</div>';
          row.appendChild(label);
          // A template you cannot read is a template you cannot trust: preview renders it
          // through the same function a send uses, with a sample contact in the placeholders.
          var peek = el('button', 'btn btn-ghost btn-sm', 'Preview');
          peek.onclick = function () {
            api('GET', '/api/templates/' + t.id + '/preview').then(function (d) {
              var modal = el('div', 'auto-modal');
              var panel = el('div', 'auto-panel');
              panel.appendChild(el('h3', 'card-title', d.template.subject || '(no subject)'));
              var frame = el('iframe', 'preview-frame');
              frame.setAttribute('sandbox', '');
              frame.setAttribute('title', 'Template preview');
              frame.srcdoc = d.html;
              panel.appendChild(frame);
              panel.appendChild(el('p', 'fine-print', 'Rendered with ' + d.sample +
                ' in the placeholders. Nothing was sent.'));
              var close = el('button', 'btn', 'Close');
              close.onclick = function () { if (modal.parentNode) modal.parentNode.removeChild(modal); };
              panel.appendChild(close);
              modal.appendChild(panel);
              document.body.appendChild(modal);
            }).catch(function (e) { toast(e.message, false); });
          };
          row.appendChild(peek);
          var drop = el('button', 'btn btn-ghost btn-sm', 'Delete');
          drop.onclick = function () {
            api('DELETE', '/api/templates/' + t.id).then(function () {
              toast('Template deleted', true);
              loadTemplates();
            }).catch(function (e) { toast(e.message, false); });
          };
          row.appendChild(drop);
          card.appendChild(row);
        });
        var add = el('button', 'btn btn-ghost btn-sm', '+ New template');
        add.onclick = function () {
          formModal('New email template', [
            { name: 'name', label: 'Name', help: 'What a send step picks from' },
            { name: 'subject', label: 'Subject', help: '{{contact.name}} works here' },
            { name: 'body', label: 'Body', help: 'Placeholders are filled from the contact' }
          ], function (data, fail, ok) {
            api('POST', '/api/templates', data).then(function () {
              ok();
              toast('Template saved', true);
              loadTemplates();
            }).catch(function (e) { fail(e.message); });
          });
        };
        card.appendChild(add);
        templatesHost.appendChild(card);
      }).catch(function () { templatesHost.style.display = 'none'; });
    }
    if (meta.kind === 'email') loadTemplates();
    var body = el('div');
    container.appendChild(body);

    // A draft needs a client, or it lands in nobody's tab. Say that up front instead of
    // letting the save fail with a toast the user has to interpret.
    api('GET', '/api/session/org').then(function (state) {
      if (state && state.acting_org) {
        add.disabled = false;
        if (meta.kind) {
          composerHost.appendChild(aiComposer(meta.kind,
            'Describe what you want. The team saves it straight into this tab as a draft, ' +
            'so you can read and edit it without leaving the page.',
            refresh, function () { return body; }));
        }
        return;
      }
      add.disabled = true;
      var hint = el('div', 'panel-card empty-state');
      hint.innerHTML = '<p><strong>Pick a client to start a draft.</strong></p>' +
        '<p class="muted">Choose one in the workspace switcher under the logo. A draft ' +
        'with no client belongs to nobody, so it would never appear in a tab.</p>';
      composerHost.appendChild(hint);
    }).catch(function () { add.disabled = false; });

    function refresh() {
      var url = '/api/deliverables?summary=1' + (meta.kind ? '&kind=' + meta.kind : '');
      api('GET', url).then(function (d) {
        body.innerHTML = '';
        var items = d.deliverables || [];
        // The endpoint returns at most 200. Say so, because a list that stops without
        // saying so is how someone concludes a draft was never saved.
        if (items.length >= 200) {
          body.appendChild(el('p', 'fine-print',
            'Showing the 200 most recent. Older drafts are in the client Library view.'));
        }
        var counts = d.counts || {};
        body.appendChild(el('p', 'muted',
          'Blog posts ' + (counts.blog || 0) + ' · Landing pages ' + (counts.funnel || 0) +
          ' · Emails ' + (counts.email || 0) + ' · Websites ' + (counts.website || 0) +
          ' · Everything ' + (counts.all || 0)));
        if (!items.length) {
          var empty = el('div', 'empty-state');
          empty.innerHTML = '<div class="big">' + icon('library') + '</div>' +
            '<strong>Nothing here yet.</strong>' +
            '<p>The team writes these, and you can add a draft yourself.</p>';
          body.appendChild(empty);
          return;
        }
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Title</th><th>Kind</th><th>Status</th>' +
          '<th>Updated</th><th></th></tr></thead>';
        var tbody = el('tbody');
        items.forEach(function (item) {
          var tr = el('tr');
          tr.innerHTML = '<td>' + esc(item.title) +
            (item.slug ? '<div class="muted">/' + esc(item.slug) + '</div>' : '') +
            (item.job_id ? '<div class="muted">from job #' + esc(item.job_id) + '</div>' : '') + '</td>' +
            '<td><span class="badge">' + esc(item.kind) + '</span></td>' +
            '<td>' + deliverableBadge(item) + '</td>' +
            '<td class="muted">' + esc((item.updated_at || '').slice(0, 16)) + '</td>';
          var td = el('td', 'row-actions');
          var preview = el('button', 'btn btn-ghost btn-sm', 'Preview');
          preview.onclick = function () { previewDeliverable(item); };
          td.appendChild(preview);
          var studio = el('button', 'btn btn-primary btn-sm', 'Studio');
          studio.onclick = function () { openInStudio('deliverable', item.id); };
          td.appendChild(studio);
          var open = el('button', 'btn btn-ghost btn-sm', 'Open');
          open.onclick = function () { showDeliverableForm(item.kind, item, refresh); };
          td.appendChild(open);
          if (item.kind === 'website' || item.kind === 'blog') {
            var pub = el('button', 'btn btn-primary btn-sm', 'Publish');
            pub.onclick = function () { publishDeliverable(item, refresh, pub); };
            td.appendChild(pub);
          }
          var del = el('button', 'btn btn-danger btn-sm', 'Delete');
          del.onclick = function () {
            if (!confirm('Delete "' + item.title + '"?')) return;
            api('DELETE', '/api/deliverables/' + item.id)
              .then(function () { toast('Deleted', true); refresh(); })
              .catch(function (e) { toast(e.message, false); });
          };
          td.appendChild(del);
          tr.appendChild(td);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
      }).catch(function (e) { toast(e.message, false); });
    }
    refresh();
    return container;
  }

  function showDeliverableForm(kind, item, done) {
    if (item && item.id && item.content === undefined) {
      // The edit form must start from the full document, never from a summary row.
      api('GET', '/api/deliverables/' + item.id).then(function (d) {
        showDeliverableForm(kind, d.deliverable || item, done);
      }).catch(function (e) { toast(e.message, false); });
      return;
    }
    var isNew = !item;
    var fields = [
      { name: 'title', label: 'Title', value: item ? item.title : '' },
      { name: 'kind', label: 'Kind', type: 'select', options: ['blog', 'funnel', 'email', 'website', 'asset'],
        value: item ? item.kind : kind }
    ];
    // A website page becomes published only through Publish, which writes the file and
    // records the approval. Letting the edit form set "published" on changed content
    // is how a draft report came to look live while the old page was still served.
    if (isNew || (item.kind !== 'website' && kind !== 'website')) {
      fields.push({
        name: 'status', label: 'Status', type: 'select',
        options: ['draft', 'scheduled', 'published', 'failed'],
        value: item ? item.status : 'draft'
      });
    }
    fields.push(
      { name: 'slug', label: 'Slug', value: item ? item.slug : '' },
      { name: 'summary', label: 'Summary', type: 'textarea', value: item ? item.summary : '' },
      { name: 'content', label: 'Content', type: 'textarea', value: item ? item.content : '' },
      { name: 'url', label: 'Published URL', value: item ? item.url : '',
        placeholder: 'https://acme.com/blog/post' }
    );
    formModal(isNew ? 'New ' + kind : item.title, fields, function (data, fail, ok) {
      var request = isNew
        ? api('POST', '/api/deliverables', data)
        : api('PUT', '/api/deliverables/' + item.id, data);
      request.then(function () { ok(); toast('Saved', true); done(); })
        .catch(function (e) { fail(e.message); });
    });
  }

  // ---------- Media ----------
  // Uploaded images, video and PDFs. The server refuses an upload without alt text
  // and a description; these controls make that rule obvious before the request.
  function formatBytes(value) {
    var n = Number(value || 0);
    if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' KB';
    return n + ' B';
  }

  function mediaKindLabel(kind) {
    if (kind === 'video') return 'Video';
    if (kind === 'document') return 'PDF';
    return 'Image';
  }

  function mediaMeta(item) {
    var parts = [mediaKindLabel(item.kind)];
    if (item.kind === 'image' && item.width && item.height) {
      parts.push(item.width + '×' + item.height);
    }
    if (item.kind === 'document' && item.pages) {
      parts.push(item.pages + ' page' + (item.pages === 1 ? '' : 's'));
    }
    parts.push(formatBytes(item.bytes));
    return parts.join(' · ');
  }

  function copyMedia(text, label) {
    if (!(navigator.clipboard && navigator.clipboard.writeText)) {
      toast('Clipboard is not available here', false);
      return;
    }
    navigator.clipboard.writeText(text || '').then(function () {
      toast(label + ' copied', true);
    }).catch(function () { toast('Could not copy', false); });
  }

  function mediaPreview(item) {
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide');
    modal.appendChild(el('h3', null, item.original_name || 'Media'));
    var frame = el('div', 'media-preview-full');
    if (item.kind === 'image') {
      var img = el('img');
      img.src = item.url;
      img.alt = item.alt_text;
      frame.appendChild(img);
    } else if (item.kind === 'video') {
      var video = el('video');
      video.src = item.url;
      video.controls = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.setAttribute('aria-label', item.alt_text);
      frame.appendChild(video);
    } else {
      var doc = el('div', 'media-document');
      doc.appendChild(el('div', 'media-doc-badge', 'PDF'));
      doc.appendChild(el('p', null, item.original_name || 'PDF'));
      doc.appendChild(el('p', 'muted', item.pages ? item.pages + ' page(s)' : 'PDF document'));
      var open = el('a', 'btn btn-primary', 'Open / download PDF');
      open.href = item.url;
      open.target = '_blank';
      open.rel = 'noopener';
      doc.appendChild(open);
      frame.appendChild(doc);
    }
    modal.appendChild(frame);
    modal.appendChild(el('p', 'media-alt', 'Alt: ' + (item.alt_text || '')));
    modal.appendChild(el('p', 'muted', item.description || ''));
    var actions = el('div', 'row-actions');
    var copyUrl = el('button', 'btn btn-ghost', 'Copy URL');
    copyUrl.onclick = function () { copyMedia(item.url, 'URL'); };
    actions.appendChild(copyUrl);
    if (item.kind === 'image') {
      var copyHtml = el('button', 'btn btn-ghost', 'Copy HTML');
      copyHtml.onclick = function () { copyMedia(item.site_html, 'HTML'); };
      actions.appendChild(copyHtml);
    } else if (item.kind === 'video') {
      var page = el('button', 'btn btn-ghost', 'Copy page HTML');
      page.onclick = function () { copyMedia(item.site_html, 'Page HTML'); };
      var email = el('button', 'btn btn-ghost', 'Copy email link');
      email.onclick = function () { copyMedia(item.email_html, 'Email link'); };
      actions.appendChild(page);
      actions.appendChild(email);
    } else {
      var link = el('button', 'btn btn-ghost', 'Copy link');
      link.onclick = function () { copyMedia(item.email_html, 'Link'); };
      actions.appendChild(link);
    }
    var close = el('button', 'btn btn-primary', 'Close');
    close.onclick = function () { backdrop.remove(); };
    actions.appendChild(close);
    modal.appendChild(actions);
    backdrop.appendChild(modal);
    backdrop.onclick = function (event) { if (event.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);
  }

  function editMedia(item, refresh) {
    formModal('Edit media', [
      { name: 'alt_text', label: 'Alt text / accessible label', value: item.alt_text,
        help: 'Required. Read by screen readers and used in emails.' },
      { name: 'description', label: 'Description', type: 'textarea',
        value: item.description,
        help: 'Required. What it shows and where it can be used.' }
    ], function (data, fail, ok) {
      if (!String(data.alt_text || '').trim()) { fail('Alt text is required.'); return; }
      if (!String(data.description || '').trim()) { fail('Description is required.'); return; }
      api('PUT', '/api/media/' + item.id, {
        alt_text: data.alt_text,
        description: data.description
      }).then(function () {
        ok();
        toast('Media updated', true);
        refresh();
      }).catch(function (e) { fail(e.message); });
    });
  }

  function deleteMedia(item, refresh) {
    if (!confirm('Delete "' + (item.original_name || 'this media') + '"?\n\n' +
                 'Any email or page using it will show a broken image or link.')) return;
    api('DELETE', '/api/media/' + item.id).then(function () {
      toast('Media deleted', true);
      refresh();
    }).catch(function (e) { toast(e.message, false); });
  }

  function mediaUploadCard(getLimit, onUploaded) {
    var card = el('div', 'panel-card media-form');
    card.appendChild(el('h3', 'card-title', 'Upload media'));
    card.appendChild(el('p', 'muted',
      'Images, video or PDFs for emails, sites and posts. Alt text and a description ' +
      'are required before the upload can be saved.'));
    var file = null;
    var objectUrl = '';
    var previewImg = null;
    var previewVideo = null;

    var pick = el('input');
    pick.type = 'file';
    pick.id = 'media-file';
    pick.accept = 'image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm,application/pdf';
    var drop = el('label', 'media-drop');
    drop.setAttribute('for', 'media-file');
    drop.appendChild(el('strong', null, 'Choose a file'));
    drop.appendChild(el('span', 'muted', 'PNG, JPEG, WebP, GIF, MP4, WebM or PDF'));
    drop.appendChild(pick);
    card.appendChild(drop);

    var preview = el('div', 'media-preview');
    preview.hidden = true;
    card.appendChild(preview);

    var alt = el('input');
    alt.type = 'text';
    alt.placeholder = 'Describe what is visible';
    alt.maxLength = 300;
    var altField = el('label', 'field media-required');
    altField.appendChild(el('span', null, 'Alt text / accessible label'));
    altField.appendChild(alt);
    altField.appendChild(el('small', 'muted',
      'Required. For images this becomes the alt attribute; for video/PDF it is the ' +
      'accessible label or link text.'));
    card.appendChild(altField);

    var desc = el('textarea');
    desc.placeholder = 'What it shows and where it can be used';
    desc.maxLength = 2000;
    var descField = el('label', 'field media-required');
    descField.appendChild(el('span', null, 'Description'));
    descField.appendChild(desc);
    descField.appendChild(el('small', 'muted',
      'Required. The team and the AI use this to choose the right file.'));
    card.appendChild(descField);

    var hint = el('p', 'fine-print', 'Up to ' + formatBytes(getLimit()) + '.');
    card.appendChild(hint);
    var error = el('div', 'form-error');
    card.appendChild(error);
    var bar = el('progress', 'media-progress');
    bar.max = 100;
    bar.value = 0;
    bar.hidden = true;
    card.appendChild(bar);
    var go = el('button', 'btn btn-primary', 'Upload media');
    go.disabled = true;
    card.appendChild(go);

    function sync() {
      go.disabled = !(file && alt.value.trim() && desc.value.trim());
      if (previewImg) previewImg.alt = alt.value.trim();
      if (previewVideo) previewVideo.setAttribute('aria-label', alt.value.trim());
    }
    function clearPreview() {
      if (objectUrl) {
        try { URL.revokeObjectURL(objectUrl); } catch (e) { /* already gone */ }
        objectUrl = '';
      }
      preview.innerHTML = '';
      preview.hidden = true;
      previewImg = null;
      previewVideo = null;
    }
    function showPreview(chosen) {
      clearPreview();
      objectUrl = URL.createObjectURL(chosen);
      preview.hidden = false;
      if (chosen.type.indexOf('image/') === 0) {
        previewImg = el('img');
        previewImg.src = objectUrl;
        previewImg.alt = alt.value.trim();
        preview.appendChild(previewImg);
      } else if (chosen.type.indexOf('video/') === 0) {
        previewVideo = el('video');
        previewVideo.src = objectUrl;
        previewVideo.controls = true;
        previewVideo.playsInline = true;
        previewVideo.setAttribute('aria-label', alt.value.trim());
        preview.appendChild(previewVideo);
      } else {
        preview.appendChild(el('div', 'media-doc-badge', 'PDF'));
        preview.appendChild(el('p', null, chosen.name));
      }
    }

    pick.onchange = function () {
      error.textContent = '';
      var chosen = pick.files && pick.files[0];
      if (!chosen) { file = null; clearPreview(); sync(); return; }
      var name = (chosen.name || '').toLowerCase();
      var typeOk = chosen.type.indexOf('image/') === 0 ||
        chosen.type.indexOf('video/') === 0 ||
        chosen.type === 'application/pdf' ||
        /\.(png|jpe?g|webp|gif|mp4|webm|pdf)$/.test(name);
      if (!typeOk) {
        error.textContent = 'Choose an image, video or PDF.';
        pick.value = '';
        file = null;
        clearPreview();
        sync();
        return;
      }
      var limit = getLimit();
      if (chosen.size > limit) {
        error.textContent = 'That file is ' + formatBytes(chosen.size) +
          '; the limit is ' + formatBytes(limit) + '.';
        pick.value = '';
        file = null;
        clearPreview();
        sync();
        return;
      }
      file = chosen;
      showPreview(chosen);
      sync();
    };
    alt.addEventListener('input', sync);
    desc.addEventListener('input', sync);

    go.onclick = function () {
      if (!file) return;
      var form = new FormData();
      form.append('file', file);
      form.append('alt_text', alt.value.trim());
      form.append('description', desc.value.trim());
      go.disabled = true;
      error.textContent = '';
      bar.hidden = false;
      bar.value = 0;
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/media');
      xhr.withCredentials = true;
      xhr.upload.onprogress = function (event) {
        if (event.lengthComputable) {
          bar.value = Math.round((event.loaded / event.total) * 100);
        }
      };
      xhr.onload = function () {
        var data = {};
        try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) { data = {}; }
        if (xhr.status >= 200 && xhr.status < 300) {
          toast('Uploaded - it is in the Media library', true);
          pick.value = '';
          file = null;
          alt.value = '';
          desc.value = '';
          clearPreview();
          bar.hidden = true;
          sync();
          if (onUploaded) onUploaded();
        } else {
          error.textContent = data.error || ('Upload failed (' + xhr.status + ')');
          go.disabled = false;
          bar.hidden = true;
        }
      };
      xhr.onerror = function () {
        error.textContent = 'Network error - try again.';
        go.disabled = false;
        bar.hidden = true;
      };
      xhr.send(form);
    };
    return { card: card, hint: hint };
  }

  function mediaCard(item, refresh) {
    var card = el('article', 'media-card');
    var thumb = el('button', 'media-thumb');
    thumb.type = 'button';
    thumb.title = 'Preview ' + (item.original_name || 'media');
    if (item.kind === 'image') {
      var img = el('img');
      img.src = item.thumb_url || item.url;
      img.alt = item.alt_text;
      img.loading = 'lazy';
      thumb.appendChild(img);
    } else if (item.kind === 'video') {
      thumb.className += ' media-thumb-video';
      thumb.appendChild(el('span', 'media-play', '▶'));
      thumb.appendChild(el('span', 'media-tile-label', 'Video'));
    } else {
      thumb.className += ' media-thumb-doc';
      thumb.appendChild(el('span', 'media-doc-badge', 'PDF'));
      thumb.appendChild(el('span', 'media-tile-label',
        item.pages ? item.pages + ' page(s)' : 'PDF'));
    }
    thumb.onclick = function () { mediaPreview(item); };
    card.appendChild(thumb);

    var body = el('div', 'media-body');
    body.appendChild(el('h3', 'media-name', item.original_name || 'Untitled'));
    body.appendChild(el('p', 'media-meta', mediaMeta(item)));
    if (item.org_name) body.appendChild(el('p', 'fine-print', item.org_name));
    body.appendChild(el('p', 'media-alt', 'Alt: ' + (item.alt_text || '')));
    body.appendChild(el('p', 'media-desc', item.description || ''));

    var actions = el('div', 'media-actions');
    var copyUrl = el('button', 'btn btn-ghost btn-sm', 'Copy URL');
    copyUrl.onclick = function () { copyMedia(item.url, 'URL'); };
    actions.appendChild(copyUrl);
    if (item.kind === 'image') {
      var html = el('button', 'btn btn-ghost btn-sm', 'Copy HTML');
      html.onclick = function () { copyMedia(item.site_html, 'HTML'); };
      actions.appendChild(html);
    } else if (item.kind === 'video') {
      var page = el('button', 'btn btn-ghost btn-sm', 'Copy page HTML');
      page.onclick = function () { copyMedia(item.site_html, 'Page HTML'); };
      var email = el('button', 'btn btn-ghost btn-sm', 'Copy email link');
      email.onclick = function () { copyMedia(item.email_html, 'Email link'); };
      actions.appendChild(page);
      actions.appendChild(email);
    } else {
      var link = el('button', 'btn btn-ghost btn-sm', 'Copy link');
      link.onclick = function () { copyMedia(item.email_html, 'Link'); };
      actions.appendChild(link);
    }
    if (canAdmin(state.user)) {
      var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
      edit.onclick = function () { editMedia(item, refresh); };
      actions.appendChild(edit);
      var del = el('button', 'btn btn-danger btn-sm', 'Delete');
      del.onclick = function () { deleteMedia(item, refresh); };
      actions.appendChild(del);
    }
    body.appendChild(actions);
    card.appendChild(body);
    return card;
  }

  function viewMedia() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Media</h2><p class="muted">Images, video and PDFs ' +
      'for emails, sites and posts. Every file needs alt text and a description ' +
      'before it can be uploaded.</p></div>';
    container.appendChild(head);

    var maxBytes = 50000000;
    var uploadHint = null;
    var uploadHost = el('div');
    container.appendChild(uploadHost);

    var toolbar = el('div', 'media-toolbar');
    var search = el('input', 'media-search');
    search.type = 'search';
    search.placeholder = 'Search media…';
    var filter = el('select', 'media-filter');
    [['', 'All media'], ['image', 'Images'], ['video', 'Video'], ['document', 'PDFs']]
      .forEach(function (pair) {
        var opt = el('option', null, pair[1]);
        opt.value = pair[0];
        filter.appendChild(opt);
      });
    var count = el('span', 'muted', '');
    toolbar.appendChild(search);
    toolbar.appendChild(filter);
    toolbar.appendChild(count);
    container.appendChild(toolbar);

    var grid = el('div', 'media-grid');
    container.appendChild(grid);

    var timer = 0;
    search.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(refresh, 250);
    });
    filter.onchange = refresh;

    function refresh() {
      var url = '/api/media?q=' + encodeURIComponent(search.value.trim()) +
        (filter.value ? '&kind=' + filter.value : '');
      api('GET', url).then(function (d) {
        maxBytes = Number(d.max_bytes || maxBytes);
        if (uploadHint) uploadHint.textContent = 'Up to ' + formatBytes(maxBytes) + '.';
        grid.innerHTML = '';
        var rows = d.media || [];
        count.textContent = rows.length + ' item(s)';
        if (!rows.length) {
          var empty = el('div', 'panel-card empty-state');
          empty.innerHTML = '<div class="big">' + icon('image') + '</div>' +
            '<strong>No media yet.</strong>' +
            '<p>Upload an image, video or PDF to use it in an email or page.</p>';
          grid.appendChild(empty);
          return;
        }
        rows.forEach(function (item) { grid.appendChild(mediaCard(item, refresh)); });
      }).catch(function (e) { toast(e.message, false); });
    }

    if (canAdmin(state.user)) {
      api('GET', '/api/session/org').then(function (session) {
        if (!session || !session.acting_org) {
          var hint = el('div', 'panel-card empty-state');
          hint.innerHTML = '<p><strong>Pick a client to upload media.</strong></p>' +
            '<p class="muted">Choose one in the workspace switcher under the logo. ' +
            'Media belongs to a client, so it never lands where nobody can find it.</p>';
          uploadHost.appendChild(hint);
          return;
        }
        var built = mediaUploadCard(function () { return maxBytes; }, refresh);
        uploadHint = built.hint;
        uploadHint.textContent = 'Up to ' + formatBytes(maxBytes) + '.';
        uploadHost.appendChild(built.card);
      }).catch(function () {
        var built = mediaUploadCard(function () { return maxBytes; }, refresh);
        uploadHint = built.hint;
        uploadHost.appendChild(built.card);
      });
    } else {
      uploadHost.appendChild(el('p', 'muted',
        'You can preview and copy media. Only a client admin can upload, edit or delete.'));
    }

    refresh();
    return container;
  }

  // ---------- Public: accepting an invitation ----------
  // Rendered before the auth gate: the visitor has no session yet, and the whole point
  // of the link is to create one.
  function viewAccept(token) {
    var wrap = el('div', 'auth-wrap');
    var card = el('div', 'auth-card');
    card.innerHTML = '<img class="auth-logo" src="/admin/logo-trim.png" alt="' +
      esc(BRAND) + '" /><h1>Create your account</h1>' +
      '<p class="sub">Choose a password to finish joining your workspace.</p>';
    var who = el('p', 'muted', 'Checking your invitation…');
    card.appendChild(who);

    var form = el('form', 'auth-form');
    var error = authError();
    var password = authPassword('Password', 'At least 8 characters');
    password.input.autocomplete = 'new-password';
    var confirm = authPassword('Confirm password', 'Repeat your password');
    var submit = el('button', 'btn btn-primary', 'Create password and sign in');
    submit.type = 'submit';
    submit.disabled = true;
    form.appendChild(error);
    form.appendChild(password.field);
    form.appendChild(confirm.field);
    form.appendChild(submit);
    form.appendChild(authBack('#/login', 'Already have an account? Sign in'));
    card.appendChild(form);

    var usable = false;
    function update() {
      var ready = usable && password.input.value.length >= 8 &&
        password.input.value === confirm.input.value;
      submit.disabled = !ready;
      if (password.input.value && password.input.value.length < 8) {
        error.textContent = 'Use at least 8 characters';
      } else if (confirm.input.value && password.input.value !== confirm.input.value) {
        error.textContent = 'The two passwords do not match';
      } else if (error.textContent === 'The two passwords do not match' ||
                 error.textContent === 'Use at least 8 characters') {
        error.textContent = '';
      }
    }
    password.input.oninput = update;
    confirm.input.oninput = update;

    api('GET', '/api/invites/' + encodeURIComponent(token)).then(function (d) {
      var inv = d.invite;
      who.textContent = (inv.name ? inv.name + ' · ' : '') + inv.email +
        (inv.org ? ' · ' + inv.org : '');
      if (inv.status !== 'pending') {
        error.textContent = inv.status === 'accepted'
          ? 'This invitation has already been used - sign in instead.'
          : 'This invitation is ' + inv.status + '. Ask for a new one.';
        return;
      }
      usable = true;
      update();
    }).catch(function (e) {
      who.textContent = '';
      error.textContent = e.message;
    });

    form.onsubmit = function (ev) {
      ev.preventDefault();
      if (!usable) return;
      if (password.input.value.length < 8) {
        error.textContent = 'Use at least 8 characters';
        return;
      }
      if (password.input.value !== confirm.input.value) {
        error.textContent = 'The two passwords do not match';
        return;
      }
      submit.disabled = true;
      api('POST', '/api/invites/accept', {
        token: token, password: password.input.value,
        confirm_password: confirm.input.value
      }).then(function () {
        location.replace('#/');
        location.reload();
      }).catch(function (e) {
        submit.disabled = false;
        error.textContent = e.message;
      });
    };

    wrap.appendChild(card);
    return wrap;
  }

  // The invitation template: a prebuilt HTML body with the merge values already in it,
  // edited as HTML beside a sandboxed live preview. The chips insert at the cursor, so
  // a value cannot be mistyped into a literal "{{name}}" that never gets replaced.
  function templateCard(data, onSaved) {
    var template = data.template || {};
    var tokens = data.tokens || [];
    var card = el('div', 'form-card');
    card.appendChild(el('h3', null, 'Invitation template'));
    card.appendChild(el('p', 'muted',
      'This is the email an invitation sends. Click a value to insert it at the cursor; ' +
      'the preview fills them with sample data.'));

    var subject = el('input');
    subject.value = template.subject || '';
    subject.placeholder = "You're invited to AscendPlatform";
    card.appendChild(el('p', 'muted', 'Subject'));
    card.appendChild(subject);

    var chips = el('div', 'token-list');
    tokens.forEach(function (token) {
      var chip = el('button', 'btn btn-ghost btn-sm', '{{' + token.key + '}}');
      chip.title = token.label + ' - e.g. ' + token.sample;
      chip.onclick = function (event) {
        event.preventDefault();
        insertToken(body, '{{' + token.key + '}}');
        draw();
      };
      chips.appendChild(chip);
    });
    card.appendChild(chips);

    var body = el('textarea', 'template-editor');
    body.value = template.body || '';
    var frame = el('iframe', 'preview-frame template-preview');
    frame.setAttribute('sandbox', '');
    var grid = el('div', 'template-grid');
    grid.appendChild(body);
    grid.appendChild(frame);
    card.appendChild(grid);

    function fill(text) {
      var out = text || '';
      tokens.forEach(function (token) {
        out = out.split('{{' + token.key + '}}').join(token.sample);
      });
      return out;
    }

    function draw() {
      frame.srcdoc = fill(body.value) || '<p style="padding:16px">Nothing yet.</p>';
    }

    body.addEventListener('input', draw);
    draw();

    var row = el('div', 'row-actions');
    var reset = el('button', 'btn btn-ghost', 'Reset to the built-in template');
    reset.onclick = function () {
      body.value = template.default_body || '';
      draw();
    };
    var save = el('button', 'btn btn-primary', 'Save template');
    save.onclick = function () {
      save.disabled = true;
      api('PUT', '/api/settings', {
        values: { INVITE_SUBJECT: subject.value, INVITE_BODY_HTML: body.value }
      }).then(function () {
        toast('Invitation template saved', true);
        onSaved();
      }).catch(function (e) { toast(e.message, false); })
        .finally(function () { save.disabled = false; });
    };
    row.appendChild(reset);
    row.appendChild(save);
    card.appendChild(row);
    return card;
  }

  function insertToken(field, text) {
    var start = field.selectionStart == null ? field.value.length : field.selectionStart;
    var end = field.selectionEnd == null ? start : field.selectionEnd;
    field.value = field.value.slice(0, start) + text + field.value.slice(end);
    field.selectionStart = field.selectionEnd = start + text.length;
    field.focus();
  }

  // ---------- Platform: every person in the agency ----------
  function viewPlatformPeople() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>People</h2><p class="muted">Agency staff and every ' +
      'client contact in one directory. Client admins manage their own Team page; ' +
      'this is the agency-wide view.</p></div>';
    var actions = el('div', 'page-actions');
    var add = el('button', 'btn btn-primary', '+ Add person');
    add.onclick = function () { personModal(null, true, orgs, loadAll); };
    var invite = el('button', 'btn btn-ghost', 'Invite someone');
    invite.onclick = function () { invitePersonModal(true, orgs, null, loadAll); };
    actions.appendChild(add);
    actions.appendChild(invite);
    head.appendChild(actions);
    container.appendChild(head);

    var orgs = [];
    var people = [];
    var invites = [];
    var search = el('input');
    search.type = 'search';
    search.placeholder = 'Search people by name or email';
    search.className = 'search-input';
    var orgFilter = el('select');
    orgFilter.innerHTML = '<option value="">All clients</option>' +
      '<option value="agency">Agency only</option>';
    var roleFilter = el('select');
    roleFilter.innerHTML = '<option value="">All roles</option>' +
      '<option value="owner">Agency owner</option>' +
      '<option value="client_admin">Client admin</option>' +
      '<option value="member">Member</option>';
    var statusFilter = el('select');
    statusFilter.innerHTML = '<option value="">All statuses</option>' +
      '<option value="active">Active</option><option value="suspended">Suspended</option>';
    var bar = el('div', 'chip-input');
    bar.appendChild(search); bar.appendChild(orgFilter);
    bar.appendChild(roleFilter); bar.appendChild(statusFilter);
    container.appendChild(bar);

    var body = el('div', 'mt');
    var inviteBox = el('div', 'mt');
    container.appendChild(body);
    container.appendChild(inviteBox);

    function drawOrgs() {
      orgFilter.innerHTML = '<option value="">All clients</option>' +
        '<option value="agency">Agency only</option>' +
        orgs.map(function (o) {
          return '<option value="' + esc(String(o.id)) + '">' + esc(o.name) + '</option>';
        }).join('');
    }

    function draw() {
      var needle = (search.value || '').trim().toLowerCase();
      var shown = people.filter(function (u) {
        if (needle && ((u.name || '') + ' ' + (u.email || '')).toLowerCase()
            .indexOf(needle) === -1) return false;
        if (orgFilter.value === 'agency' && u.org_id) return false;
        if (orgFilter.value && orgFilter.value !== 'agency' &&
            String(u.org_id || '') !== orgFilter.value) return false;
        if (roleFilter.value && u.role !== roleFilter.value) return false;
        if (statusFilter.value && u.status !== statusFilter.value) return false;
        return true;
      });
      body.innerHTML = '';
      body.appendChild(peopleTable(shown, true, orgs, loadAll));
      inviteBox.innerHTML = '';
      var heading = el('div', 'page-head');
      heading.innerHTML = '<div><h3>Pending invitations</h3><p class="muted">' +
        'Invitations only create client admins and members. Agency owners are added ' +
        'directly so a link can never grant platform access.</p></div>';
      inviteBox.appendChild(heading);
      inviteBox.appendChild(inviteRows(invites, orgs, loadAll));
    }

    function loadAll() {
      api('GET', '/api/platform/users').then(function (d) {
        people = d.users || [];
        draw();
      }).catch(function (e) { toast(e.message, false); });
      api('GET', '/api/invites').then(function (d) {
        invites = d.invites || [];
        draw();
      }).catch(function () { /* the directory still works without invites */ });
    }

    api('GET', '/api/orgs').then(function (d) {
      orgs = d.orgs || [];
      drawOrgs();
      loadAll();
    }).catch(function (e) { toast(e.message, false); });

    search.oninput = draw;
    orgFilter.onchange = draw;
    roleFilter.onchange = draw;
    statusFilter.onchange = draw;
    return container;
  }

  // ---------- Platform: internal agency bug tracker ----------
  function bugStatusBadge(status) {
    var cls = status === 'resolved' || status === 'closed' ? 'badge-done'
      : (status === 'in_progress' ? 'badge-running' : 'badge-warn');
    var labels = { open: 'Open', in_progress: 'In progress', resolved: 'Resolved',
                   closed: 'Closed' };
    return '<span class="badge ' + cls + '">' + esc(labels[status] || status) + '</span>';
  }

  function bugPriorityBadge(priority) {
    var cls = priority === 'urgent' ? 'badge-danger'
      : (priority === 'high' ? 'badge-warn'
        : (priority === 'normal' ? 'badge-admin' : 'badge-member'));
    return '<span class="badge ' + cls + '">' +
      esc(String(priority || '').replace(/^[a-z]/, function (c) { return c.toUpperCase(); })) +
      '</span>';
  }

  function bugEventText(event) {
    var detail = event.detail || {};
    if (event.action === 'commented') return detail.comment || 'Comment';
    if (event.action === 'created') return 'Reported this bug';
    if (event.action === 'status') {
      return 'Status: ' + String(detail.from || '') + ' → ' + String(detail.to || '');
    }
    if (event.action === 'priority') {
      return 'Priority: ' + String(detail.from || '') + ' → ' + String(detail.to || '');
    }
    if (event.action === 'assigned') {
      return detail.to ? 'Assigned to #' + detail.to : 'Unassigned';
    }
    var fields = [];
    if (detail.title) fields.push('title');
    if (detail.description) fields.push('description');
    if (detail.page) fields.push('page');
    if (detail.field) fields.push(String(detail.field).replace(/_/g, ' '));
    return fields.length ? 'Updated ' + fields.join(', ') : 'Updated';
  }

  function openBugDetail(bugId, options) {
    api('GET', '/api/platform/bugs/' + bugId).then(function (d) {
      openBugModal(d.bug, d.events || [], options || {});
    }).catch(function (e) { toast(e.message, false); });
  }

  // One bug editor for the tracker and the client Support tab. `options` carries
  // the shared org/assignee lists, and `lockClient` keeps an issue reported from a
  // client page attached to that client instead of offering every tenant.
  function openBugModal(bug, events, options) {
    options = options || {};
    var orgs = options.orgs || [];
    var assignees = options.assignees || [];
    var lockClient = !!options.lockClient;
    var defaultOrgId = options.defaultOrgId || null;
    var editing = !!bug;
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide bug-modal');
    modal.appendChild(el('h3', null, editing ? 'Bug #' + bug.id : 'Report a bug'));
    var form = el('form', 'form bug-form');
    var error = el('div', 'form-error');
    var title = el('input');
    title.value = editing ? bug.title : '';
    title.placeholder = 'A short summary, e.g. Publish button does nothing';
    var description = el('textarea');
    description.value = editing ? bug.description : '';
    description.placeholder = 'What happened, what you expected, and anything you tried';
    function field(labelText, input, help) {
      var wrap = el('label', 'field');
      wrap.appendChild(el('span', null, labelText));
      wrap.appendChild(input);
      if (help) wrap.appendChild(el('small', 'muted', help));
      return wrap;
    }
    function select(optionsList, current) {
      var node = el('select');
      optionsList.forEach(function (pair) {
        var option = el('option', null, pair[1]);
        option.value = pair[0];
        node.appendChild(option);
      });
      node.value = current || '';
      return node;
    }
    var statusSelect = select([
      ['open', 'Open'], ['in_progress', 'In progress'],
      ['resolved', 'Resolved'], ['closed', 'Closed']], editing ? bug.status : 'open');
    var prioritySelect = select([
      ['urgent', 'Urgent'], ['high', 'High'], ['normal', 'Normal'], ['low', 'Low']],
      editing ? bug.priority : 'normal');

    var clientOptions = [['', 'No client']].concat(orgs.map(function (org) {
      return [String(org.id), org.name];
    }));
    var currentOrg = editing && bug.org_id ? String(bug.org_id)
      : (defaultOrgId ? String(defaultOrgId) : '');
    if (lockClient) {
      var locked = orgs.filter(function (org) {
        return String(org.id) === currentOrg;
      })[0];
      clientOptions = currentOrg
        ? [[currentOrg, (locked && locked.name) || 'This client']]
        : [['', 'No client']];
    }
    var clientSelect = select(clientOptions, currentOrg);
    if (lockClient) clientSelect.disabled = true;
    var assigneeSelect = select([['', 'Unassigned']].concat(assignees.map(function (person) {
      return [String(person.id), person.name || person.email];
    })), editing && bug.assignee_id ? String(bug.assignee_id) : '');
    var page = el('input');
    page.value = editing ? (bug.page || '') : (location.hash || '');
    page.placeholder = '#/platform/clients or a screen name';
    form.appendChild(field('Title', title));
    form.appendChild(field('Description', description));
    var row = el('div', 'form-row');
    row.appendChild(field('Status', statusSelect));
    row.appendChild(field('Priority', prioritySelect));
    form.appendChild(row);
    row = el('div', 'form-row');
    row.appendChild(field('Client', clientSelect,
      lockClient ? 'This issue stays attached to this client.' : ''));
    row.appendChild(field('Assignee', assigneeSelect));
    form.appendChild(row);
    form.appendChild(field('Page or screen', page, 'Optional context for whoever fixes it.'));
    form.appendChild(error);
    var buttons = el('div', 'modal-actions');
    var cancel = el('button', 'btn btn-ghost', 'Close');
    cancel.type = 'button';
    cancel.onclick = function () { backdrop.remove(); };
    var save = el('button', 'btn btn-primary', editing ? 'Save changes' : 'Report bug');
    save.type = 'submit';
    buttons.appendChild(cancel); buttons.appendChild(save);
    form.appendChild(buttons);
    form.onsubmit = function (event) {
      event.preventDefault();
      var payload = {
        title: title.value, description: description.value,
        status: statusSelect.value, priority: prioritySelect.value,
        org_id: clientSelect.value || null, assignee_id: assigneeSelect.value || null,
        page: page.value
      };
      save.disabled = true;
      api(editing ? 'PUT' : 'POST',
          '/api/platform/bugs' + (editing ? '/' + bug.id : ''), payload)
        .then(function (d) {
          backdrop.remove();
          toast(editing ? 'Bug updated' : 'Bug reported', true);
          if (options.onSaved) options.onSaved(d.bug);
          if (!editing && d.bug && options.openAfterSave) {
            openBugDetail(d.bug.id, options);
          }
        }).catch(function (e) {
          save.disabled = false;
          error.textContent = e.message;
        });
    };
    modal.appendChild(form);


    if (editing) {
      var timeline = el('div', 'bug-timeline');
      timeline.appendChild(el('h4', null, 'Activity'));
      if (!events.length) timeline.appendChild(el('p', 'muted', 'No activity yet.'));
      events.forEach(function (ev) {
        var line = el('div', 'bug-event ' + (ev.action === 'commented' ? 'is-comment' : ''));
        line.innerHTML = '<div class="bug-event-head"><strong>' +
          esc(ev.actor_name || ev.actor_email || 'Agency') + '</strong>' +
          '<span class="muted">' + esc(shortTime(ev.created_at)) + '</span></div>' +
          '<div>' + esc(bugEventText(ev)) + '</div>';
        timeline.appendChild(line);
      });
      var comment = el('textarea');
      comment.placeholder = 'Add a comment for the agency';
      var addComment = el('button', 'btn btn-ghost', 'Add comment');
      addComment.type = 'button';
      addComment.onclick = function () {
        if (!comment.value.trim()) return;
        addComment.disabled = true;
        api('POST', '/api/platform/bugs/' + bug.id + '/comments',
            { comment: comment.value }).then(function () {
          backdrop.remove();
          if (options.onSaved) options.onSaved(bug);
          openBugDetail(bug.id, options);
        }).catch(function (e) {
          addComment.disabled = false;
          error.textContent = e.message;
        });
      };
      timeline.appendChild(comment);
      timeline.appendChild(addComment);
      modal.appendChild(timeline);
    }

    backdrop.appendChild(modal);
    backdrop.onclick = function (e) { if (e.target === backdrop) backdrop.remove(); };
    document.body.appendChild(backdrop);
    title.focus();
  }


  function viewBugTracker() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Bug tracker</h2><p class="muted">Internal issues for the ' +
      'agency. Optional client context never makes a bug visible to that client.</p></div>';
    var actions = el('div', 'page-actions');
    var add = el('button', 'btn btn-primary', '+ Report bug');
    var refresh = el('button', 'btn btn-ghost', 'Refresh');
    actions.appendChild(add);
    actions.appendChild(refresh);
    head.appendChild(actions);
    container.appendChild(head);

    var orgs = [];
    var assignees = [];
    var counts = {};
    var bugs = [];
    var summary = el('div', 'bug-summary');
    var bar = el('div', 'chip-input');
    var search = el('input');
    search.type = 'search';
    search.placeholder = 'Search bugs';
    search.className = 'search-input';
    var status = el('select');
    status.innerHTML = '<option value="">All statuses</option>' +
      '<option value="open">Open</option><option value="in_progress">In progress</option>' +
      '<option value="resolved">Resolved</option><option value="closed">Closed</option>';
    var priority = el('select');
    priority.innerHTML = '<option value="">All priorities</option>' +
      '<option value="urgent">Urgent</option><option value="high">High</option>' +
      '<option value="normal">Normal</option><option value="low">Low</option>';
    var assignee = el('select');
    var client = el('select');
    bar.appendChild(search); bar.appendChild(status); bar.appendChild(priority);
    bar.appendChild(assignee); bar.appendChild(client);
    container.appendChild(summary);
    container.appendChild(bar);
    var body = el('div', 'mt');
    container.appendChild(body);

    function drawPeopleFilters() {
      var selectedAssignee = assignee.value;
      var selectedClient = client.value;
      assignee.innerHTML = '<option value="">All assignees</option>' +
        '<option value="unassigned">Unassigned</option>' +
        assignees.map(function (person) {
          return '<option value="' + esc(String(person.id)) + '">' +
            esc(person.name || person.email) + '</option>';
        }).join('');
      client.innerHTML = '<option value="">All clients</option>' +
        orgs.map(function (org) {
          return '<option value="' + esc(String(org.id)) + '">' + esc(org.name) + '</option>';
        }).join('');
      assignee.value = selectedAssignee;
      client.value = selectedClient;
    }

    function drawSummary() {
      summary.innerHTML = '';
      [['Open', 'open'], ['In progress', 'in_progress'], ['Resolved', 'resolved'],
       ['Total', 'all']].forEach(function (pair) {
        var chip = el('div', 'bug-count');
        chip.innerHTML = '<strong>' + esc(String(counts[pair[1]] || 0)) + '</strong>' +
          '<span>' + pair[0] + '</span>';
        summary.appendChild(chip);
      });
    }

    function draw() {
      body.innerHTML = '';
      if (!bugs.length) {
        body.innerHTML = '<div class="panel-card empty-state"><div class="big">' +
          icon('alert') + '</div><strong>No bugs match this view.</strong>' +
          '<p>Report the first issue, or clear a filter.</p></div>';
        return;
      }
      var table = el('table', 'table bug-table');
      table.innerHTML = '<thead><tr><th>Bug</th><th>Priority</th><th>Status</th>' +
        '<th>Client</th><th>Assignee</th><th>Updated</th></tr></thead>';
      var tbody = el('tbody');
      bugs.forEach(function (bug) {
        var tr = el('tr');
        tr.className = 'bug-row';
        tr.title = 'Open bug #' + bug.id;
        tr.innerHTML = '<td><strong>#' + esc(String(bug.id)) + ' ' + esc(bug.title) +
          '</strong><div class="muted">' + esc(bug.reporter_name || 'Agency') +
          (bug.page ? ' · ' + esc(bug.page) : '') + '</div></td>' +
          '<td>' + bugPriorityBadge(bug.priority) + '</td>' +
          '<td>' + bugStatusBadge(bug.status) + '</td>' +
          '<td class="muted">' + esc(bug.org_name || '—') + '</td>' +
          '<td class="muted">' + esc(bug.assignee_name || 'Unassigned') + '</td>' +
          '<td class="muted">' + esc(shortTime(bug.updated_at)) + '</td>';
        tr.onclick = function () {
          openBugDetail(bug.id, { orgs: orgs, assignees: assignees, onSaved: load });
        };
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      body.appendChild(table);
    }

    function load() {
      var query = [];
      if (search.value.trim()) query.push('q=' + encodeURIComponent(search.value.trim()));
      if (status.value) query.push('status=' + encodeURIComponent(status.value));
      if (priority.value) query.push('priority=' + encodeURIComponent(priority.value));
      if (assignee.value) query.push('assignee=' + encodeURIComponent(assignee.value));
      if (client.value) query.push('org_id=' + encodeURIComponent(client.value));
      api('GET', '/api/platform/bugs' + (query.length ? '?' + query.join('&') : ''))
        .then(function (d) {
          bugs = d.bugs || [];
          counts = d.counts || {};
          assignees = d.assignees || [];
          drawPeopleFilters();
          drawSummary();
          draw();
        }).catch(function (e) { toast(e.message, false); });
    }

    function newBug() {
      openBugModal(null, [], {
        orgs: orgs, assignees: assignees, onSaved: load, openAfterSave: true
      });
    }

    add.onclick = newBug;
    refresh.onclick = load;
    search.oninput = load;
    [status, priority, assignee, client].forEach(function (node) {
      node.onchange = load;
    });
    api('GET', '/api/orgs').then(function (d) {
      orgs = d.orgs || [];
      drawPeopleFilters();
      load();
    }).catch(function (e) { toast(e.message, false); });
    return container;
  }

  // ---------- Platform: email and invitations ----------
  function viewPlatformEmail() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Email &amp; invites</h2><p>The platform sends its own ' +
      'invitations through GoHighLevel. Clients keep their own SMTP.</p></div>';
    var inviteBtn = el('button', 'btn btn-primary', '+ Invite someone');
    head.appendChild(inviteBtn);
    container.appendChild(head);

    var connection = el('div', 'form-card');
    var senderBox = el('div', 'mt');
    var templateBox = el('div', 'mt');
    var invitesBox = el('div', 'mt');
    container.appendChild(connection);
    container.appendChild(senderBox);
    container.appendChild(templateBox);
    container.appendChild(invitesBox);

    var state = { senders: [], fields: [] };

    inviteBtn.onclick = function () { inviteForm(); };

    function linkModal(title, message, link, failed) {
      var backdrop = el('div', 'modal-backdrop');
      var modal = el('div', 'modal');
      modal.appendChild(el('h3', null, title));
      modal.appendChild(el('p', failed ? 'form-error' : 'muted', message));
      if (link) {
        // The database only keeps a hash, so this is the only moment the link exists.
        var input = el('input');
        input.value = link;
        input.readOnly = true;
        modal.appendChild(input);
        var copy = el('button', 'btn btn-primary', 'Copy link');
        copy.onclick = function () { copyText(link, copy); };
        modal.appendChild(copy);
      }
      var close = el('button', 'btn btn-ghost', 'Close');
      close.onclick = function () { document.body.removeChild(backdrop); };
      modal.appendChild(close);
      backdrop.appendChild(modal);
      backdrop.onclick = function (e) { if (e.target === backdrop) document.body.removeChild(backdrop); };
      document.body.appendChild(backdrop);
    }

    function inviteForm() {
      api('GET', '/api/orgs').then(function (d) {
        invitePersonModal(true, d.orgs || [], null, load);
      }).catch(function (e) { toast(e.message, false); });
    }

    function renderConnection(d) {
      connection.innerHTML = '';
      connection.appendChild(el('h3', null, 'GoHighLevel connection'));
      connection.appendChild(el('p', 'muted', d.configured
        ? 'Credentials saved. GHL only sends from an address verified in the sub-account.'
        : 'No API token yet - add one in Platform settings, group "Platform email".'));
      if (d.error) connection.appendChild(el('p', 'form-error', d.error));
      var row = el('div', 'row-actions');
      var test = el('button', 'btn btn-ghost', 'Test connection');
      test.onclick = function () {
        test.disabled = true;
        api('POST', '/api/platform/email/test', {}).then(function (r) {
          toast(r.output, r.ok);
        }).catch(function (e) { toast(e.message, false); }).finally(function () { test.disabled = false; });
      };
      var sendTest = el('button', 'btn btn-ghost', 'Send test email');
      sendTest.onclick = function () {
        sendTest.disabled = true;
        api('POST', '/api/platform/email/send-test', {}).then(function () {
          toast('Test email sent to your account address', true);
        }).catch(function (e) { toast(e.message, false); }).finally(function () { sendTest.disabled = false; });
      };
      var settingsLink = el('a', 'btn btn-ghost', 'Credentials & template');
      settingsLink.href = '#/platform/settings';
      row.appendChild(test);
      row.appendChild(sendTest);
      row.appendChild(settingsLink);
      connection.appendChild(row);
    }

    function senderForm(s) {
      var fields = [
        { name: 'name', label: 'Label', value: s ? s.name : 'AscendPlatform' },
        { name: 'from_name', label: 'From name', value: s ? s.from_name : '' },
        { name: 'from_email', label: 'From address', value: s ? s.from_email : '',
          placeholder: 'hello@yourdomain.com',
          help: 'Must be verified in GoHighLevel - use "Send test email" to prove it.' },
        { name: 'reply_to', label: 'Reply-to (optional)', value: s ? s.reply_to : '' }
      ];
      // These are the fields already in the GHL location: nothing here invents schema,
      // it only maps our values onto theirs.
      var stored = (s && s.custom_fields) || {};
      state.fields.forEach(function (field) {
        var key = field.fieldKey || field.id;
        fields.push({
          name: 'cf:' + key,
          label: field.name + ' (GHL custom field)',
          value: stored[key] || stored[field.id] || '',
          help: field.dataType
        });
      });
      formModal(s ? 'Edit sender' : 'New sender', fields, function (data, fail, ok) {
        var payload = {
          name: data.name,
          from_name: data.from_name,
          from_email: data.from_email,
          reply_to: data.reply_to,
          signature: s ? s.signature : '',
          logo_url: s ? s.logo_url : '',
          custom_fields: {},
          is_default: s ? s.is_default : state.senders.length === 0
        };
        if (s) payload.id = s.id;
        Object.keys(data).forEach(function (key) {
          if (key.indexOf('cf:') === 0) payload.custom_fields[key.slice(3)] = data[key];
        });
        api('POST', '/api/platform/email/senders', payload).then(function () {
          ok();
          toast('Sender saved', true);
          load();
        }).catch(function (e) { fail(e.message); });
      });
    }

    function renderSenders() {
      senderBox.innerHTML = '';
      var heading = el('div', 'page-head');
      heading.innerHTML = '<div><h3>Sender</h3><p class="muted">Who invitations come ' +
        'from: a label, a verified from-address, and the GHL custom fields to stamp on ' +
        'the contacts it creates.</p></div>';
      var add = el('button', 'btn btn-ghost', '+ Add sender');
      add.onclick = function () { senderForm(null); };
      heading.appendChild(add);
      senderBox.appendChild(heading);

      if (!state.senders.length) {
        senderBox.appendChild(el('p', 'muted',
          'No sender yet - invitations need a from-address, so add one.'));
        return;
      }
      state.senders.forEach(function (sender) {
        var card = el('div', 'panel-card');
        card.innerHTML = '<strong>' + esc(sender.name || sender.from_email) + '</strong>' +
          (sender.is_default ? ' <span class="badge badge-admin">default</span>' : '') +
          '<div class="muted">' +
          esc(sender.from_name ? sender.from_name + ' <' + sender.from_email + '>'
                               : sender.from_email) + '</div>' +
          (sender.reply_to ? '<div class="muted">reply-to ' + esc(sender.reply_to) + '</div>' : '');
        var row = el('div', 'row-actions');
        var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
        edit.onclick = function () { senderForm(sender); };
        row.appendChild(edit);
        if (!sender.is_default) {
          var makeDefault = el('button', 'btn btn-ghost btn-sm', 'Make default');
          makeDefault.onclick = function () {
            var payload = {
              id: sender.id,
              name: sender.name,
              from_name: sender.from_name,
              from_email: sender.from_email,
              reply_to: sender.reply_to,
              signature: sender.signature,
              logo_url: sender.logo_url,
              custom_fields: sender.custom_fields,
              is_default: true
            };
            api('POST', '/api/platform/email/senders', payload).then(load)
              .catch(function (e) { toast(e.message, false); });
          };
          row.appendChild(makeDefault);
        }
        card.appendChild(row);
        senderBox.appendChild(card);
      });
    }

    function renderInvites(list) {
      invitesBox.innerHTML = '';
      var heading = el('div', 'page-head');
      heading.innerHTML = '<div><h3>Invitations</h3><p class="muted">Each link is ' +
        'emailed once and works once. Resending retires the previous link.</p></div>';
      invitesBox.appendChild(heading);
      if (!list.length) {
        invitesBox.appendChild(el('p', 'muted', 'No invitations yet.'));
        return;
      }
      var table = el('table', 'table');
      table.innerHTML = '<thead><tr><th>Email</th><th>Role</th><th>Status</th>' +
        '<th>Expires</th><th></th></tr></thead>';
      var tbody = el('tbody');
      list.forEach(function (invite) {
        var tr = el('tr');
        tr.innerHTML = '<td>' + esc(invite.email) +
          (invite.name ? '<div class="muted">' + esc(invite.name) + '</div>' : '') + '</td>' +
          '<td>' + personRoleBadge(invite.role) + '</td>' +
          '<td><span class="badge">' + esc(invite.status) + '</span></td>' +
          '<td class="muted">' + esc((invite.expires_at || '').slice(0, 10)) + '</td>';
        var td = el('td', 'row-actions');
        if (invite.status !== 'accepted') {
          var resend = el('button', 'btn btn-ghost btn-sm', 'Resend');
          resend.onclick = function () {
            resend.disabled = true;
            api('POST', '/api/invites/' + invite.id + '/resend', {}).then(function (res) {
              linkModal('New link', 'Emailed to ' + res.invite.email + '.', res.link, false);
              load();
            }).catch(function (e) {
              var body = e.data || {};
              if (body.link) {
                linkModal('Email not sent', e.message, body.link, true);
                load();
              } else {
                toast(e.message, false);
                resend.disabled = false;
              }
            });
          };
          td.appendChild(resend);
          if (invite.status === 'pending') {
            var revoke = el('button', 'btn btn-danger btn-sm', 'Revoke');
            revoke.onclick = function () {
              api('POST', '/api/invites/' + invite.id + '/revoke', {}).then(load)
                .catch(function (e) { toast(e.message, false); });
            };
            td.appendChild(revoke);
          }
        } else {
          td.appendChild(el('span', 'muted', 'accepted ' + (invite.accepted_at || '').slice(0, 10)));
        }
        tr.appendChild(td);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      invitesBox.appendChild(table);
    }

    function load() {
      api('GET', '/api/platform/email').then(function (d) {
        state.senders = d.senders || [];
        state.fields = d.custom_fields || [];
        renderConnection(d);
        renderSenders();
        templateBox.innerHTML = '';
        templateBox.appendChild(templateCard(d, load));
        renderInvites(d.invites || []);
      }).catch(function (e) { toast(e.message, false); });
    }

    load();
    return container;
  }

  // Shared source editor for the operator's Platform page and the client's Analytics
  // page. A client connects with Google OAuth; the service-account path is a fallback
  // for agency-managed sources, so nobody is asked for a server file path.
  function analyticsSourceForm(source, kinds, orgId, operator, onSaved, ga4Email,
                               oauthReady) {
    var isEdit = !!(source && source.id);
    var fields = [];
    var sourceMode = source && source.auth_mode ? source.auth_mode : '';
    var serviceAvailable = !!ga4Email;
    var oauthAvailable = !!oauthReady || sourceMode === 'oauth';
    var method = 'service';
    if (!operator) {
      if (sourceMode === 'oauth') method = 'oauth';
      else if (sourceMode === 'service_account' && source) method = 'service';
      else if (oauthAvailable) method = 'oauth';
      else if (!serviceAvailable) method = 'oauth';
    }
    if (operator) {
      fields.push({
        name: 'kind', label: 'Source', type: 'select',
        options: (kinds || []).map(function (k) { return k.kind; }),
        value: source ? source.kind : ((kinds && kinds[0]) ? kinds[0].kind : 'ga4')
      });
    } else if (oauthAvailable && (serviceAvailable || sourceMode === 'service_account')) {
      fields.push({
        name: 'auth_method', label: 'Connection method', type: 'select',
        options: [
          { value: 'oauth', label: 'Connect with Google (recommended)' },
          { value: 'service', label: 'Use agency service account' }
        ],
        value: method,
        help: 'Google sign-in grants read-only access to this client’s GA4 property.'
      });
    }
    fields.push({
      name: 'external_id',
      label: operator ? 'Account / property / location ID' : 'Google Analytics property ID',
      value: source ? source.external_id : '',
      placeholder: operator ? 'Location, property or ad account ID' : 'e.g. 123456789',
      help: operator
        ? 'GHL: location id. GA4: properties/123456789. Meta: act_1234567890.'
        : (oauthReady
            ? 'Find this in Google Analytics under Admin → Property settings, then connect with Google.'
            : (ga4Email
                ? 'OAuth is not configured. The agency can connect this property with its service account: ' + ga4Email
                : 'Ask your agency to configure Google Analytics OAuth or a service account.'))
    });
    fields.push({
      name: 'label',
      label: operator ? 'Label (optional)' : 'Name this connection (optional)',
      value: source ? source.label : '',
      placeholder: 'e.g. Main website'
    });
    if (operator) {
      fields.push({
        name: 'credential_ref', label: 'Credential reference (advanced)',
        value: source ? source.credential_ref : '',
        help: 'Optional path to a mounted key file. Leave blank to use the platform key.'
      });
    }
    formModal(isEdit ? 'Edit analytics source' : 'Connect analytics', fields,
      function (data, fail, ok) {
        var kind = operator ? data.kind : (source ? source.kind : 'ga4');
        var external_id = String(data.external_id || '').trim();
        // The GA4 API addresses a property as "properties/<id>". A client enters the
        // numeric ID from GA4 Admin; the operator can type the full resource name.
        if (!operator && kind === 'ga4' && external_id &&
            external_id.indexOf('properties/') !== 0) {
          external_id = 'properties/' + external_id.replace(/^\/+/, '');
        }
        var selectedMethod = operator ? 'service' : (data.auth_method || method);

        if (!operator && kind === 'ga4' && selectedMethod === 'oauth') {
          var start = {
            external_id: external_id,
            label: data.label
          };
          if (source && source.id) start.source_id = source.id;
          api('POST', '/api/analytics/oauth/google/start', start).then(function (r) {
            ok();
            window.location.assign(r.auth_url);
          }).catch(function (e) { fail(e.message); });
          return;
        }

        var payload = {
          kind: kind,
          external_id: external_id,
          label: data.label
        };
        if (operator) {
          payload.credential_ref = data.credential_ref || '';
        } else if (source && source.auth_mode === 'oauth') {
          // Choosing the service-account route is an explicit switch away from OAuth.
          payload.credential_ref = '';
        }
        if (source && source.id) payload.id = source.id;
        if (operator && orgId) payload.org_id = Number(orgId);
        api('POST', '/api/data-sources', payload).then(function () {
          ok();
          toast('Analytics connection saved', true);
          if (onSaved) onSaved();
        }).catch(function (e) { fail(e.message); });
      }, operator ? 'Save' : (isEdit ? 'Reconnect' : 'Connect'));
  }

  // Start a Google consent for a client or the agency grant. Read scopes first; the
  // write upgrade is a separate, explicit action when the user asks for setup/automation.
  function startGoogleAnalytics(scope, write, bigquery, autoSetup) {
    var payload = { connection_scope: scope, write: !!write,
                    bigquery: !!bigquery, auto_setup: !!autoSetup };
    if (scope === 'client') {
      var workspace = isOwner(state.user) ? state.user.acting_org
        : (state.user.org && state.user.org.id);
      if (!workspace) { toast('Choose a client first', false); return; }
      payload.org_id = workspace;
    }
    api('POST', '/api/analytics/oauth/google/start', payload).then(function (r) {
      window.location.assign(r.auth_url);
    }).catch(function (e) { toast(e.message, false); });
  }

  // After Google is connected, let the user pick the GA4 property and an optional GTM
  // web container. The property list is only properties this grant can actually see.
  function chooseGoogleProperty(scope, onDone) {
    function properties() {
      return api('GET', '/api/analytics/google/properties?connection_scope=' + scope);
    }
    function containers() {
      return api('GET', '/api/analytics/google/containers?connection_scope=' + scope)
        .catch(function () { return { containers: [] }; });
    }
    Promise.all([properties(), containers()]).then(function (results) {
      var propertyRows = (results[0] || {}).properties || [];
      var containerRows = (results[1] || {}).containers || [];
      if (!propertyRows.length) {
        openGoogleAccountHelp(scope, onDone);
        return;
      }
      var fields = [{
        name: 'property', label: 'GA4 property', type: 'select',
        options: propertyRows.map(function (p) {
          return { value: p.property,
                   label: (p.display_name || p.property) +
                          (p.account_name ? ' — ' + p.account_name : '') };
        })
      }, {
        name: 'container', label: 'Tag Manager container (optional)', type: 'select',
        options: [{ value: '', label: 'No container yet' }].concat(
          containerRows.map(function (c) {
            return { value: c.container_id,
                     label: (c.name || 'Container') +
                            (c.public_id ? ' (' + c.public_id + ')' : '') };
          }))
      }, {
        name: 'label', label: 'Name this connection (optional)', value: ''
      }];
      formModal('Choose a GA4 property', fields, function (data, fail, ok) {
        var property = propertyRows.filter(function (p) {
          return p.property === data.property;
        })[0] || {};
        var container = containerRows.filter(function (c) {
          return c.container_id === data.container;
        })[0] || null;
        var payload = {
          connection_scope: scope,
          external_id: property.property,
          label: data.label,
          account_name: property.account_name || ''
        };
        if (container) {
          payload.container_id = container.container_id;
          payload.container_account_id = container.account_id;
          payload.container_public_id = container.public_id;
          payload.container_name = container.name;
          payload.container_domain = container.domain_name;
        }
        api('POST', '/api/analytics/google/connect', payload).then(function (result) {
          ok();
          toast('GA4 property connected', true);
          syncAnalyticsSource((result.source || {}).id, onDone);
        }).catch(function (e) { fail(e.message); });
      }, 'Connect property');
    }).catch(function (e) { toast(e.message, false); });
  }

  // Create a GA4 property and, optionally, a GTM container with GA4 config/event tags,
  // publish it, and install the snippet on the site's live pages. One explicit button:
  // the person clicking it is the approval for the whole setup.
  function createGoogleSetup(scope, onDone) {
    Promise.all([
      api('GET', '/api/analytics/google/accounts?connection_scope=' + scope),
      api('GET', '/api/analytics/google/gtm-accounts?connection_scope=' + scope)
        .catch(function () { return { accounts: [] }; }),
      api('GET', '/api/sites?summary=1').catch(function () { return { sites: [] }; })
    ]).then(function (results) {
      var accounts = (results[0] || {}).accounts || [];
      var gtmAccounts = (results[1] || {}).accounts || [];
      var sites = (results[2] || {}).sites || [];
      if (!accounts.length) {
        openGoogleAccountHelp(scope, onDone);
        return;
      }
      var firstSite = sites[0] || null;
      var timezone = 'Etc/UTC';
      try { timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || timezone; }
      catch (e) { /* older browsers */ }
      var fields = [{
        name: 'account', label: 'Google Analytics account', type: 'select',
        options: accounts.map(function (a) {
          return { value: a.account, label: a.display_name || a.account_id };
        })
      }];
      if (sites.length) {
        fields.push({
          name: 'site', label: 'Website to tag', type: 'select',
          options: [{ value: '', label: 'No website yet' }].concat(
            sites.map(function (s) {
              return { value: String(s.id), label: s.domain };
            })),
          value: firstSite ? String(firstSite.id) : '',
          help: 'The snippet is installed now and on every future publish.'
        });
      }
      fields.push({
        name: 'property_name', label: 'New GA4 property name',
        value: firstSite ? firstSite.domain : ''
      });
      fields.push({ name: 'time_zone', label: 'Time zone', value: timezone });
      fields.push({ name: 'currency_code', label: 'Currency code', value: 'USD' });
      fields.push({
        name: 'key_events', label: 'Key events', value: 'form_submit',
        help: 'Comma separated. Each one becomes a GA4 key event and a GTM event tag; ' +
              'the tag fires when the page pushes that event to dataLayer.'
      });
      fields.push({
        name: 'audience_event', label: 'Create an audience for', value: 'form_submit',
        help: 'Optional. Leave blank to skip the audience.'
      });
      if (gtmAccounts.length) {
        fields.push({
          name: 'make_container', label: 'Tag Manager', type: 'select',
          options: [
            { value: 'yes', label: 'Create and publish a new container' },
            { value: 'no', label: 'Create the property only' }
          ], value: 'yes'
        });
        fields.push({
          name: 'gtm_account_id', label: 'Tag Manager account', type: 'select',
          options: gtmAccounts.map(function (a) {
            return { value: a.account_id, label: a.display_name || a.account_id };
          })
        });
        fields.push({
          name: 'container_name', label: 'Container name',
          value: firstSite ? firstSite.domain : 'Website'
        });
      }
      formModal('Set up Google Analytics', fields, function (data, fail, ok) {
        var site = sites.filter(function (s) {
          return String(s.id) === String(data.site);
        })[0] || null;
        var account = accounts.filter(function (a) {
          return a.account === data.account;
        })[0] || {};
        var makeContainer = data.make_container === 'yes' && !!gtmAccounts.length;
        var payload = {
          connection_scope: scope,
          site_id: site ? site.id : null,
          account: data.account,
          account_name: account.display_name || '',
          property_name: data.property_name,
          time_zone: data.time_zone,
          currency_code: data.currency_code,
          key_events: data.key_events,
          audience_event: data.audience_event,
          make_container: makeContainer,
          gtm_account_id: makeContainer ? data.gtm_account_id : '',
          container_name: makeContainer ? data.container_name : ''
        };
        api('POST', '/api/analytics/google/setup', payload).then(function (result) {
          ok();
          var tag = result.tag || {};
          if (result.errors && result.errors.length) {
            toast('Property created, but ' + result.errors[0], false);
          } else if (tag.dry_run) {
            toast('Property and container created. Tag installation is dry-run; ' +
                  'enable live actions to write the pages.', false);
          } else if (tag.updated) {
            toast('GA4 and Tag Manager are set up; the snippet is on ' + tag.updated +
                  ' page(s).', true);
          } else {
            toast('GA4 and Tag Manager are set up. The snippet will be added on the ' +
                  'next publish.', true);
          }
          syncAnalyticsSource((result.source || {}).id, onDone);
        }).catch(function (e) { fail(e.message); });
      }, 'Create & publish');
    }).catch(function (e) { toast(e.message, false); });
  }


  function analyticsSetupStatus(scope, discover) {
    return api('GET', '/api/analytics/setup/status?connection_scope=' +
      encodeURIComponent(scope) + (discover ? '&discover=1' : ''));
  }

  // After a property is connected or created, pull its first reports without making the
  // person hunt for Refresh. A brand-new GA4 property may not have report rows yet; that
  // is a normal wait, not a setup failure.
  function syncAnalyticsSource(sourceId, onDone) {
    if (!sourceId) {
      if (onDone) onDone();
      return;
    }
    toast('Setup complete. Syncing the first Analytics reports…', true);
    api('POST', '/api/data-sources/' + sourceId + '/sync', {})
      .then(function (result) {
        if (result.rows) toast('Analytics synced: ' + result.rows + ' rows', true);
      })
      .catch(function () {
        toast('Setup is complete. The first GA4 reports will appear as Google ' +
              'collects data.', true);
      })
      .then(function () { if (onDone) onDone(); });
  }

  function setupModalShell(title, wide) {
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal' + (wide ? ' modal-wide' : ''));
    var close = el('button', 'btn btn-ghost btn-sm', '\u00d7');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close');
    close.onclick = function () { backdrop.remove(); };
    modal.appendChild(close);
    modal.appendChild(el('h3', null, title));
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    return { backdrop: backdrop, modal: modal, close: close };
  }

  // The OAuth grant says the platform may call Google; this modal is where the person
  // accepts that setup is recurring and that Google's terms, privacy and costs stay theirs.
  function openGoogleSetupConsent(scope, onDone) {
    analyticsSetupStatus(scope).then(function (status) {
      var consent = status.consent || {};
      var shell = setupModalShell('Grant full setup automation', true);
      var intro = el('p', 'muted',
        'This lets AscendPlatform finish Analytics setup now and keep it working later. ' +
        'It does not replace Google\'s own terms.');
      shell.modal.appendChild(intro);
      var list = el('div', 'consent-terms');
      (consent.disclaimer || []).forEach(function (line) {
        list.appendChild(el('p', null, line));
      });
      shell.modal.appendChild(list);
      var label = el('label', 'consent-check');
      var check = el('input');
      check.type = 'checkbox';
      label.appendChild(check);
      label.appendChild(document.createTextNode(
        ' I have read and accept these terms for this workspace.'));
      shell.modal.appendChild(label);
      var error = el('div', 'form-error');
      shell.modal.appendChild(error);
      var actions = el('div', 'modal-actions');
      var cancel = el('button', 'btn btn-ghost', 'Cancel');
      cancel.type = 'button';
      cancel.onclick = function () { shell.backdrop.remove(); };
      var agree = el('button', 'btn btn-primary', 'I agree — grant access');
      agree.type = 'button';
      agree.onclick = function () {
        if (!check.checked) {
          error.textContent = 'Tick the box to confirm you have read the terms.';
          return;
        }
        agree.disabled = true;
        api('POST', '/api/analytics/setup/consent', {
          accepted: true, version: consent.version, connection_scope: scope
        }).then(function (result) {
          shell.backdrop.remove();
          toast('Full setup automation granted', true);
          if (!result.write_ready) {
            toast('Now let Google grant setup access.', true);
            startGoogleAnalytics(scope, true, true, true);
          } else if (onDone) {
            onDone();
          }
        }).catch(function (e) {
          agree.disabled = false;
          error.textContent = e.message;
        });
      };
      actions.appendChild(cancel);
      actions.appendChild(agree);
      shell.modal.appendChild(actions);
    }).catch(function (e) { toast(e.message, false); });
  }

  function revokeGoogleSetup(scope, onDone) {
    if (!confirm('Stop future Analytics setup automation? Resources already created ' +
                 'and data already collected stay in place.')) return;
    api('POST', '/api/analytics/setup/revoke', { connection_scope: scope })
      .then(function () {
        toast('Future setup automation is off', true);
        if (onDone) onDone();
      }).catch(function (e) { toast(e.message, false); });
  }

  // No Analytics account is a normal first-run state, not an error. Google requires the
  // person to accept its terms, so the API creates a ticket and this modal sends them to
  // Google's form; "I created it" is the fallback if Google changes that browser route.
  function openGoogleAccountHelp(scope, onDone, autoSetup) {
    var shell = setupModalShell('Create a Google Analytics account', true);
    var body = el('div');
    body.innerHTML = '<p>A GA4 property has to belong to an Analytics account. None is ' +
      'visible to this Google connection yet.</p>' +
      '<p class="muted">Create one with Google below. Google will show its own terms; ' +
      'accepting them is yours. Then come back here and run setup again.</p>';
    shell.modal.appendChild(body);
    var nameWrap = el('label', 'field');
    nameWrap.appendChild(el('span', null, 'Account name (shown in Google Analytics)'));
    var name = el('input');
    name.type = 'text';
    name.value = 'AscendPlatform Analytics';
    nameWrap.appendChild(name);
    shell.modal.appendChild(nameWrap);
    var error = el('div', 'form-error');
    shell.modal.appendChild(error);
    var actions = el('div', 'modal-actions');
    var refresh = el('button', 'btn btn-ghost', 'I created it — refresh');
    refresh.type = 'button';
    refresh.onclick = function () {
      shell.backdrop.remove();
      if (onDone) onDone();
    };
    var open = el('button', 'btn btn-ghost', 'Open Google Analytics');
    open.type = 'button';
    open.onclick = function () {
      window.open('https://analytics.google.com/analytics/web/', '_blank', 'noopener');
    };
    var create = el('button', 'btn btn-primary', 'Create account with Google');
    create.type = 'button';
    create.onclick = function () {
      create.disabled = true;
      error.textContent = '';
      api('POST', '/api/analytics/google/account-ticket', {
        connection_scope: scope, display_name: name.value,
        auto_setup: !!autoSetup
      }).then(function (result) {
        // The ticket page is the only place Google accepts its Analytics terms.
        window.location.assign(result.tos_url || result.fallback_url);
      }).catch(function (e) {
        create.disabled = false;
        error.textContent = e.message;
        if (e.status === 403) {
          var consent = el('button', 'btn btn-ghost btn-sm', 'Grant setup consent');
          consent.type = 'button';
          consent.onclick = function () {
            shell.backdrop.remove();
            openGoogleSetupConsent(scope, onDone);
          };
          error.appendChild(document.createTextNode(' '));
          error.appendChild(consent);
        }
      });
    };
    var reconnect = el('button', 'btn btn-ghost', 'Reconnect Google');
    reconnect.type = 'button';
    reconnect.onclick = function () {
      shell.backdrop.remove();
      startGoogleAnalytics(scope, true, true, true);
    };
    actions.appendChild(refresh);
    actions.appendChild(open);
    actions.appendChild(reconnect);
    actions.appendChild(create);
    shell.modal.appendChild(actions);
    shell.modal.appendChild(el('p', 'fine-print',
      'If Google shows an error or the account form does not open, use Open Google ' +
      'Analytics, create the account there, then click I created it — refresh.'));
  }

  // Google exposes no API that creates a Tag Manager account. Be explicit rather than
  // promising automation Google does not allow.
  function openGoogleTagManagerHelp(onDone) {
    var shell = setupModalShell('Create a Tag Manager account', false);
    shell.modal.appendChild(el('p', null,
      'GA4 is ready. Google does not let apps create Tag Manager accounts, so create ' +
      'one in Tag Manager, then run Set up everything again to create and publish the ' +
      'container.'));
    shell.modal.appendChild(el('p', 'muted',
      'The GA4 property and any BigQuery link are already saved; setup will reuse them.'));
    var actions = el('div', 'modal-actions');
    var open = el('button', 'btn btn-primary', 'Open Tag Manager');
    open.type = 'button';
    open.onclick = function () {
      window.open('https://tagmanager.google.com/', '_blank', 'noopener');
    };
    var done = el('button', 'btn btn-ghost', 'I created it — refresh');
    done.type = 'button';
    done.onclick = function () {
      shell.backdrop.remove();
      if (onDone) onDone();
    };
    actions.appendChild(done);
    actions.appendChild(open);
    shell.modal.appendChild(actions);
  }

  function startGoogleSetup(scope, onDone) {
    analyticsSetupStatus(scope).then(function (status) {
      var consent = status.consent || {};
      if (!consent.accepted) {
        openGoogleSetupConsent(scope, function () { startGoogleSetup(scope, onDone); });
        return;
      }
      if (!status.write_ready) {
        toast('Let Google grant setup access first.', true);
        startGoogleAnalytics(scope, true, true, true);
        return;
      }
      if (!status.live_actions) {
        toast('Live actions are off. An operator must enable ALLOW_LIVE_ACTIONS.',
              false);
        return;
      }
      Promise.all([
        api('GET', '/api/analytics/google/accounts?connection_scope=' + scope),
        api('GET', '/api/analytics/google/gtm-accounts?connection_scope=' + scope)
          .catch(function () { return { accounts: [] }; }),
        api('GET', '/api/sites?summary=1').catch(function () { return { sites: [] }; })
      ]).then(function (results) {
        var accounts = (results[0] || {}).accounts || [];
        var gtmAccounts = (results[1] || {}).accounts || [];
        var sites = (results[2] || {}).sites || [];
        if (!accounts.length) {
          openGoogleAccountHelp(scope, onDone, true);
          return;
        }
        var site = sites[0] || null;
        var timezone = 'Etc/UTC';
        try { timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || timezone; }
        catch (e) { /* older browsers */ }
        var makeContainer = gtmAccounts.length > 0;
        var account = accounts[0];
        var gtm = gtmAccounts[0] || {};
        var payload = {
          connection_scope: scope,
          site_id: site ? site.id : null,
          account: account.account,
          account_name: account.display_name || '',
          property_name: site ? site.domain : 'Website',
          time_zone: timezone,
          currency_code: 'USD',
          key_events: 'form_submit',
          audience_event: 'form_submit',
          make_container: makeContainer,
          gtm_account_id: makeContainer ? gtm.account_id : '',
          container_name: site ? site.domain : 'Website'
        };
        api('POST', '/api/analytics/setup/bootstrap', payload).then(function (result) {
          var needs = result.needs || [];
          if (needs.indexOf('ga4_account') >= 0) {
            openGoogleAccountHelp(scope, onDone, true);
            return;
          }
          if (needs.indexOf('gtm_account') >= 0) {
            openGoogleTagManagerHelp(onDone);
            return;
          }
          if (result.errors && result.errors.length) {
            toast('Setup finished with a note: ' + result.errors[0], false);
          } else if ((result.tag || {}).updated) {
            toast('Google Analytics and Tag Manager are ready; the snippet is on ' +
                  result.tag.updated + ' page(s).', true);
          } else {
            toast(result.mode === 'existing'
              ? 'Google Analytics setup is already complete.'
              : 'Google Analytics and Tag Manager are ready.', true);
          }
          syncAnalyticsSource((result.source || {}).id, onDone);
        }).catch(function (e) {
          if (e.data && (e.data.needs || []).indexOf('ga4_account') >= 0) {
            openGoogleAccountHelp(scope, onDone, true);
            return;
          }
          toast(e.message, false);
        });
      }).catch(function (e) { toast(e.message, false); });
    }).catch(function (e) { toast(e.message, false); });
  }

  // Re-run tag installation for a client that already has a container selected.
  function installGoogleTag(onDone) {
    api('GET', '/api/sites?summary=1').then(function (d) {
      var sites = d.sites || [];
      if (!sites.length) {
        toast('Add a domain in the Website tab first - the snippet needs a site.', false);
        return;
      }
      formModal('Install Tag Manager snippet', [{
        name: 'site', label: 'Website', type: 'select',
        options: sites.map(function (s) {
          return { value: String(s.id), label: s.domain };
        }),
        help: 'Rewrites the live pages in place. Publishing later also adds the snippet.'
      }], function (data, fail, ok) {
        var site = sites.filter(function (s) {
          return String(s.id) === String(data.site);
        })[0] || sites[0];
        api('POST', '/api/analytics/google/tag/install', { site_id: site.id })
          .then(function (result) {
            ok();
            if (result.dry_run) {
              toast('Dry-run: no pages changed. Enable live actions first.', false);
            } else if (result.errors && result.errors.length) {
              toast('Installed with issues: ' + result.errors[0], false);
            } else if (result.updated) {
              toast('Tag installed on ' + result.updated + ' page(s).', true);
            } else {
              toast('No published pages needed changing; the snippet will be added on ' +
                    'the next publish.', true);
            }
            if (onDone) onDone();
          }).catch(function (e) { fail(e.message); });
      }, 'Install snippet');
    }).catch(function (e) { toast(e.message, false); });
  }

  // ---------- Platform: data sources ----------
  // A page of its own rather than a card inside viewClient: sources are per-org data
  // with their own sync state, and this keeps the client detail view untouched.
  function viewDataSources() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Data sources</h2><p>Where each client&apos;s numbers ' +
      'come from. Site logs are ours; GA4 and Meta belong to the client.</p></div>';
    container.appendChild(head);

    var picker = el('div', 'form-card');
    var body = el('div', 'mt');
    container.appendChild(picker);
    container.appendChild(body);

    var state = { orgs: [], org: '' };

    function loadOrgs() {
      return api('GET', '/api/orgs').then(function (d) {
        state.orgs = d.orgs || [];
        picker.innerHTML = '';
        picker.appendChild(el('h3', null, 'Client'));
        var select = el('select');
        select.innerHTML = state.orgs.map(function (org) {
          return '<option value="' + org.id + '">' + esc(org.name) + '</option>';
        }).join('');
        if (state.org) select.value = String(state.org);
        select.onchange = function () { state.org = select.value; loadSources(); };
        picker.appendChild(select);
        if (!state.org && state.orgs.length) state.org = String(state.orgs[0].id);
        var row = el('div', 'row-actions');
        var sync = el('button', 'btn btn-ghost', 'Sync site logs now');
        sync.onclick = function () {
          sync.disabled = true;
          api('POST', '/api/data-sources/sync', {}).then(function (r) {
            toast(r.ok ? 'Synced ' + r.rows + ' rows across ' + r.orgs + ' client(s)'
                       : r.error, r.ok);
            loadSources();
          }).catch(function (e) { toast(e.message, false); })
            .finally(function () { sync.disabled = false; });
        };
        row.appendChild(sync);
        picker.appendChild(row);
        loadSources();
      });
    }

    function loadSources() {
      api('GET', '/api/data-sources?org_id=' + encodeURIComponent(state.org))
        .then(function (d) {
          body.innerHTML = '';
          var sources = d.sources || [];
          var propertyRows = d.properties || [];
          var add = el('button', 'btn btn-primary', '+ Add source');
          add.onclick = function () {
            analyticsSourceForm(null, d.kinds, state.org, true, loadSources);
          };
          var addAgency = null;
          if (isOwner(state.user) && d.oauth_ready && !d.agency_oauth_connected) {
            addAgency = el('button', 'btn btn-ghost', 'Connect agency Google');
            addAgency.onclick = function () { startGoogleAnalytics('agency'); };
          }
          if (!sources.length) {
            var empty = el('div', 'empty-state');
            empty.innerHTML = '<div class="big">' + icon('trend') + '</div>' +
              '<strong>No analytics sources yet.</strong>' +
              '<p>Connect the client\'s GHL sub-account, their GA4 property, or read our ' +
              'own site logs.</p>';
            empty.appendChild(add);
            if (addAgency) empty.appendChild(addAgency);
            body.appendChild(empty);
            return;
          }
          body.appendChild(add);
          if (addAgency) body.appendChild(addAgency);
          var table = el('table', 'table');
          table.innerHTML = '<thead><tr><th>Kind</th><th>Account</th><th>Status</th>' +
            '<th>Last sync</th><th></th></tr></thead>';
          var tbody = el('tbody');
          sources.forEach(function (source) {
            var tr = el('tr');
            var property = propertyRows.filter(function (row) {
              return String(row.source_id) === String(source.id);
            })[0];
            var problem = source.last_error
              ? '<div class="form-error">' + esc(source.last_error.slice(0, 90)) + '</div>'
              : '';
            tr.innerHTML = '<td><span class="badge">' + esc(source.kind) + '</span>' +
              '<div class="muted">' + esc(source.label) + '</div></td>' +
              '<td class="muted">' +
              esc((property && (property.display_name || source.external_id)) ||
                  source.external_id || '—') + problem + '</td>' +
              '<td>' + (source.enabled ? 'enabled' : 'disabled') + '</td>' +
              '<td class="muted">' + esc((source.last_sync_at || 'never').slice(0, 16)) + '</td>';
            var td = el('td', 'row-actions');
            var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
            edit.onclick = function () {
              analyticsSourceForm(source, d.kinds, state.org, true, loadSources);
            };
            td.appendChild(edit);
            var del = el('button', 'btn btn-danger btn-sm', 'Remove');
            del.onclick = function () {
              api('DELETE', '/api/data-sources/' + source.id).then(loadSources)
                .catch(function (e) { toast(e.message, false); });
            };
            td.appendChild(del);
            tr.appendChild(td);
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          body.appendChild(table);
        }).catch(function (e) { toast(e.message, false); });
    }

    // The shared editor lives above; this page is the operator's cross-client view.
    // Start on the client picked in the sidebar switcher, so this page and the rest of
    // the console agree. The picker stays: syncing and configuring sources is per client
    // and the operator may well want to look at a different one without leaving.
    api('GET', '/api/session/org').then(function (s) {
      if (s && s.acting_org) state.org = String(s.acting_org);
    }).catch(function () { /* fall back to the first client */ })
      .finally(loadOrgs);
    return container;
  }

  // ---------- Analytics: the brief, for humans ----------
  function viewAnalytics(notice) {
    var container = el('div', 'container');
    var owner = isOwner(state.user);
    var canManage = owner || canAdmin(state.user);
    var days = 7;
    var sources = [];
    var sourceKinds = [];
    var ga4Email = '';
    var oauthReady = false;
    var clientOauth = false;
    var agencyOauth = false;
    var clientWriteReady = false;
    var agencyWriteReady = false;
    var clientBigqueryReady = false;
    var agencyBigqueryReady = false;
    var liveReady = true;
    var properties = [];
    var containers = [];
    var reportData = { reports: {} };
    var actionLog = [];
    var insightsData = { insights: [] };
    var alerts = [];
    var alertRules = [];
    var setupStatus = {};
    var autoSetupPending = false;

    function maybeRunAutoSetup(scope) {
      if (!autoSetupPending || !canManage || !setupStatus.connected) return;
      autoSetupPending = false;
      startGoogleSetup(scope, load);
    }
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Analytics</h2><p class="muted">' +
      (owner ? 'Traffic for this client, compared with the previous 7 days.'
             : 'How your website is doing, compared with the previous 7 days.') +
      '</p></div>';
    var refresh = el('button', 'btn btn-ghost', 'Refresh');
    refresh.onclick = function () { load(); };
    head.appendChild(refresh);
    container.appendChild(head);

    // The tenant comes from the session, like every other page: the sidebar switcher is
    // the one control, so this page can never disagree with it.
    var body = el('div');
    container.appendChild(body);

    var SOURCE_LABELS = {
      caddy: 'Website logs', ga4: 'Google Analytics 4',
      ghl: 'GoHighLevel', meta: 'Meta ads'
    };

    function sourceLabel(kind) {
      return SOURCE_LABELS[kind] || String(kind || 'Analytics source');
    }

    function shortNumber(n) {
      n = Number(n || 0);
      return n >= 10000 ? (n / 1000).toFixed(1) + 'k' : String(n);
    }

    function card(label, value, delta) {
      var box = el('div', 'panel-card kpi');
      var d = Number(delta || 0);
      var cls = d === 0 ? 'pill flat' : (d > 0 ? 'pill up' : 'pill down');
      box.innerHTML =
        '<div class="kpi-label">' + esc(label) + '</div>' +
        '<div class="kpi-value">' + esc(shortNumber(value)) + '</div>' +
        '<div class="' + cls + '">' + (d > 0 ? '▲ ' : (d < 0 ? '▼ ' : '')) +
          Math.abs(d) + '%</div>';
      return box;
    }

    function emptyPanel(title, text, action) {
      var panel = el('div', 'panel-card empty-state');
      panel.innerHTML = '<div class="big">' + icon('trend') + '</div>' +
        '<strong>' + esc(title) + '</strong><p>' + esc(text) + '</p>';
      var actions = el('div', 'empty-actions');
      if (action) actions.appendChild(action);
      panel.appendChild(actions);
      return panel;
    }

    function connectButton(label) {
      var button = el('button', 'btn btn-primary btn-sm',
        label || (oauthReady ? 'Connect Google Analytics' : 'Connect analytics'));
      button.onclick = function () {
        if (!oauthReady) {
          analyticsSourceForm(null, sourceKinds, null, owner, load, ga4Email, oauthReady);
          return;
        }
        if (clientOauth || agencyOauth) {
          chooseGoogleProperty(clientOauth ? 'client' : 'agency', load);
        } else {
          startGoogleAnalytics('client');
        }
      };
      return button;
    }

    function syncSource(source, button) {
      if (!source || !source.id) return;
      button.disabled = true;
      api('POST', '/api/data-sources/' + source.id + '/sync', {})
        .then(function (result) {
          toast(result.rows ? 'Refreshed ' + result.rows + ' rows' : 'Analytics refreshed', true);
          load();
        })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { button.disabled = false; });
    }

    function linkBigQuery(source, meta, scope, onDone) {
      if (!scope) {
        toast('Connect Google before linking BigQuery', false);
        return;
      }
      var ready = scope === 'client' ? clientBigqueryReady : agencyBigqueryReady;
      if (!ready) {
        if (!confirm('Google will ask for BigQuery read access and Analytics edit. ' +
                     'Continue?')) return;
        startGoogleAnalytics(scope, true, true);
        return;
      }
      var propertyId = String(meta.property_id || '').split('/').pop();
      formModal('Link GA4 to BigQuery', [{
        name: 'project', label: 'Google Cloud project ID',
        placeholder: 'my-project-123',
        help: 'The project that will own analytics_' + propertyId + '.'
      }, {
        name: 'location', label: 'BigQuery dataset location', type: 'select',
        options: ['US', 'EU', 'europe-west1', 'us-central1', 'asia-northeast1'],
        value: 'US',
        help: 'Must match the dataset GA4 creates; changing it later needs a new link.'
      }, {
        name: 'daily_export', label: 'Daily export', type: 'select',
        options: [{ value: 'yes', label: 'Yes - daily tables' },
                  { value: 'no', label: 'No' }], value: 'yes'
      }, {
        name: 'streaming_export', label: 'Streaming export', type: 'select',
        options: [{ value: 'no', label: 'No - lower cost' },
                  { value: 'yes', label: 'Yes - near real time' }], value: 'no',
        help: 'Streaming BigQuery export has extra Google Cloud cost.'
      }], function (data, fail, ok) {
        api('POST', '/api/analytics/google/bigquery-link', {
          connection_scope: scope,
          source_id: source.id,
          project: data.project,
          location: data.location,
          daily_export: data.daily_export === 'yes',
          streaming_export: data.streaming_export === 'yes'
        }).then(function (result) {
          ok();
          toast('BigQuery link created. GA4 may take up to 24 hours to export the first ' +
                'daily table.', true);
          if (onDone) onDone();
        }).catch(function (e) { fail(e.message); });
      }, 'Create link');
    }

    function syncBigQuery(source, button) {
      button.disabled = true;
      api('POST', '/api/analytics/bigquery/sync',
          { source_id: source.id, days: days })
        .then(function (result) {
          if (result.errors && result.errors.length) {
            toast('BigQuery partly refreshed: ' + result.errors[0], false);
          } else {
            toast(result.rows ? 'Refreshed ' + result.rows + ' BigQuery facts'
                              : 'BigQuery refreshed', true);
          }
          load();
        })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { button.disabled = false; });
    }

    function sourceCard(hasTraffic) {
      // With no traffic and no source, the empty state already owns the primary action;
      // a second "connect" card below it would compete with it.
      if (!sources.length && !hasTraffic) return null;
      if (!canManage && !sources.length) return null;
      var panel = el('div', 'panel-card');
      var headRow = el('div', 'panel-head');
      headRow.appendChild(el('h3', 'card-title', 'Traffic sources'));
      if (canManage) {
        var add = el('button', 'btn btn-ghost btn-sm', '+ Connect source');
        add.onclick = function () {
          analyticsSourceForm(null, sourceKinds, null, owner, load, ga4Email, oauthReady);
        };
        headRow.appendChild(add);
      }
      panel.appendChild(headRow);
      if (!sources.length) {
        panel.appendChild(el('p', 'muted', canManage
          ? 'Connect a source to start measuring traffic for this workspace.'
          : 'Your workspace admin can connect an analytics source.'));
        return panel;
      }
      sources.forEach(function (source) {
        var row = el('div', 'source-row');
        var copy = el('div');
        var meta = properties.filter(function (p) {
          return String(p.source_id) === String(source.id);
        })[0];
        var container = meta ? containers.filter(function (c) {
          return String(c.id) === String(meta.tag_container_id);
        })[0] : null;
        var googleScope = clientOauth ? 'client' : (owner && agencyOauth ? 'agency' : '');
        var bigqueryReady = googleScope === 'client'
          ? clientBigqueryReady : agencyBigqueryReady;
        var writeReady = googleScope === 'client'
          ? clientWriteReady : agencyWriteReady;
        copy.innerHTML = '<strong>' + esc(source.label || sourceLabel(source.kind)) + '</strong>' +
          '<div class="muted">' + esc(sourceLabel(source.kind)) +
          (source.kind === 'ga4' && source.auth_mode === 'oauth' ? ' · Google sign-in' : '') +
          (source.kind === 'ga4' && meta
            ? ' · ' + esc(meta.display_name || meta.property_id)
            : (source.external_id ? ' · ' + esc(source.external_id) : '')) +
          (source.kind === 'ga4'
            ? (container ? ' · GTM ' + esc(container.public_id || container.name)
                         : ' · no GTM container')
            : '') +
          (source.kind === 'ga4' && meta && meta.bigquery_project
            ? ' · BQ ' + esc(meta.bigquery_dataset || '') +
              (meta.bigquery_link_status
                ? ' · ' + esc(String(meta.bigquery_link_status).slice(0, 40))
                : '')
            : (source.kind === 'ga4' ? ' · no BigQuery export' : '')) +
          (source.last_sync_at ? ' · Updated ' + esc(String(source.last_sync_at).slice(0, 16))
                               : ' · Not synced yet') + '</div>' +
          (source.last_error
            ? '<div class="form-error">' +
              esc(owner ? String(source.last_error).slice(0, 140)
                        : (source.auth_mode === 'oauth'
                            ? 'This Google connection needs attention. Click Edit to reconnect.'
                            : 'This connection needs attention. Try Refresh or ask your admin.')) +
              '</div>'
            : '');
        row.appendChild(copy);
        var actions = el('div', 'row-actions');
        // Client admins manage the GA4 connection in this workspace; an older
        // platform-managed source (GHL/Meta/Caddy) stays visible but read-only.
        if (canManage && (owner || source.kind === 'ga4')) {
          if (source.kind === 'ga4') {
            var sync = el('button', 'btn btn-ghost btn-sm', 'Refresh');
            sync.onclick = function () { syncSource(source, sync); };
            actions.appendChild(sync);
            if (source.auth_mode === 'oauth' && (clientOauth || agencyOauth)) {
              var change = el('button', 'btn btn-ghost btn-sm', 'Change property');
              change.onclick = function () {
                chooseGoogleProperty(clientOauth ? 'client' : 'agency', load);
              };
              actions.appendChild(change);
            }
            if (meta && googleScope) {
              if (!bigqueryReady) {
                var enableBq = el('button', 'btn btn-ghost btn-sm', 'Enable BigQuery');
                enableBq.onclick = function () {
                  if (!confirm('Google will ask for BigQuery read access and Analytics ' +
                               'edit. Continue?')) return;
                  startGoogleAnalytics(googleScope, true, true);
                };
                actions.appendChild(enableBq);
              } else if (!meta.bigquery_project) {
                if (writeReady) {
                  var linkBq = el('button', 'btn btn-ghost btn-sm', 'Link BigQuery');
                  linkBq.disabled = !liveReady;
                  linkBq.title = liveReady ? '' : 'Live actions are off';
                  linkBq.onclick = function () {
                    linkBigQuery(source, meta, googleScope, load);
                  };
                  actions.appendChild(linkBq);
                } else {
                  var enableForLink = el('button', 'btn btn-ghost btn-sm',
                    'Enable setup for BigQuery');
                  enableForLink.onclick = function () {
                    startGoogleAnalytics(googleScope, true, true);
                  };
                  actions.appendChild(enableForLink);
                }
              } else {
                var syncBq = el('button', 'btn btn-ghost btn-sm', 'Sync BigQuery');
                syncBq.onclick = function () { syncBigQuery(source, syncBq); };
                actions.appendChild(syncBq);
              }
            }
          }
          var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
          edit.onclick = function () {
            analyticsSourceForm(source, sourceKinds, null, owner, load, ga4Email, oauthReady);
          };
          actions.appendChild(edit);
          var remove = el('button', 'btn btn-danger btn-sm', 'Remove');
          remove.onclick = function () {
            if (!confirm('Remove this analytics connection?')) return;
            api('DELETE', '/api/data-sources/' + source.id)
              .then(load).catch(function (e) { toast(e.message, false); });
          };
          actions.appendChild(remove);
        } else if (canManage) {
          actions.appendChild(el('span', 'muted', 'Managed by your agency'));
        }
        row.appendChild(actions);
        panel.appendChild(row);
      });
      return panel;
    }

    function googleConnectionCard() {
      if (!canManage) return null;
      var card = el('div', 'panel-card');
      card.appendChild(el('h3', 'card-title', 'Google connection'));
      var status = el('p', 'fine-print');
      if (!oauthReady) {
        status.textContent = 'Google sign-in is not configured. Add the OAuth client ID ' +
          'and secret in Settings, then connect this client.';
        card.appendChild(status);
        return card;
      }
      var activeScope = clientOauth ? 'client' : ((owner && agencyOauth) ? 'agency' : '');
      var activeWrite = activeScope === 'client' ? clientWriteReady : agencyWriteReady;
      var activeBigquery = activeScope === 'client'
        ? clientBigqueryReady : agencyBigqueryReady;
      var consent = setupStatus.consent || {};
      var setupProperties = setupStatus.properties || properties;
      var setupContainers = setupStatus.containers || containers;
      var gaAccounts = setupStatus.ga_accounts;
      var gtmAccounts = setupStatus.gtm_accounts;
      var bigQueryLinked = setupProperties.some(function (p) {
        return !!(p && p.bigquery_project);
      });
      var parts = [];
      parts.push(clientOauth ? 'This client has its own Google connection.'
                             : 'This client has no Google connection of its own.');
      if (owner) {
        parts.push(agencyOauth ? 'The agency Google connection is available.'
                               : 'The agency Google connection is not connected yet.');
      }
      parts.push(activeScope
        ? (activeWrite ? 'Setup and automation access are granted.'
                       : 'Read-only access is granted.')
        : 'Connect Google to choose a property.');
      if (activeScope && activeWrite) {
        parts.push(consent.accepted
          ? 'Full setup automation is on for this workspace.'
          : 'Full setup automation has not been granted yet.');
        if (consent.accepted) {
          parts.push(setupProperties.length + ' GA4 propert' +
            (setupProperties.length === 1 ? 'y' : 'ies') + ', ' +
            setupContainers.length + ' Tag Manager container' +
            (setupContainers.length === 1 ? '' : 's') + '.');
        }
      }
      if (activeScope) {
        if (gaAccounts === null || gaAccounts === undefined) {
          parts.push('Checking which Analytics accounts Google can see…');
        } else if (!gaAccounts.length) {
          parts.push('No Analytics account is visible to this Google connection.');
        } else {
          parts.push(gaAccounts.length + ' Analytics account' +
                     (gaAccounts.length === 1 ? '' : 's') + ' available.');
        }
        if (gtmAccounts === null || gtmAccounts === undefined) {
          parts.push('Checking Tag Manager accounts…');
        } else if (!gtmAccounts.length) {
          parts.push('No Tag Manager account is visible; Google requires you to ' +
                     'create one there.');
        } else {
          parts.push(gtmAccounts.length + ' Tag Manager account' +
                     (gtmAccounts.length === 1 ? '' : 's') + ' available.');
        }
        parts.push(activeBigquery
          ? 'BigQuery read access is granted.'
          : 'BigQuery read access is not granted.');
        parts.push(bigQueryLinked
          ? 'A BigQuery export is linked to the stored property.'
          : 'BigQuery stays off until you link a Google Cloud project; creating that ' +
            'project is yours to do.');
      }
      if (setupStatus.discovery_error) {
        parts.push('Google account check: ' + setupStatus.discovery_error);
      }
      if (activeScope && activeWrite && !liveReady) {
        parts.push('Live actions are off; an operator must enable ' +
                   'ALLOW_LIVE_ACTIONS before setup can write.');
      }
      status.textContent = parts.join(' ');
      card.appendChild(status);
      var actions = el('div', 'row-actions');
      if (!clientOauth) {
        var autoClient = el('button', 'btn btn-primary btn-sm',
          'Connect Google & set up');
        autoClient.onclick = function () { startGoogleSetup('client', load); };
        actions.appendChild(autoClient);
        var clientButton = el('button', 'btn btn-ghost btn-sm',
          "Connect read-only");
        clientButton.onclick = function () { startGoogleAnalytics('client'); };
        actions.appendChild(clientButton);
      }
      if (owner && !agencyOauth) {
        var agencyButton = el('button', 'btn btn-ghost btn-sm',
          'Connect agency Google');
        agencyButton.onclick = function () { startGoogleAnalytics('agency'); };
        actions.appendChild(agencyButton);
      }
      if (clientOauth) {
        var chooseClient = el('button', 'btn btn-primary btn-sm',
          'Choose GA4 property');
        chooseClient.onclick = function () { chooseGoogleProperty('client', load); };
        actions.appendChild(chooseClient);
      }
      if (owner && agencyOauth) {
        var chooseAgency = el('button', 'btn btn-ghost btn-sm',
          'Use agency GA4 property');
        chooseAgency.onclick = function () { chooseGoogleProperty('agency', load); };
        actions.appendChild(chooseAgency);
      }
      if (activeScope && gaAccounts && !gaAccounts.length) {
        var newAccount = el('button', 'btn btn-ghost btn-sm',
          'Create Analytics account');
        newAccount.onclick = function () { openGoogleAccountHelp(activeScope, load, true); };
        actions.appendChild(newAccount);
      }
      if (activeScope && gtmAccounts && !gtmAccounts.length) {
        var tagManager = el('button', 'btn btn-ghost btn-sm',
          'Open Tag Manager');
        tagManager.onclick = function () { openGoogleTagManagerHelp(load); };
        actions.appendChild(tagManager);
      }
      if (activeScope && !activeWrite) {
        var upgrade = el('button', 'btn btn-primary btn-sm',
          'Grant Google setup access');
        upgrade.onclick = function () {
          if (!confirm('Google will ask for write access to Analytics, Tag Manager ' +
                       'and BigQuery. Setup actions you start here can then create ' +
                       'and publish tags.')) return;
          startGoogleAnalytics(activeScope, true, true, true);
        };
        actions.appendChild(upgrade);
      }
      if (activeScope && activeWrite && !consent.accepted) {
        var grant = el('button', 'btn btn-primary btn-sm',
          'Grant full setup automation');
        grant.onclick = function () {
          openGoogleSetupConsent(activeScope, function () {
            startGoogleSetup(activeScope, load);
          });
        };
        actions.appendChild(grant);
      }
      if (activeScope && activeWrite && consent.accepted) {
        var setup = el('button', 'btn btn-primary btn-sm', 'Set up everything');
        setup.disabled = !liveReady;
        setup.title = liveReady ? '' : 'Live actions are off';
        setup.onclick = function () { startGoogleSetup(activeScope, load); };
        actions.appendChild(setup);
        var create = el('button', 'btn btn-ghost btn-sm',
          'Create property manually');
        create.disabled = !liveReady;
        create.title = liveReady ? '' : 'Live actions are off';
        create.onclick = function () { createGoogleSetup(activeScope, load); };
        actions.appendChild(create);
        var stop = el('button', 'btn btn-ghost btn-sm', 'Stop automation');
        stop.onclick = function () { revokeGoogleSetup(activeScope, load); };
        actions.appendChild(stop);
      }
      if (containers.length) {
        var install = el('button', 'btn btn-ghost btn-sm', 'Install GTM snippet');
        install.onclick = function () { installGoogleTag(load); };
        actions.appendChild(install);
      }
      card.appendChild(actions);
      return card;
    }

    function pivotRows(rows, keys) {
      var out = {};
      (rows || []).forEach(function (row) {
        var dims = row.dimensions || {};
        var key = keys.map(function (name) { return dims[name] || ''; }).join('|');
        if (!out[key]) {
          out[key] = { dims: {}, metrics: {} };
          keys.forEach(function (name) { out[key].dims[name] = dims[name] || ''; });
        }
        out[key].metrics[row.metric] =
          (out[key].metrics[row.metric] || 0) + Number(row.value || 0);
      });
      return Object.keys(out).map(function (key) { return out[key]; });
    }

    function reportTable(definition, rows) {
      var labels = {
        sessions: 'Sessions', engagedSessions: 'Engaged',
        engagementRate: 'Engagement', keyEvents: 'Key events',
        screenPageViews: 'Views', eventCount: 'Events',
        totalUsers: 'Users', activeUsers: 'Active users',
        users: 'Users', views: 'Views'
      };
      rows.sort(function (a, b) {
        return (b.metrics[definition.sort] || 0) - (a.metrics[definition.sort] || 0);
      });
      var shown = rows.slice(0, 20);
      var table = el('table', 'table');
      var headers = definition.headers.concat(
        definition.metrics.map(function (metric) { return labels[metric] || metric; }));
      table.innerHTML = '<thead><tr>' + headers.map(function (header) {
        return '<th>' + esc(header) + '</th>';
      }).join('') + '</tr></thead>';
      var tbody = el('tbody');
      shown.forEach(function (row) {
        var tr = el('tr');
        var cells = definition.keys.map(function (key) {
          return '<td>' + esc(row.dims[key] || '—') + '</td>';
        }).concat(definition.metrics.map(function (metric) {
          var number = Number(row.metrics[metric] || 0);
          var text = metric === 'engagementRate'
            ? (number * 100).toFixed(1) + '%'
            : (number >= 10000 ? (number / 1000).toFixed(1) + 'k'
                               : String(Math.round(number * 100) / 100));
          return '<td>' + esc(text) + '</td>';
        }));
        tr.innerHTML = cells.join('');
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      var wrap = el('div');
      wrap.appendChild(table);
      if (rows.length > shown.length) {
        wrap.appendChild(el('p', 'fine-print',
          'Showing the top ' + shown.length + ' of ' + rows.length + ' rows.'));
      }
      return wrap;
    }

    function reportCard() {
      var reports = (reportData && reportData.reports) || {};
      var nonEmpty = Object.keys(reports).filter(function (key) {
        return (reports[key] || []).length;
      });
      if (!nonEmpty.length) return null;
      var definitions = [{
        key: 'traffic', label: 'Acquisition',
        keys: ['sessionDefaultChannelGroup', 'sessionSource', 'sessionMedium'],
        headers: ['Channel', 'Source', 'Medium'],
        metrics: ['sessions', 'engagedSessions', 'engagementRate', 'keyEvents'],
        sort: 'sessions'
      }, {
        key: 'pages', label: 'Pages',
        keys: ['pagePath', 'pageTitle'], headers: ['Page', 'Title'],
        metrics: ['screenPageViews', 'sessions', 'keyEvents'], sort: 'screenPageViews'
      }, {
        key: 'events', label: 'Events', keys: ['eventName'], headers: ['Event'],
        metrics: ['eventCount', 'keyEvents', 'totalUsers'], sort: 'eventCount'
      }, {
        key: 'audience', label: 'Audience',
        keys: ['country', 'deviceCategory'], headers: ['Country', 'Device'],
        metrics: ['activeUsers', 'sessions', 'keyEvents'], sort: 'activeUsers'
      }, {
        key: 'bq_events', label: 'BQ events', keys: ['event_name'], headers: ['Event'],
        metrics: ['eventCount', 'users'], sort: 'eventCount'
      }, {
        key: 'bq_pages', label: 'BQ pages', keys: ['page_path'], headers: ['Page'],
        metrics: ['views', 'users'], sort: 'views'
      }, {
        key: 'bq_acquisition', label: 'BQ acquisition',
        keys: ['source', 'medium'], headers: ['Source', 'Medium'],
        metrics: ['sessions', 'users'], sort: 'sessions'
      }];
      var card = el('div', 'panel-card');
      card.appendChild(el('h3', 'card-title', 'Reports'));
      var tabs = el('div', 'row-actions');
      var host = el('div', 'mt');
      var buttons = {};
      function draw(definition) {
        Object.keys(buttons).forEach(function (key) {
          buttons[key].className = 'btn btn-ghost btn-sm' +
            (key === definition.key ? ' active' : '');
        });
        host.innerHTML = '';
        var rows = pivotRows(reports[definition.key], definition.keys);
        if (!rows.length) {
          host.appendChild(el('p', 'fine-print', 'No data for this period yet.'));
          return;
        }
        host.appendChild(reportTable(definition, rows));
      }
      definitions.forEach(function (definition) {
        var button = el('button', 'btn btn-ghost btn-sm', definition.label);
        button.type = 'button';
        button.onclick = function () { draw(definition); };
        buttons[definition.key] = button;
        tabs.appendChild(button);
      });
      card.appendChild(tabs);
      card.appendChild(host);
      draw(definitions[0]);
      return card;
    }

    function setupHistoryCard() {
      if (!canManage || !actionLog.length) return null;
      var labels = {
        ga4_create_property: 'Created GA4 property',
        ga4_create_stream: 'Created web stream',
        ga4_create_key_event: 'Marked key event',
        ga4_create_audience: 'Created audience',
        gtm_create_container: 'Created and published GTM container',
        gtm_install_snippet: 'Installed GTM snippet',
        bigquery_link: 'Linked GA4 to BigQuery',
        bigquery_sync: 'Synced BigQuery facts',
        alerts_evaluate: 'Evaluated analytics alerts'
      };
      var panel = el('div', 'panel-card');
      panel.appendChild(el('h3', 'card-title', 'Recent Google setup actions'));
      actionLog.slice(0, 6).forEach(function (row) {
        var item = el('div', 'source-row');
        var copy = el('div');
        var label = labels[row.action] || row.action || 'Google action';
        var meta = [row.target, String(row.created_at || '').slice(0, 16)]
          .filter(Boolean).map(esc).join(' · ');
        copy.innerHTML = '<strong>' + esc(label) + '</strong>' +
          '<div class="muted">' + meta + '</div>' +
          (row.result
            ? '<div class="muted">' + esc(String(row.result).slice(0, 160)) + '</div>'
            : '');
        item.appendChild(copy);
        item.appendChild(el('span', row.status === 'ok' ? 'pill up' : 'pill down',
          row.status === 'ok' ? 'Done' : 'Failed'));
        panel.appendChild(item);
      });
      return panel;
    }

    function intelligenceCard() {
      var insights = (insightsData && insightsData.insights) || [];
      if (!canManage && !insights.length && !alerts.length) return null;
      var card = el('div', 'panel-card');
      card.appendChild(el('h3', 'card-title', 'What changed'));
      if (insights.length) {
        insights.forEach(function (item) {
          var row = el('div', 'source-row');
          var copy = el('div');
          copy.innerHTML = '<strong>' + esc(item.title) + '</strong>' +
            '<div class="muted">' + esc(item.detail) + '</div>';
          row.appendChild(copy);
          var severity = item.severity || 'info';
          row.appendChild(el('span',
            'pill ' + (severity === 'info' ? 'flat' : 'down'),
            severity === 'critical' ? 'Critical'
              : (severity === 'warning' ? 'Watch' : 'Info')));
          card.appendChild(row);
        });
      } else {
        card.appendChild(el('p', 'muted', 'No notable change in this period yet.'));
      }

      if (alerts.length) {
        card.appendChild(el('h4', null, 'Open alerts'));
        alerts.slice(0, 6).forEach(function (alert) {
          var row = el('div', 'source-row');
          var copy = el('div');
          copy.innerHTML = '<strong>' + esc(alert.title || 'Analytics alert') +
            '</strong><div class="muted">' + esc(alert.detail || '') + '</div>';
          row.appendChild(copy);
          if (canManage) {
            var read = el('button', 'btn btn-ghost btn-sm', 'Mark read');
            read.onclick = function () {
              read.disabled = true;
              api('POST', '/api/analytics/alerts/' + alert.id + '/read', {})
                .then(load).catch(function (e) {
                  read.disabled = false;
                  toast(e.message, false);
                });
            };
            row.appendChild(read);
          }
          card.appendChild(row);
        });
      } else if (canManage) {
        card.appendChild(el('p', 'fine-print', 'No open alerts.'));
      }

      if (canManage) {
        var head = el('div', 'panel-head');
        head.appendChild(el('h4', null, 'Alert rules'));
        var run = el('button', 'btn btn-ghost btn-sm', 'Run now');
        run.onclick = function () {
          run.disabled = true;
          api('POST', '/api/analytics/alerts/evaluate', { days: days })
            .then(function (result) {
              toast(result.alerts ? 'Created ' + result.alerts + ' alert(s)'
                                  : 'No new alerts', true);
              load();
            })
            .catch(function (e) { toast(e.message, false); })
            .finally(function () { run.disabled = false; });
        };
        head.appendChild(run);
        var add = el('button', 'btn btn-ghost btn-sm', 'Add rule');
        add.onclick = addAlertRule;
        head.appendChild(add);
        card.appendChild(head);
        if (!alertRules.length) {
          var defaults = el('button', 'btn btn-primary btn-sm',
            'Turn on default alerts');
          defaults.onclick = function () {
            defaults.disabled = true;
            api('POST', '/api/analytics/alert-rules/defaults', {})
              .then(function () {
                toast('Default alerts are on; they stay in-app until you add email.',
                      true);
                load();
              })
              .catch(function (e) {
                defaults.disabled = false;
                toast(e.message, false);
              });
          };
          card.appendChild(defaults);
        }
        alertRules.forEach(function (rule) {
          var row = el('div', 'source-row');
          var copy = el('div');
          var metric = { pageviews: 'Pageviews', sessions: 'Sessions', errors: 'Errors' }[
            rule.metric] || rule.metric;
          copy.innerHTML = '<strong>' + esc(rule.name) + '</strong>' +
            '<div class="muted">' + esc(metric) + ' · ' +
            esc(rule.comparison === 'spike' ? 'spikes by' : 'falls by') + ' ' +
            esc(rule.threshold) + '% over ' + esc(rule.window_days) + ' days' +
            (rule.email ? ' · emails admins' : ' · in-app only') + '</div>';
          row.appendChild(copy);
          var remove = el('button', 'btn btn-danger btn-sm', 'Delete');
          remove.onclick = function () {
            if (!confirm('Delete the alert rule "' + rule.name + '"?')) return;
            remove.disabled = true;
            api('DELETE', '/api/analytics/alert-rules/' + rule.id)
              .then(load)
              .catch(function (e) {
                remove.disabled = false;
                toast(e.message, false);
              });
          };
          row.appendChild(remove);
          card.appendChild(row);
        });
      }
      return card;
    }

    function addAlertRule() {
      formModal('Add an alert rule', [{
        name: 'name', label: 'Name', value: 'Pageviews falling'
      }, {
        name: 'metric', label: 'Metric', type: 'select',
        options: [{ value: 'pageviews', label: 'Pageviews' },
                  { value: 'sessions', label: 'Sessions' },
                  { value: 'errors', label: 'Errors' }]
      }, {
        name: 'comparison', label: 'Condition', type: 'select',
        options: [{ value: 'drop', label: 'Falls by at least' },
                  { value: 'spike', label: 'Rises by at least' }]
      }, {
        name: 'threshold', label: 'Threshold (%)', type: 'number', value: '30'
      }, {
        name: 'window_days', label: 'Window', type: 'select',
        options: [{ value: '7', label: '7 days' }, { value: '14', label: '14 days' },
                  { value: '30', label: '30 days' }], value: '7'
      }, {
        name: 'email', label: 'Email client admins', type: 'select',
        options: [{ value: 'no', label: 'No - in-app only' },
                  { value: 'yes', label: 'Yes - send an email' }], value: 'no'
      }], function (data, fail, ok) {
        api('POST', '/api/analytics/alert-rules', {
          name: data.name,
          metric: data.metric,
          comparison: data.comparison,
          threshold: data.threshold,
          window_days: data.window_days,
          email: data.email === 'yes',
          enabled: true
        }).then(function () {
          ok();
          toast('Alert rule added', true);
          load();
        }).catch(function (e) { fail(e.message); });
      }, 'Add rule');
    }

    function render(d) {
      body.innerHTML = '';
      var now = d.current || {};
      var change = d.change || {};
      var hasTraffic = !!(now.pageviews || now.sessions);
      if (!hasTraffic) {
        var hasSource = sources.length > 0;
        var action = canManage && !hasSource ? connectButton('Connect analytics') : null;
        var empty = emptyPanel(
          hasSource ? 'No visitors in the last ' + days + ' days.' : 'No traffic recorded yet.',
          hasSource
            ? 'Your analytics source is connected, but it has no visitors in this window.'
            : (canManage
               ? 'Connect Google Analytics, or let the automatic website logs do the work once your site is live.'
               : 'Your workspace admin can connect an analytics source for this workspace.'),
          action);
        body.appendChild(empty);
        if (canManage && hasSource && days === 7) {
          var wider = el('button', 'btn btn-ghost btn-sm', 'View 30 days');
          wider.onclick = function () { days = 30; load(); };
          empty.querySelector('.empty-actions').appendChild(wider);
        }
      } else {
        var grid = el('div', 'kpi-grid');
        grid.appendChild(card('Pageviews', now.pageviews, change.pageviews));
        grid.appendChild(card('Sessions', now.sessions, change.sessions));
        grid.appendChild(card('Errors', now.errors, change.errors));
        body.appendChild(grid);
        var measured = (d.sources || []).map(sourceLabel).join(', ');
        body.appendChild(el('p', 'fine-print',
          'Measured from ' + (measured || 'no connected source') +
          ' · since ' + esc(d.since)));
        if (days === 7) {
          var toggle = el('button', 'btn btn-ghost btn-sm', 'View 30 days');
          toggle.onclick = function () { days = 30; load(); };
          body.appendChild(toggle);
        }
        var pages = now.top_pages || [];
        if (pages.length) {
          var top = Math.max.apply(null, pages.map(function (p) { return p.views || 0; })) || 1;
          var cardBox = el('div', 'panel-card');
          cardBox.appendChild(el('h3', 'card-title', 'Top pages'));
          var list = el('div', 'rank-list');
          pages.forEach(function (page) {
            var row = el('div', 'rank-row');
            var pct = Math.round(((page.views || 0) / top) * 100);
            row.innerHTML = '<div class="rank-head"><span>' + esc(page.path) +
              '</span><span class="muted">' + (page.views || 0) + '</span></div>' +
              '<div class="rank-track"><div class="rank-fill" style="width:' + pct + '%"></div></div>';
            list.appendChild(row);
          });
          cardBox.appendChild(list);
          body.appendChild(cardBox);
        }
      }
      var panel = sourceCard(hasTraffic);
      if (panel) body.appendChild(panel);
      var googleCard = googleConnectionCard();
      if (googleCard) body.appendChild(googleCard);
      var historyCard = setupHistoryCard();
      if (historyCard) body.appendChild(historyCard);
      var reportsPanel = reportCard();
      if (reportsPanel) body.appendChild(reportsPanel);
      var intelligence = intelligenceCard();
      if (intelligence) body.appendChild(intelligence);
    }

    function load() {
      var workspace = owner ? state.user.acting_org
                            : (state.user.org && state.user.org.id);
      if (!workspace) {
        body.innerHTML = '';
        body.appendChild(emptyPanel(
          owner ? 'Choose a client to see analytics.' : 'No workspace is attached.',
          owner ? 'Use the workspace switcher under the logo.'
                : 'Ask your agency to attach this login to a workspace.'));
        return;
      }
      body.innerHTML = '<div class="muted" style="padding:16px">Loading analytics…</div>';
      Promise.all([
        api('GET', '/api/analytics/summary?days=' + days),
        api('GET', '/api/data-sources'),
        api('GET', '/api/analytics/reports?days=' + days)
          .catch(function () { return { reports: {} }; }),
        api('GET', '/api/analytics/actions')
          .catch(function () { return { actions: [] }; }),
        api('GET', '/api/analytics/insights?days=' + days)
          .catch(function () { return { insights: [] }; }),
        api('GET', '/api/analytics/alerts?status=open')
          .catch(function () { return { alerts: [] }; }),
        canManage
          ? api('GET', '/api/analytics/alert-rules').catch(function () { return { rules: [] }; })
          : Promise.resolve({ rules: [] })
      ]).then(function (results) {
        var data = results[0] || {};
        var sourceData = results[1] || {};
        sources = sourceData.sources || [];
        sourceKinds = sourceData.kinds || [];
        ga4Email = sourceData.ga4_email || '';
        oauthReady = !!sourceData.oauth_ready;
        clientOauth = !!sourceData.client_oauth_connected;
        agencyOauth = !!sourceData.agency_oauth_connected;
        clientWriteReady = !!sourceData.client_oauth_write_ready;
        agencyWriteReady = !!sourceData.agency_oauth_write_ready;
        clientBigqueryReady = !!sourceData.client_oauth_bigquery_ready;
        agencyBigqueryReady = !!sourceData.agency_oauth_bigquery_ready;
        liveReady = sourceData.dry_run === undefined ? true : !sourceData.dry_run;
        properties = sourceData.properties || [];
        containers = sourceData.containers || [];
        reportData = results[2] || { reports: {} };
        actionLog = (results[3] || {}).actions || [];
        insightsData = results[4] || { insights: [] };
        alerts = (results[5] || {}).alerts || [];
        alertRules = (results[6] || {}).rules || [];
        canManage = canManage || !!sourceData.can_manage;
        var activeScope = clientOauth ? 'client'
          : ((owner && agencyOauth) ? 'agency' : 'client');
        if (!canManage) {
          setupStatus = {};
          render(data);
          return;
        }
        analyticsSetupStatus(activeScope).then(function (status) {
          setupStatus = status || {};
          render(data);
          maybeRunAutoSetup(activeScope);
          if (setupStatus.connected) {
            // Account discovery is a second, opt-in call: Google being slow must not
            // stop the traffic page from loading.
            analyticsSetupStatus(activeScope, true).then(function (full) {
              setupStatus = full || setupStatus;
              render(data);
              maybeRunAutoSetup(activeScope);
            }).catch(function () { /* keep the first status */ });
          }
        }).catch(function () {
          // An older server may not have the setup endpoint yet; the connection card
          // still works, it just cannot show consent/one-click state.
          setupStatus = {};
          render(data);
        });
      }).catch(function (e) {
        body.innerHTML = '';
        body.appendChild(emptyPanel('Analytics is unavailable.',
          e.message || 'Please try again in a moment.'));
      });
    }

    if (notice === 'oauth-ok' || notice === 'oauth-error') {
      api('GET', '/api/analytics/oauth/notice').then(function (d) {
        var note = d.notice || {};
        if (note.message) toast(note.message, note.kind === 'ok');
        if (note.auto_setup) {
          autoSetupPending = true;
          var scope = clientOauth ? 'client'
            : ((owner && agencyOauth) ? 'agency' : 'client');
          if (setupStatus.connected) maybeRunAutoSetup(scope);
          else load();
        }
      }).catch(function () {
        toast(notice === 'oauth-ok' ? 'Google Analytics connected.'
                                    : 'Google Analytics was not connected.', false);
      });
      // The notice is one-shot; keep the visible page URL clean.
      try { history.replaceState(null, '', '#/app/analytics'); } catch (e) { /* older browsers */ }
    }
    load();
    return container;
  }

  // What the Studio is editing, and which chat it is continuing, remembered per user and
  // acting client. The operator works across clients from one login, so `user.org` is the
  // wrong scope: every client shared one key, and switching workspaces looked like the
  // Studio had forgotten the thread and the page.
  function studioOrgKey() {
    var user = (state && state.user) || {};
    if (user.acting_org) return String(user.acting_org);
    if (user.org && user.org.id) return String(user.org.id);
    return 'op';
  }

  function studioDraftKey() {
    return 'studio:draft:' + studioOrgKey() + ':' + ((state.user && state.user.id) || 0);
  }

  function readStudioDraft() {
    try {
      var raw = JSON.parse(localStorage.getItem(studioDraftKey()) || 'null');
      var id = raw && parseInt(raw.id, 10);
      return id > 0 ? { kind: 'deliverable', id: id } : null;
    } catch (e) { return null; }
  }

  function writeStudioDraft(item) {
    try {
      if (item && item.id) {
        localStorage.setItem(studioDraftKey(),
          JSON.stringify({ kind: 'deliverable', id: item.id }));
      } else {
        localStorage.removeItem(studioDraftKey());
      }
    } catch (e) { /* private mode: the chat session still remembers it */ }
  }

  // The active conversation is shared by every chat door: the Studio, the floating Ask
  // sheet, and their voices. Only "New chat" clears it. The key was previously local to
  // the Studio view, which is why the sheet and voice could not continue its thread.
  function studioSessionKey() {
    return 'studio:session:' + studioOrgKey() + ':' + ((state.user && state.user.id) || 0);
  }

  function readStudioSession() {
    try {
      return parseInt(localStorage.getItem(studioSessionKey()), 10) || null;
    } catch (e) {
      return null;
    }
  }

  function writeStudioSession(value) {
    var id = parseInt(value, 10) || null;
    try {
      if (id) localStorage.setItem(studioSessionKey(), String(id));
      else localStorage.removeItem(studioSessionKey());
    } catch (e) { /* private mode: the session still works for this visit */ }
    // The first-run welcome and the tour can create the first session while the
    // Studio is already mounted. A light event lets the mounted thread adopt it.
    try {
      if (typeof window !== 'undefined' && typeof CustomEvent === 'function' &&
          window.dispatchEvent) {
        window.dispatchEvent(new CustomEvent('ascend:chat-session', { detail: id }));
      }
    } catch (e) { /* the store is still the source of truth */ }
    return id;
  }

  var chatSessionPending = null;

  function ensureChatSession() {
    var stored = readStudioSession();
    if (stored) return Promise.resolve(stored);
    if (!state.user) return Promise.resolve(null);
    if (chatSessionPending) return chatSessionPending;
    chatSessionPending = api('POST', '/api/assistant/sessions', {})
      .then(function (d) {
        var created = (d && d.session) || {};
        return created.id ? writeStudioSession(created.id) : null;
      })
      .catch(function () { return null; })
      .then(function (id) {
        chatSessionPending = null;
        return id;
      });
    return chatSessionPending;
  }

  // ---------- Studio: chats on the left, the conversation, the artifact ----------
  // The workspace's main tool: one conversation per job, the page it is changing beside
  // it, and the draft named on every turn. The old Assistant page was the same manager
  // without a canvas, so its session rail lives here now.
  function viewStudio(kind, id) {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Studio</h2><p class="muted">Every job is a conversation. ' +
      'The chat on the left is the thread; the page it changes stays on the right - live ' +
      'while the team works, and as a preview once it saves.</p></div>';
    container.appendChild(head);

    var createBuild = el('button', 'btn btn-primary', 'Create something');
    createBuild.type = 'button';
    createBuild.setAttribute('data-tour', 'studio-create');
    createBuild.onclick = function () { openBuildPicker(); };
    head.appendChild(createBuild);
    var studioHelp = el('button', 'btn btn-ghost', 'How Studio works');
    studioHelp.type = 'button';
    studioHelp.onclick = function () { startStudioTour(true, null, false); };
    head.appendChild(studioHelp);

    var layout = el('div', 'studio-layout');
    layout.setAttribute('data-pane', 'chat');
    container.appendChild(layout);

    // ----- far left: the chats, one per job, newest first -----
    // A new chat is created on its first message, so a blank row never sits there
    // pretending to be work.
    var rail = el('aside', 'asst-rail studio-rail');
    rail.setAttribute('data-tour', 'studio-chats');
    var railHead = el('div', 'asst-rail-head');
    railHead.appendChild(el('h3', 'asst-rail-title', 'Chats'));
    var newChat = el('button', 'btn btn-primary btn-sm', '+ New');
    newChat.type = 'button';
    newChat.title = 'Start a new conversation, with a blank canvas';
    railHead.appendChild(newChat);
    rail.appendChild(railHead);
    var sessionList = el('div', 'asst-session-list');
    rail.appendChild(sessionList);
    layout.appendChild(rail);

    // ----- middle: the conversation, the same thread voice and typing share -----
    var chat = el('section', 'studio-chat');
    var chatHead = el('div', 'studio-chat-head');
    var chip = el('button', 'studio-chip empty', 'Working on: nothing yet');
    chip.type = 'button';
    chip.setAttribute('data-tour', 'studio-working-on');
    chip.title = 'Choose the draft this conversation edits';
    chatHead.appendChild(chip);
    var pick = el('button', 'btn btn-ghost btn-sm', 'Choose…');
    pick.type = 'button';
    chatHead.appendChild(pick);
    chat.appendChild(chatHead);

    var thread = el('div', 'asst-thread studio-thread');
    chat.appendChild(thread);

    var box = el('textarea', 'prompt-box');
    box.placeholder = 'Make the headline shorter…';
    var send = el('button', 'btn btn-primary', 'Send');
    var composer = el('div', 'asst-composer');
    composer.setAttribute('data-tour', 'studio-composer');
    composer.appendChild(box);
    composer.appendChild(send);
    chat.appendChild(composer);
    if (pendingStudioPrompt) {
      box.value = pendingStudioPrompt;
      pendingStudioPrompt = '';
      box.focus();
    }
    if (pendingStudioSend) {
      pendingStudioSend = false;
      // Wait until the rest of the Studio view, its session and its send handler are
      // initialized. This is an explicit Ask AI turn, not a recommendation chip click.
      setTimeout(function () { sendTurn(); }, 0);
    }

    var answerHint = el('p', 'asst-hint');
    answerHint.hidden = true;
    var answerText = el('span');
    answerHint.appendChild(answerText);
    var typeInstead = el('button', 'btn btn-ghost btn-sm', 'Type the answer');
    typeInstead.type = 'button';
    typeInstead.hidden = true;
    typeInstead.onclick = function () {
      typedAnswer = true;
      updateComposerState();
      try { box.focus(); } catch (e) { /* not focusable yet */ }
    };
    answerHint.appendChild(typeInstead);
    chat.appendChild(answerHint);

    var voiceRow = el('div', 'studio-voice-row');
    var talk = el('button', 'btn btn-ghost', 'Talk');
    talk.style.display = 'none';
    var voiceNote = el('p', 'asst-hint');
    voiceNote.setAttribute('role', 'status');
    voiceRow.appendChild(talk);
    voiceRow.appendChild(voiceNote);
    chat.appendChild(voiceRow);
    var voiceHost = el('div', 'studio-voice-host');
    voiceHost.hidden = true;
    chat.appendChild(voiceHost);
    layout.appendChild(chat);

    // ----- right: the canvas -----
    var canvas = el('section', 'studio-canvas');
    var canvasHead = el('div', 'studio-canvas-head');
    var tabs = el('div', 'studio-tabs');
    var liveTab = el('button', 'studio-tab active', 'Live job');
    var previewTab = el('button', 'studio-tab', 'Preview');
    liveTab.type = 'button';
    previewTab.type = 'button';
    liveTab.setAttribute('data-tour', 'studio-live');
    previewTab.setAttribute('data-tour', 'studio-preview');
    tabs.appendChild(liveTab);
    tabs.appendChild(previewTab);
    canvasHead.appendChild(tabs);

    var tools = el('div', 'studio-tools');
    var widthToggle = el('button', 'btn btn-ghost btn-sm', 'Mobile');
    var refresh = el('button', 'btn btn-ghost btn-sm', 'Refresh');
    var openFull = el('button', 'btn btn-ghost btn-sm', 'Open full');
    widthToggle.type = 'button';
    refresh.type = 'button';
    openFull.type = 'button';
    tools.appendChild(widthToggle);
    tools.appendChild(refresh);
    tools.appendChild(openFull);
    canvasHead.appendChild(tools);
    canvas.appendChild(canvasHead);

    // Mobile: one pane at a time, chosen by hand. The desktop layout ignores this.
    var mobileTabs = el('div', 'studio-mobile-tabs');
    var mobileButtons = {};
    function markMobileTab(label) {
      Object.keys(mobileButtons).forEach(function (key) {
        mobileButtons[key].className = 'studio-tab' + (key === label ? ' active' : '');
      });
    }
    ['Chat', 'Live', 'Preview'].forEach(function (label) {
      var button = el('button', 'studio-tab' + (label === 'Chat' ? ' active' : ''), label);
      button.type = 'button';
      button.onclick = function () {
        markMobileTab(label);
        if (label === 'Chat') { layout.setAttribute('data-pane', 'chat'); return; }
        layout.setAttribute('data-pane', 'canvas');
        setTab(label === 'Live' ? 'live' : 'preview');
      };
      mobileButtons[label] = button;
      mobileTabs.appendChild(button);
    });
    canvas.appendChild(mobileTabs);

    var livePane = el('div', 'studio-live');
    var liveSummary = el('p', 'fine-print',
      'No job is running. Ask for a change and it will appear here.');
    livePane.appendChild(liveSummary);
    var approvalsHost = el('div');
    livePane.appendChild(approvalsHost);
    var liveLog = el('div', 'studio-log');
    livePane.appendChild(liveLog);
    canvas.appendChild(livePane);

    var previewPane = el('div', 'studio-preview');
    previewPane.hidden = true;
    var previewHead = el('div', 'studio-preview-head');
    var previewTitle = el('strong', null, 'Nothing open');
    var previewMeta = el('span', 'fine-print');
    previewHead.appendChild(previewTitle);
    previewHead.appendChild(previewMeta);
    previewPane.appendChild(previewHead);
    // sandbox="" - no scripts, no forms, no same-origin access. This markup came from a
    // model, so the canvas renders it without trusting or executing any of it.
    var frame = el('iframe', 'studio-preview-frame');
    frame.setAttribute('sandbox', '');
    frame.setAttribute('title', 'Draft preview');
    previewPane.appendChild(frame);
    previewPane.appendChild(el('p', 'fine-print',
      'Previews update when the team saves the draft; the live transcript is the part ' +
      'that is continuous.'));
    canvas.appendChild(previewPane);
    layout.appendChild(canvas);

    // ----- state -----
    var activeArtifact = null;
    var renderedContent = null;
    var sessions = [];             // this user's chats for the acting client, newest first
    var watched = {};              // job id -> {after, done, status, seen}
    var typedJobs = {};            // jobs started by a typed turn: their answer belongs in the thread
    var pendingWait = null;        // the thinking bubble for the current typed turn
    var pollTimer = 0;
    var polling = false;
    var ticks = 0;
    var busy = false;
    var sending = false;           // a typed turn is between the click and its job id
    var voiceOn = false;
    var voiceWaiting = false;      // the team asked by voice and has not been answered
    var typedAnswer = false;       // the user chose to answer that question by typing
    var previewMobile = false;
    var sessionId = readStudioSession();
    // The welcome/tour voice can create the first session after this view mounted.
    // Adopt it and render its thread; our own saveSessionId calls arrive here too and
    // fall out on the equality check.
    window.addEventListener('ascend:chat-session', function (event) {
      var next = parseInt(event && event.detail, 10) || null;
      if (next === sessionId) return;
      if (!next) { saveSessionId(null); return; }
      saveSessionId(next);
      openSession(next, { quiet: true, keepCanvas: true, keepPane: true,
                          keepCanvasIfEmpty: true });
    });

    function setTab(name) {
      livePane.hidden = name !== 'live';
      previewPane.hidden = name !== 'preview';
      liveTab.className = 'studio-tab' + (name === 'live' ? ' active' : '');
      previewTab.className = 'studio-tab' + (name === 'preview' ? ' active' : '');
      tools.hidden = name !== 'preview';
    }

    function updateComposerState() {
      send.disabled = busy || (voiceWaiting && voiceOn && !typedAnswer);
      talk.disabled = busy;
      if (voiceWaiting && voiceOn) {
        answerHint.hidden = false;
        answerText.textContent = typedAnswer
          ? 'Typing the answer here continues the same thread. '
          : 'The team asked a question by voice. Answer there, or ';
        typeInstead.hidden = typedAnswer;
      } else {
        answerHint.hidden = true;
        answerText.textContent = '';
        typeInstead.hidden = true;
      }
    }

    function anyRunning() {
      return Object.keys(watched).some(function (key) {
        return !watched[key].done;
      });
    }

    function refreshBusy() {
      // Busy is "the team owes us an answer": a typed turn in flight, or any job still
      // running. Deriving it stops a finished poll from unlocking the composer while a
      // new send is still waiting for its job id.
      busy = sending || anyRunning();
      updateComposerState();
    }

    function bubble(who, text) {
      var wrap = el('div', 'asst-msg ' + who);
      wrap.appendChild(el('div', 'asst-body', text));
      thread.appendChild(wrap);
      thread.scrollTop = thread.scrollHeight;
      return wrap;
    }

    function welcome() {
      thread.innerHTML = '';
      var card = el('div', 'asst-welcome');
      card.appendChild(el('h3', null, 'What should we change?'));
      card.appendChild(el('p', 'fine-print',
        'Open a draft on the right, then say what to change. Voice and typing share ' +
        'this thread, and the team\u2019s questions arrive here and on the canvas.'));
      var chips = el('div', 'asst-suggest');
      var fallback = ['Make the headline shorter',
                      'Move the sign-up form to the top',
                      'Warmer tone, same structure'];
      function paint(items) {
        chips.innerHTML = '';
        items.forEach(function (item) {
          var text = item.action_text || item.title || String(item);
          var button = el('button', 'asst-chip', item.title || text);
          if (item.why) button.title = item.why;
          button.onclick = function () {
            box.value = text;
            box.focus();
          };
          chips.appendChild(button);
        });
      }
      paint(fallback.map(function (text) { return { title: text, action_text: text }; }));
      var timer = 0;
      function loadIdeas() {
        api('GET', '/api/recommendations?limit=3').then(function (data) {
          if (!chips.parentNode) return;
          var items = (data.recommendations || []).slice(0, 3);
          if (items.length) paint(items);
          if (data.refreshing) timer = setTimeout(loadIdeas, 3000);
        }).catch(function () { /* the local prompts still work */ });
      }
      loadIdeas();
      onCleanup(function () { if (timer) clearTimeout(timer); });
      card.appendChild(chips);
      thread.appendChild(card);
    }

    function renderMessages(messages) {
      thread.innerHTML = '';
      if (!messages.length) { welcome(); return; }
      messages.forEach(function (message) {
        bubble(message.role === 'user' ? 'me' : 'them', message.content);
      });
      thread.scrollTop = thread.scrollHeight;
    }

    function thinking() {
      var node = el('div', 'asst-msg them think');
      var dots = el('span', 'think-dots');
      dots.appendChild(el('span'));
      dots.appendChild(el('span'));
      dots.appendChild(el('span'));
      node.appendChild(dots);
      var phase = el('span', 'think-phase', 'Thinking');
      node.appendChild(phase);
      var steps = el('div', 'think-steps');
      node.appendChild(steps);
      thread.appendChild(node);
      thread.scrollTop = thread.scrollHeight;
      return {
        phase: function (text) {
          phase.textContent = text;
          thread.scrollTop = thread.scrollHeight;
        },
        step: function (text) {
          steps.appendChild(el('div', 'done', '\u2713 ' + String(text).slice(0, 60)));
          thread.scrollTop = thread.scrollHeight;
        },
        done: function () {
          if (node.parentNode) node.parentNode.removeChild(node);
        }
      };
    }

    // ----- the thread's server session -----
    function saveSessionId(value) {
      sessionId = writeStudioSession(value);
    }

    function renderSessions() {
      sessionList.innerHTML = '';
      if (!sessions.length) {
        sessionList.appendChild(el('p', 'asst-rail-empty',
          'No chats yet. Ask for something to start one.'));
        return;
      }
      sessions.forEach(function (item) {
        var button = el('button', 'asst-session' + (item.id === sessionId ? ' active' : ''));
        button.type = 'button';
        button.appendChild(el('span', 'asst-session-title', item.title || 'New chat'));
        var meta = shortTime(item.updated_at);
        if (item.message_count) {
          meta += ' \u00b7 ' + item.message_count + ' message' +
            (item.message_count === 1 ? '' : 's');
        }
        button.appendChild(el('span', 'asst-session-time', meta));
        button.onclick = function () { openSession(item.id); };
        sessionList.appendChild(button);
      });
    }

    function loadSessions() {
      return api('GET', '/api/assistant/sessions').then(function (d) {
        sessions = d.sessions || [];
        renderSessions();
        return sessions;
      }).catch(function () { return sessions; });
    }

    // The canvas belongs to the chat: opening one restores its draft, and leaving one
    // clears a page that belonged to the thread we left behind.
    function rememberDraft() {
      if (!sessionId || !activeArtifact) return;
      api('POST', '/api/assistant/sessions/' + sessionId + '/draft',
          { deliverable_id: activeArtifact.id })
        .catch(function () { /* the next turn names it again anyway */ });
    }

    function clearArtifact() {
      setArtifact(null, { keepHash: true });
      try { history.replaceState(null, '', '#/app/studio'); } catch (e) { /* blocked */ }
    }

    // Returns a promise that settles *after* the canvas decision, so the mount can ask
    // "did the chat name a page?" instead of racing a parallel request for the stored
    // draft. That race is why a refresh could show the page and then blank it.
    function openSession(target, opts) {
      opts = opts || {};
      if (busy) { toast('Let the team finish first', false); return Promise.resolve(null); }
      return api('GET', '/api/assistant/sessions/' + target).then(function (d) {
        var found = (d && d.session) || {};
        saveSessionId(found.id || target);
        renderMessages(d.messages || []);
        renderSessions();
        if (!opts.keepPane) {
          layout.setAttribute('data-pane', 'chat');
          markMobileTab('Chat');
        }
        if (opts.keepCanvas) return found;
        if (found.deliverable_id) {
          return api('GET', '/api/deliverables/' + found.deliverable_id).then(function (r) {
            if (r.deliverable) {
              setArtifact(r.deliverable, { keepHash: true, restore: true });
              setTab('preview');
            } else {
              clearArtifact();   // the draft was deleted since
            }
            return found;
          }).catch(function () { clearArtifact(); return found; });
        }
        // A chat that never named a page is not a reason to throw away the page on
        // screen: on a refresh the mount may be about to restore the last draft.
        if (!opts.keepCanvasIfEmpty) clearArtifact();
        return found;
      }).catch(function (e) {
        if (opts.quiet) {          // the remembered chat was deleted since last visit
          saveSessionId(null);
          welcome();
          return null;
        }
        toast(e.message, false);
        return null;
      });
    }

    function startNewChat() {
      if (busy) { toast('Let the team finish first', false); return; }
      // A new chat starts a new piece of work: no thread, and a blank canvas. The old
      // draft is untouched and still in the Library - it simply stops being the target.
      saveSessionId(null);
      loadSessions();
      renderMessages([]);
      clearArtifact();
      layout.setAttribute('data-pane', 'chat');
      markMobileTab('Chat');
      try { box.focus(); } catch (e) { /* not focusable yet */ }
    }

    function ensureSession() {
      var stored = readStudioSession();
      if (stored && stored !== sessionId) saveSessionId(stored);
      if (sessionId) return Promise.resolve(sessionId);
      return ensureChatSession().then(function (id) {
        if (id) saveSessionId(id);
        rememberDraft();         // a draft opened before the chat existed
        return id;
      });
    }

    // ----- the artifact on the canvas -----
    function artifactRef() {
      // The only thing the browser is allowed to name: kind and id. The server loads
      // the title and the content, and checks the draft belongs to this client. The
      // stored draft is the fallback so a voice opened before the canvas painted still
      // names the page this user was editing.
      if (activeArtifact) return { kind: 'deliverable', id: activeArtifact.id };
      return readStudioDraft();
    }

    function renderChip() {
      if (!activeArtifact) {
        chip.className = 'studio-chip empty';
        chip.textContent = 'Working on: nothing yet';
        return;
      }
      chip.className = 'studio-chip';
      chip.textContent = 'Working on: Page #' + activeArtifact.id + ' \u00b7 ' +
        (activeArtifact.title || activeArtifact.kind || 'draft');
    }

    function flashPreview() {
      previewHead.className = 'studio-preview-head studio-flash';
      setTimeout(function () { previewHead.className = 'studio-preview-head'; }, 1000);
    }

    function renderPreview(item, opts) {
      if (!item) {
        previewTitle.textContent = 'Nothing open';
        previewMeta.textContent = 'Open a draft from Pages or All content.';
        if (renderedContent !== '') {
          renderedContent = '';
          frame.srcdoc = '<p style="font-family:system-ui;padding:24px;color:#667">' +
            'Open a draft, or ask the team to build a page.</p>';
        }
        return;
      }
      previewTitle.textContent = 'Page #' + item.id + ' \u00b7 ' +
        (item.title || 'untitled');
      previewMeta.textContent = [item.kind, item.status,
        item.updated_at ? 'updated ' + String(item.updated_at).slice(0, 16) : '']
        .filter(Boolean).join(' \u00b7 ');
      var content = item.content || '';
      if (content !== renderedContent) {
        renderedContent = content;
        frame.srcdoc = content ||
          '<p style="font-family:system-ui;padding:24px;color:#667">' +
          'Nothing to preview yet.</p>';
        if (opts && opts.flash) flashPreview();
      }
    }

    function setArtifact(item, opts) {
      opts = opts || {};
      var changed = (item && item.id) !== (activeArtifact && activeArtifact.id);
      activeArtifact = item || null;
      // Remember it outside the view: the route can be torn down at any moment, and a
      // reload or a tab switch must land back on this page.
      if (changed) writeStudioDraft(activeArtifact);
      renderChip();
      renderPreview(activeArtifact, opts);
      if (activeArtifact && !opts.keepHash) {
        try {
          // replaceState, never location.hash: a hashchange re-renders the whole shell
          // and would take the thread and the docked voice panel with it.
          history.replaceState(null, '', '#/app/studio/deliverable/' + activeArtifact.id);
        } catch (e) { /* a blocked history call must not break the canvas */ }
      }
      // The chat is about whatever the canvas shows, so reopening it restores this page.
      // This runs on a restore too: opening a draft from Library and then reloading used to
      // lose the link, because only a *change* was ever recorded on the session.
      if (activeArtifact && changed) rememberDraft();
      if (opts.announce && activeArtifact) {
        toast('Draft #' + activeArtifact.id + ' is on the canvas', true);
      }
    }

    function refreshArtifact() {
      if (!activeArtifact) return;
      api('GET', '/api/deliverables/' + activeArtifact.id).then(function (d) {
        var fresh = d.deliverable;
        if (!fresh) return;
        var changed = (fresh.content || '') !== (activeArtifact.content || '');
        setArtifact(fresh, { flash: changed, restore: true });
      }).catch(function () { /* deleted or unreachable: leave what is on screen */ });
    }

    // ----- watching the run -----
    function ensurePolling() {
      if (pollTimer) return;
      pollTimer = setInterval(pollOnce, 1500);
      onCleanup(function () {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = 0; }
      });
    }

    function stopPolling() {
      if (!pollTimer) return;
      clearInterval(pollTimer);
      pollTimer = 0;
    }

    function watchJob(jobId) {
      var jobKey = Number(jobId);
      if (!jobKey) return;
      if (!watched[jobKey]) {
        watched[jobKey] = { after: 0, done: false, status: 'queued', seen: {} };
      }
      setTab('live');
      ensurePolling();
      renderWatched();
      pollOnce();
    }

    function renderWatched() {
      var ids = Object.keys(watched);
      if (!ids.length) {
        liveSummary.textContent =
          'No job is running. Ask for a change and it will appear here.';
        return;
      }
      liveSummary.textContent = 'Following ' + ids.map(function (key) {
        return '#' + key + ' ' + (watched[key].status || 'queued');
      }).join(' \u00b7 ');
    }

    function pollJob(jobId) {
      var watch = watched[jobId];
      if (!watch || watch.done) return Promise.resolve();
      return api('GET', '/api/jobs/' + jobId + '/events?after=' + watch.after)
        .then(function (d) {
          (d.events || []).forEach(function (ev) {
            watch.after = Math.max(watch.after, Number(ev.seq) || 0);
            handleEvent(jobId, ev);
          });
          var job = d.job || {};
          if (job.status) watch.status = String(job.status);
          if (['done', 'failed', 'cancelled'].indexOf(job.status) === -1) return;
          watch.done = true;
          if (job.status === 'done') {
            // The work saved; the page is what the user wants to see next - unless the
            // team asked a question, in which case the card on the Live tab is the thing
            // they need in front of them.
            if (activeArtifact && !watch.askedQuestion) setTab('preview');
          } else {
            appendLive(jobId, {
              source: 'system', type: 'error',
              content: job.error || 'The job stopped before it finished.'
            });
          }
          // A run writes its drafts itself; look for them by job id, never by title.
          refreshOutputs(jobId);
          refreshArtifact();
          if (pendingWait && typedJobs[jobId]) {
            pendingWait.done();
            pendingWait = null;
          }
        })
        .catch(function () { /* one failed poll is not a reason to tear the canvas down */ });
    }

    function pollOnce() {
      if (polling) return;
      var ids = Object.keys(watched);
      if (!ids.length) { stopPolling(); return; }
      polling = true;
      ticks += 1;
      var chain = Promise.resolve();
      ids.forEach(function (key) {
        chain = chain.then(function () { return pollJob(Number(key)); });
      });
      chain.then(function () {
        polling = false;
        renderWatched();
        if (!anyRunning()) stopPolling();
        refreshBusy();
        // A save can land mid-run: re-read the open draft so an in-place edit shows up
        // while the team is still working, not only when the job finishes.
        if (activeArtifact && anyRunning()) refreshArtifact();
      }).catch(function () { polling = false; });
      if (ticks % 2 === 0) refreshApprovals();
    }

    function appendLive(jobId, ev) {
      var row = el('div', 'studio-event type-' + (ev.type || 'system'));
      row.appendChild(el('span', 'studio-event-head',
        '#' + jobId + ' \u00b7 ' + (ev.source || 'system') + ' \u00b7 ' + (ev.type || '')));
      row.appendChild(el('div', 'studio-event-body', String(ev.content || '').slice(0, 2000)));
      liveLog.appendChild(row);
      while (liveLog.children.length > 250) liveLog.removeChild(liveLog.firstChild);
      liveLog.scrollTop = liveLog.scrollHeight;
    }

    function handleEvent(jobId, ev) {
      appendLive(jobId, ev);
      if (ev.source === 'Manager' && ev.type === 'tool' && pendingWait) {
        pendingWait.step(String(ev.content || '').replace(/_/g, ' '));
      }
      if (ev.source === 'Manager' && ev.type === 'message' && typedJobs[jobId] && ev.content) {
        if (pendingWait) { pendingWait.done(); pendingWait = null; }
        bubble('them', ev.content);
      }
      if (ev.source === 'Manager' && ev.type === 'child_job') {
        try {
          var link = JSON.parse(ev.content || '{}');
          if (link.job_id) watchJob(link.job_id);
        } catch (e) { /* a malformed link is not worth a toast */ }
      }
      if (ev.source === 'Manager' && ev.type === 'question') {
        var questions = [];
        try { questions = JSON.parse(ev.content || '[]'); } catch (e) { questions = []; }
        if (watched[jobId]) watched[jobId].askedQuestion = true;
        if (pendingWait) { pendingWait.done(); pendingWait = null; }
        if (questions.length) showQuestions(questions);
        // A spoken session owns the answer: the composer steps aside until the user
        // either answers out loud or chooses to type it. Only the Manager asks, and it
        // asks on the job the Studio is following.
        voiceWaiting = voiceOn;
        typedAnswer = false;
        updateComposerState();
      }
      if (ev.source === 'Manager' && ev.type === 'build') {
        var info = {};
        try { info = JSON.parse(ev.content || '{}'); } catch (e) { info = {}; }
        if (info.build_id) showBuildCard(thread, info.build_id);
      }
      if (ev.type === 'tool_result' &&
          String(ev.content || '').indexOf('save_deliverable') === 0) {
        refreshOutputs(jobId);
      }
    }

    function refreshOutputs(jobId) {
      api('GET', '/api/deliverables?job_id=' + jobId).then(function (d) {
        var rows = d.deliverables || [];
        if (!rows.length) return;
        var watch = watched[jobId];
        rows.forEach(function (row) {
          var isOpen = activeArtifact && activeArtifact.id === row.id;
          var seen = watch && watch.seen[row.id];
          if (watch) watch.seen[row.id] = true;
          if (!isOpen) {
            if (seen) return;
            // The user asked for a different page, or the run produced its first one:
            // the canvas follows the work instead of asking which draft is meant.
            setArtifact(row, { announce: true });
            setTab('preview');
          } else {
            // Refreshing the open draft on every save is the point of the canvas.
            setArtifact(row, { flash: true });
          }
        });
      }).catch(function () { /* a draft that cannot be listed yet is not an error */ });
    }

    function showQuestions(questions) {
      var card = el('div', 'panel-card studio-question');
      card.appendChild(el('strong', null, 'The team needs a little more'));
      var inputs = questions.map(function (question) {
        card.appendChild(el('p', 'fine-print', String(question)));
        var input = el('input');
        card.appendChild(input);
        return input;
      });
      var reply = el('button', 'btn btn-primary btn-sm', 'Send answers');
      reply.onclick = function () {
        var parts = inputs.map(function (input, index) {
          var text = (input.value || '').trim();
          return text ? questions[index] + ' ' + text : '';
        }).filter(function (part) { return part; });
        if (!parts.length) { toast('Answer at least one question', false); return; }
        if (card.parentNode) card.parentNode.removeChild(card);
        voiceWaiting = false;
        typedAnswer = false;
        updateComposerState();
        box.value = parts.join(' ');
        sendTurn();
      };
      card.appendChild(reply);
      liveLog.appendChild(card);
      liveLog.scrollTop = liveLog.scrollHeight;
    }

    function refreshApprovals() {
      var ids = Object.keys(watched).map(Number);
      if (!ids.length) return;
      api('GET', '/api/approvals?status=pending').then(function (d) {
        approvalsHost.innerHTML = '';
        if (d.dry_run) return;
        var rows = (d.approvals || []).filter(function (approval) {
          return approval.job_id == null || ids.indexOf(Number(approval.job_id)) !== -1;
        });
        if (!rows.length) return;
        var card = el('div', 'panel-card');
        card.appendChild(el('strong', null, 'Waiting for your approval'));
        rows.forEach(function (approval) {
          var row = el('div', 'approval-row');
          row.appendChild(el('div', null, approval.tool + ' - ' + approval.summary));
          var buttons = el('div', 'card-actions');
          var yes = el('button', 'btn btn-primary btn-sm', 'Approve');
          yes.onclick = function () { decide(approval.id, 'approve'); };
          var no = el('button', 'btn btn-danger btn-sm', 'Deny');
          no.onclick = function () { decide(approval.id, 'deny'); };
          buttons.appendChild(yes);
          buttons.appendChild(no);
          row.appendChild(buttons);
          card.appendChild(row);
        });
        approvalsHost.appendChild(card);
      }).catch(function () { /* approvals are best-effort on this screen */ });
    }

    function decide(approvalId, decision) {
      api('POST', '/api/approvals/' + approvalId + '/' + decision)
        .then(function () { toast('Recorded', true); refreshApprovals(); })
        .catch(function (e) { toast(e.message, false); });
    }

    function sendTurn() {
      var text = (box.value || '').trim();
      if (!text) { toast('Say what you want first', false); return; }
      if (busy) return;
      if (voiceWaiting && voiceOn && !typedAnswer) {
        // The voice owns the answer; the hint's button is the one-way door out of that.
        toast('The team asked by voice - answer there, or press Type the answer.', false);
        return;
      }
      box.value = '';
      box.style.height = 'auto';
      var hello = thread.querySelector('.asst-welcome');
      if (hello && hello.parentNode) hello.parentNode.removeChild(hello);
      bubble('me', text);
      sending = true;
      refreshBusy();
      voiceWaiting = false;
      typedAnswer = false;
      updateComposerState();
      pendingWait = thinking();
      var active = sessionId || readStudioSession();
      if (active && active !== sessionId) saveSessionId(active);
      var payload = { message: text };
      if (active) payload.session_id = active;
      else payload.create_session = true;
      var artifact = artifactRef();
      if (artifact) payload.artifact = artifact;
      function postTurn(data, retried) {
        return api('POST', '/api/manager/chat', data).then(function (d) {
          if (d.session_id) saveSessionId(d.session_id);
          typedJobs[d.job_id] = true;
          if (pendingWait) pendingWait.phase('Reading the draft\u2026');
          watchJob(d.job_id);
          loadSessions();          // a first turn titles the chat and puts it in the rail
          sending = false;
          refreshBusy();
        }).catch(function (e) {
          if (!retried && e.status === 404 && e.message === 'That chat was not found') {
            // The chat was deleted in another tab or by a workspace cleanup. A new
            // session keeps this turn instead of losing the thread on a dead id.
            saveSessionId(null);
            return ensureChatSession().then(function (id) {
              if (id) data.session_id = id;
              else { delete data.session_id; data.create_session = true; }
              return postTurn(data, true);
            });
          }
          throw e;
        });
      }
      postTurn(payload, false).catch(function (e) {
        sending = false;
        if (pendingWait) { pendingWait.done(); pendingWait = null; }
        bubble('them', e.message);
        refreshBusy();
      });
    }

    function pickArtifact() {
      api('GET', '/api/deliverables').then(function (d) {
        var rows = (d.deliverables || []).slice(0, 60);
        if (!rows.length) { toast('No drafts yet - ask the team for one', false); return; }
        var options = rows.map(function (row) {
          return {
            value: String(row.id),
            label: '#' + row.id + ' \u00b7 ' + (row.title || 'untitled') +
                   ' (' + row.kind + ', ' + row.status + ')'
          };
        });
        formModal('Open a draft in the Studio', [
          { name: 'draft', label: 'Draft', type: 'select', options: options,
            value: activeArtifact ? String(activeArtifact.id) : options[0].value }
        ], function (data, fail, ok) {
          var chosen = rows.filter(function (row) {
            return String(row.id) === String(data.draft);
          })[0];
          if (!chosen) { fail('Choose a draft'); return; }
          ok();
          setArtifact(chosen, { announce: true });
          setTab('preview');
        });
      }).catch(function (e) { toast(e.message, false); });
    }

    chip.onclick = pickArtifact;
    pick.onclick = pickArtifact;
    newChat.onclick = startNewChat;
    liveTab.onclick = function () { setTab('live'); };
    previewTab.onclick = function () { setTab('preview'); };
    widthToggle.onclick = function () {
      previewMobile = !previewMobile;
      previewPane.className = 'studio-preview' + (previewMobile ? ' mobile' : '');
      widthToggle.textContent = previewMobile ? 'Desktop' : 'Mobile';
    };
    refresh.onclick = function () { refreshArtifact(); };
    openFull.onclick = function () {
      if (!activeArtifact) { toast('Open a draft first', false); return; }
      previewDeliverable(activeArtifact);
    };
    send.onclick = sendTurn;

    box.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendTurn();
      }
    });
    box.addEventListener('input', function () {
      box.style.height = 'auto';
      box.style.height = Math.min(160, box.scrollHeight || 48) + 'px';
    });

    // ----- voice, docked into the left pane -----
    talk.onclick = function () {
      if (!window.AscendVoice) { toast('The voice client did not load.', false); return; }
      if (window.AscendVoice.running()) { window.AscendVoice.restore(); return; }
      if (busy) return;
      // The voice is the same thread as typing: it must start from the active session,
      // not a fresh Manager prompt. The voice client persists each final turn into it.
      ensureSession().then(function (studioSession) {
        if (!studioSession) { toast('The chat could not be started.', false); return; }
        voiceHost.hidden = false;
        window.AscendVoice.open({
          page: 'studio',
          source: 'studio',
          host: voiceHost,
          compact: true,
          sessionId: studioSession,
          onSession: function (id) { saveSessionId(id); },
          // Read by the voice when it delegates, so "make it warmer" means the page on
          // screen even if the user picked another draft mid-sentence.
          getContext: artifactRef,
          onState: function (phase, label) {
            voiceOn = ['connecting', 'listening', 'thinking', 'speaking'].indexOf(phase) !== -1;
            voiceNote.textContent = voiceOn ? 'Voice: ' + label : '';
            if (!voiceOn) { voiceWaiting = false; typedAnswer = false; }
            updateComposerState();
          },
          onTurn: function (turn) {
            bubble(turn.role === 'user' ? 'me' : 'them', turn.text);
            loadSessions();       // the voice client saved the turn to the session
          },
          onJob: function (jobId) {
            voiceWaiting = false;
            typedAnswer = false;
            watchJob(jobId);
          },
          onClose: function () {
            voiceOn = false;
            voiceWaiting = false;
            typedAnswer = false;
            voiceHost.hidden = true;
            voiceNote.textContent = '';
            updateComposerState();
          }
        });
      });
    };

    api('GET', '/api/voice/status').then(function (s) {
      if (!s.available) {
        voiceNote.textContent = s.reason;
        return;
      }
      talk.style.display = '';
      voiceNote.textContent = (s.voice ? 'Voice: ' + s.voice + ' \u00b7 '
                                       : 'Voice ready \u00b7 ') +
        s.remaining_minutes + ' min left today';
    }).catch(function () { /* no voice is not an error worth a toast */ });

    onCleanup(function () {
      stopPolling();
      if (window.AscendVoice && window.AscendVoice.phase &&
          window.AscendVoice.phase() !== 'idle') {
        window.AscendVoice.stop();
      }
    });

    // ----- first paint -----
    renderChip();
    renderPreview(null);
    renderWatched();
    renderSessions();
    setTab('live');
    updateComposerState();
    var sessionsReady = loadSessions();

    // A deep link to a draft or a job owns the canvas; restoring the last chat must not
    // pull a different page over it.
    var deepLinked = (kind === 'deliverable' && id) || (kind === 'job' && id);

    function restoreStoredDraft() {
      var remembered = readStudioDraft();
      if (!remembered) return Promise.resolve(false);
      return api('GET', '/api/deliverables/' + remembered.id).then(function (d) {
        if (!d.deliverable) throw new Error('gone');
        setArtifact(d.deliverable, { restore: true });
        setTab('preview');
        return true;
      }).catch(function () { writeStudioDraft(null); return false; });
    }

    // Order: deep link -> the chat's own draft -> this browser's last draft -> the newest
    // chat for this client. The last two are what make a refresh land where it was: a chat
    // that named no page no longer clears the canvas, and a stored chat that has since
    // gone falls back to the client's most recent one instead of a blank thread.
    function openChatForMount() {
      if (sessionId) {
        return openSession(sessionId, { keepCanvas: !!deepLinked, keepPane: true,
                                        quiet: true, keepCanvasIfEmpty: true });
      }
      welcome();
      return Promise.resolve(null);
    }

    if (kind === 'deliverable' && id) {
      api('GET', '/api/deliverables/' + encodeURIComponent(id)).then(function (d) {
        if (!d.deliverable) throw new Error('That draft was not found.');
        setArtifact(d.deliverable, { keepHash: true, restore: true });
        setTab('preview');
        layout.setAttribute('data-pane', 'canvas');
        markMobileTab('Preview');
      }).catch(function (e) { toast(e.message, false); });
    } else if (kind === 'job' && id) {
      layout.setAttribute('data-pane', 'canvas');
      markMobileTab('Live');
      watchJob(Number(id));
    }

    openChatForMount().then(function (opened) {
      if (opened) return null;
      return sessionsReady.then(function (list) {
        var newest = (list || [])[0];
        if (!newest || String(newest.id) === String(sessionId)) return null;
        return openSession(newest.id, { keepCanvas: !!deepLinked, keepPane: true,
                                        quiet: true, keepCanvasIfEmpty: true });
      });
    }).then(function () {
      if (deepLinked || activeArtifact) return null;
      return restoreStoredDraft();
    });

    return container;
  }


  // ---------- Usage: what the team spent, and on what ----------
  // The client's own meter. The operator's cross-client screen is separate, because it
  // owns the rate card and the credits themselves.
  function usageKind(kind) {
    var labels = { model: 'Assistant', voice: 'Voice', image: 'Image' };
    return labels[kind] || kind || 'Activity';
  }

  function viewUsage() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    var owner = isOwner(state.user);
    // Credits only. The dollar price of a credit is the operator's business, and it is
    // deliberately absent from this screen and from a client's payload. Technical
    // token/model detail is operator-only too; a client sees what the work cost.
    head.innerHTML = owner
      ? '<div><h2>Usage</h2><p class="muted">What the team has used, in credits. ' +
        'Cached input costs less than fresh input, so a cached prompt is cheaper to run.' +
        '</p></div>'
      : '<div><h2>Credits used</h2><p class="muted">What your team has used this month, ' +
        'in credits.</p></div>';
    var range = el('select');
    range.innerHTML = '<option value="7">Last 7 days</option>' +
      '<option value="30" selected>Last 30 days</option>' +
      '<option value="90">Last 90 days</option>';
    head.appendChild(range);
    container.appendChild(head);
    var body = el('div');
    container.appendChild(body);

    function load() {
      api('GET', '/api/usage?days=' + encodeURIComponent(range.value)).then(function (d) {
        body.innerHTML = '';
        var card = el('div', 'panel usage-head');
        card.appendChild(el('div', 'usage-balance',
          d.balance === null || d.balance === undefined
            ? 'No credits yet' : fmtCredits(d.balance) + ' credits'));
        card.appendChild(el('p', 'muted', [
          'Plan ' + (d.plan || 'standard'),
          d.allowance ? fmtCredits(d.allowance) + ' credits a month' : 'no monthly allowance'
        ].concat(owner ? ['enforcement: ' + (d.enforce || 'shadow')] : []).join(' \u00b7 ')));
        if (d.low) {
          card.appendChild(el('p', 'form-error',
            'Low balance. Once enforcement is on, new work is refused at zero.'));
        }
        body.appendChild(card);

        if (owner && (d.missing_rates || []).length) {
          var warn = el('div', 'panel');
          warn.appendChild(el('h3', null, 'Unpriced models'));
          warn.appendChild(el('p', 'muted', 'These calls are recorded but not priced. ' +
            'An operator adds them in Platform \u2192 Usage \u2192 Rate card.'));
          d.missing_rates.slice(0, 8).forEach(function (row) {
            warn.appendChild(el('div', 'fine-print',
              (row.provider || '?') + ' / ' +
              (row.model_returned || row.model_configured || '?') +
              ' \u00b7 ' + (row.calls || 0) + ' call(s)'));
          });
          body.appendChild(warn);
        }

        var summary = el('div', 'panel');
        summary.appendChild(el('h3', null,
          owner ? 'Spend by model and agent' : 'Where your credits went'));
        var rows = d.rows || [];
        if (!rows.length) {
          summary.appendChild(el('p', 'muted', 'Nothing has been spent in this window.'));
        } else {
          // A client payload has no provider or model (stripped server-side); the
          // operator does. The client table is deliberately shorter: activity, team,
          // calls and credits only.
          var named = owner && rows.some(function (row) { return !!(row.provider || row.model); });
          var table = el('table', 'table');
          table.innerHTML = owner
            ? '<thead><tr><th>Where</th><th>Agent</th><th>Calls</th>' +
              '<th>Input (cached)</th><th>Output</th><th>Credits</th></tr></thead>'
            : '<thead><tr><th>Activity</th><th>Team</th><th>Calls</th>' +
              '<th>Credits</th></tr></thead>';
          var tbody = el('tbody');
          rows.forEach(function (row) {
            var tr = el('tr');
            var where = '<td><span class="badge">' + esc(usageKind(row.kind)) + '</span>' +
              (named ? ' ' + esc(row.provider || '') +
                (row.model ? ' \u00b7 ' + esc(row.model) : '') : '') + '</td>';
            var team = '<td>' + esc(row.label || '-') + '</td>' +
              '<td>' + esc(row.calls || 0) + '</td>';
            var credits = '<td>' + (row.unpriced && owner
              ? '<span class="badge badge-queued">unpriced</span> '
              : '') + esc(fmtCredits(row.credits)) + '</td>';
            tr.innerHTML = owner
              ? where + team +
                '<td>' + esc(row.input_uncached || 0) +
                (row.input_cached ? ' <span class="muted">(' + esc(row.input_cached) +
                  ' cached)</span>' : '') + '</td>' +
                '<td>' + esc(row.output_tokens || 0) + '</td>' + credits
              : where + team + credits;
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          summary.appendChild(table);
        }
        body.appendChild(summary);

        var recent = el('div', 'panel');
        recent.appendChild(el('h3', null, owner ? 'Recent calls' : 'Recent activity'));
        var items = d.recent || [];
        if (!items.length) {
          recent.appendChild(el('p', 'muted', 'No activity yet.'));
        } else {
          var showModel = owner && items.some(function (item) {
            return !!(item.model_returned || item.model_configured);
          });
          var list = el('table', 'table');
          list.innerHTML = owner
            ? '<thead><tr><th>When</th><th>Kind</th>' +
              (showModel ? '<th>Model</th>' : '') +
              '<th>Agent</th><th>Credits</th></tr></thead>'
            : '<thead><tr><th>When</th><th>Activity</th><th>Team</th>' +
              '<th>Credits</th></tr></thead>';
          var listBody = el('tbody');
          items.forEach(function (item) {
            var tr = el('tr');
            var when = '<td class="muted">' +
              esc((item.created_at || '').slice(0, 16)) + '</td>';
            var credits = '<td>' + (item.credits_milli === null && owner
              ? '<span class="badge badge-queued">unpriced</span>'
              : esc(fmtCredits((item.credits_milli || 0) / 1000))) + '</td>';
            tr.innerHTML = when +
              '<td>' + esc(usageKind(item.kind)) + '</td>' +
              (showModel ? '<td>' + esc(item.model_returned || item.model_configured || '-') +
                '</td>' : '') +
              '<td>' + esc(item.label || '-') + '</td>' + credits;
            listBody.appendChild(tr);
          });
          list.appendChild(listBody);
          recent.appendChild(list);
        }
        body.appendChild(recent);
      }).catch(function (e) {
        body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
      });
    }
    range.onchange = load;
    load();
    return container;
  }

  function grantCreditsModal(org, onSaved) {
    formModal('Add credits to ' + org.name, [
      { name: 'credits', label: 'Credits', help: 'Negative corrects a balance.' },
      { name: 'note', label: 'Note', placeholder: 'why' }
    ], function (data, fail, ok) {
      api('POST', '/api/orgs/' + org.id + '/credits', {
        credits: data.credits, note: data.note
      }).then(function (res) {
        ok();
        toast('Balance now ' + fmtCredits(res.balance) + ' credits', true);
        if (onSaved) onSaved(res);
      }).catch(function (e) { fail(e.message); });
    });
  }


  // ---------- Platform usage: every client's balance, and the price list ----------
  function viewPlatformUsage() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Usage</h2><p class="muted">Every client\u2019s balance, ' +
      'the rate card in dollars, and the models nobody has priced yet.</p></div>';
    var grant = el('button', 'btn btn-ghost', 'Grant monthly now');
    grant.onclick = function () {
      grant.disabled = true;
      api('POST', '/api/usage/grant-monthly', {}).then(function (d) {
        toast('Granted ' + d.granted + ' client(s)', true);
        load();
      }).catch(function (e) { toast(e.message, false); })
        .finally(function () { grant.disabled = false; });
    };
    head.appendChild(grant);
    container.appendChild(head);
    var body = el('div');
    container.appendChild(body);

    function reconForm() {
      formModal('Reconcile a month', [
        { name: 'provider', label: 'Provider', placeholder: 'deepseek' },
        { name: 'period', label: 'Month', placeholder: '2026-11',
          value: new Date().toISOString().slice(0, 7) },
        { name: 'billed_usd', label: 'Billed ($)', placeholder: '12.34' },
        { name: 'note', label: 'Note', placeholder: 'invoice number' }
      ], function (data, fail, ok) {
        api('POST', '/api/usage/reconciliation', data).then(function (res) {
          ok();
          var row = res.reconciliation;
          toast('We computed $' + Number(row.computed_usd || 0).toFixed(2) +
                ' against $' + Number(row.billed_usd || 0).toFixed(2) + ' billed', true);
          load();
        }).catch(function (e) { fail(e.message); });
      });
    }

    function rateForm(rate) {
      rate = rate || {};
      formModal(rate.id ? 'Edit rate' : 'Add a rate', [
        { name: 'provider', label: 'Provider', value: rate.provider || '',
          placeholder: 'deepseek' },
        { name: 'model', label: 'Model', value: rate.model || '',
          placeholder: 'deepseek-chat' },
        { name: 'kind', label: 'Kind', type: 'select',
          options: ['model', 'voice', 'image'], value: rate.kind || 'model' },
        { name: 'input_per_1m', label: 'Input ($ / 1M tokens)',
          value: rate.input_per_1m || 0 },
        { name: 'cached_input_per_1m', label: 'Cached input ($ / 1M)',
          value: rate.cached_input_per_1m || 0 },
        { name: 'cache_write_per_1m', label: 'Cache write ($ / 1M)',
          value: rate.cache_write_per_1m || 0 },
        { name: 'output_per_1m', label: 'Output ($ / 1M)',
          value: rate.output_per_1m || 0 },
        { name: 'per_unit', label: 'Per minute / per image ($)',
          value: rate.per_unit || 0 },
        { name: 'note', label: 'Note', value: rate.note || '',
          placeholder: 'list price 2026-11' }
      ], function (data, fail, ok) {
        api('POST', '/api/usage/rates', data).then(function () {
          ok(); toast('Rate saved', true); load();
        }).catch(function (e) { fail(e.message); });
      });
    }

    function load() {
      api('GET', '/api/usage?days=30').then(function (d) {
        body.innerHTML = '';
        // One price, stated once. The rate card below stays in provider cost, so
        // reconciliation never carries the markup and this line cannot disagree with it.
        var price = Number(d.credit_usd || 0);
        if (price > 0) {
          var priceCard = el('div', 'panel');
          priceCard.appendChild(el('h3', null, 'Credit price'));
          priceCard.appendChild(el('p', 'muted',
            '1 credit = $' + String(price) + ' · ' + fmtCredits(d.markup_percent) +
            '% markup · ' + fmtCredits(d.margin_percent) + '% margin. Clients see ' +
            'credits only; the rate card below is what the provider charges us.'));
          body.appendChild(priceCard);
        }
        var clients = el('div', 'panel');
        clients.appendChild(el('h3', null, 'Clients'));
        var orgs = d.orgs || [];
        if (!orgs.length) {
          clients.appendChild(el('p', 'muted', 'No clients yet.'));
        } else {
          var table = el('table', 'table');
          table.innerHTML = '<thead><tr><th>Client</th><th>Plan</th><th>Allowance</th>' +
            '<th>Balance</th><th>Value</th><th></th></tr></thead>';
          var tbody = el('tbody');
          orgs.forEach(function (org) {
            var tr = el('tr');
            var balance = org.balance === null || org.balance === undefined
              ? '-' : fmtCredits(org.balance);
            tr.innerHTML = '<td>' + esc(org.name) +
              '<div class="fine-print">' + esc(org.status) + '</div></td>' +
              '<td><span class="badge">' + esc(org.plan) + '</span></td>' +
              '<td>' + (org.allowance ? esc(fmtCredits(org.allowance)) : '-') + '</td>' +
              '<td>' + esc(balance) + '</td>' +
              '<td>' + (org.balance === null || org.balance === undefined ? '-' :
                '$' + (Number(org.balance) * price).toFixed(2)) + '</td>';
            var td = el('td', 'row-actions');
            var add = el('button', 'btn btn-ghost btn-sm', 'Credits');
            add.onclick = function () { grantCreditsModal(org, load); };
            td.appendChild(add);
            tr.appendChild(td);
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          clients.appendChild(table);
        }
        body.appendChild(clients);

        // The invoice check. The provider bills one number for a month; we compute the
        // same month from the snapshot on every call. Variance is ours minus theirs, so
        // a negative red row means the meter is charging less than the bill.
        var recon = el('div', 'panel');
        var reconBar = el('div', 'panel-head');
        reconBar.appendChild(el('h3', null, 'Reconcile against the invoice'));
        var addRecon = el('button', 'btn btn-ghost btn-sm', 'Check a month');
        addRecon.onclick = function () { reconForm(); };
        reconBar.appendChild(addRecon);
        recon.appendChild(reconBar);
        recon.appendChild(el('p', 'fine-print',
          'Provider, month and the total from their dashboard. A positive variance means ' +
          'the meter charges more than the invoice; a negative one means it charges less.'));
        body.appendChild(recon);
        api('GET', '/api/usage/reconciliation').then(function (r) {
          var rows = r.rows || [];
          if (!rows.length) {
            recon.appendChild(el('p', 'muted',
              'No month checked yet. Until one is, the meter has only been read by us.'));
            return;
          }
          var rt = el('table', 'table');
          rt.innerHTML = '<thead><tr><th>Checked</th><th>Provider</th><th>Month</th>' +
            '<th>Billed</th><th>We computed</th><th>Variance</th><th>Note</th></tr></thead>';
          var rtb = el('tbody');
          rows.forEach(function (row) {
            var tr = el('tr');
            var short = Number(row.variance_usd || 0) < 0;
            tr.innerHTML = '<td class="muted">' +
              esc((row.created_at || '').slice(0, 16)) + '</td>' +
              '<td>' + esc(row.provider) + '</td>' +
              '<td>' + esc(row.period) + '</td>' +
              '<td>$' + esc(Number(row.billed_usd || 0).toFixed(2)) + '</td>' +
              '<td>$' + esc(Number(row.computed_usd || 0).toFixed(2)) + '</td>' +
              '<td class="' + (short ? 'form-error' : '') + '">$' +
              esc(Number(row.variance_usd || 0).toFixed(2)) +
              (row.variance_pct === null || row.variance_pct === undefined
                ? '' : ' <span class="muted">(' + esc(row.variance_pct) + '%)</span>') +
              '</td>' +
              '<td class="muted">' + esc(row.note || '') + '</td>';
            rtb.appendChild(tr);
          });
          rt.appendChild(rtb);
          recon.appendChild(rt);
        }).catch(function (e) {
          recon.appendChild(el('p', 'form-error', esc(e.message)));
        });

        var ratesCard = el('div', 'panel');
        var bar = el('div', 'panel-head');
        bar.appendChild(el('h3', null, 'Rate card (dollars)'));
        var addRate = el('button', 'btn btn-primary btn-sm', '+ Add rate');
        addRate.onclick = function () { rateForm(null); };
        bar.appendChild(addRate);
        ratesCard.appendChild(bar);
        ratesCard.appendChild(el('p', 'fine-print',
          'Copy the numbers from the provider dashboard. Input, cached input, cache ' +
          'write and output are per million tokens; the unit is per minute or per image.'));
        body.appendChild(ratesCard);
        api('GET', '/api/usage/rates').then(function (r) {
          var rows = r.rates || [];
          if (!rows.length) {
            ratesCard.appendChild(el('p', 'muted',
              'No rates yet. Add one for every model in use, or its calls stay unpriced.'));
          } else {
            var rt = el('table', 'table');
            rt.innerHTML = '<thead><tr><th>Provider</th><th>Model</th><th>Kind</th>' +
              '<th>In / cached / write / out ($ per 1M)</th><th>Unit ($)</th><th></th></tr></thead>';
            var rtb = el('tbody');
            rows.forEach(function (row) {
              var tr = el('tr');
              tr.innerHTML = '<td>' + esc(row.provider) + '</td>' +
                '<td>' + esc(row.model) + '</td>' +
                '<td><span class="badge">' + esc(row.kind) + '</span></td>' +
                '<td class="muted">' + esc(row.input_per_1m) + ' / ' +
                esc(row.cached_input_per_1m) + ' / ' + esc(row.cache_write_per_1m) +
                ' / ' + esc(row.output_per_1m) + '</td>' +
                '<td>' + esc(row.per_unit) + '</td>';
              var td = el('td', 'row-actions');
              var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
              edit.onclick = function () { rateForm(row); };
              var del = el('button', 'btn btn-danger btn-sm', 'Delete');
              del.onclick = function () {
                if (!confirm('Delete this rate?')) return;
                api('DELETE', '/api/usage/rates/' + row.id).then(function () {
                  toast('Deleted', true); load();
                }).catch(function (e) { toast(e.message, false); });
              };
              td.appendChild(edit);
              td.appendChild(del);
              tr.appendChild(td);
              rtb.appendChild(tr);
            });
            rt.appendChild(rtb);
            ratesCard.appendChild(rt);
          }
          var missing = r.missing || [];
          if (missing.length) {
            ratesCard.appendChild(el('p', 'form-error', 'Unpriced models in use: ' +
              missing.map(function (item) {
                return (item.provider || '?') + '/' +
                  (item.model_returned || item.model_configured || '?');
              }).join(', ')));
          }
        }).catch(function (e) {
          ratesCard.appendChild(el('p', 'form-error', esc(e.message)));
        });
      }).catch(function (e) {
        body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
      });
    }

    load();
    return container;
  }

  // ---------- Contacts: the mini CRM ----------
  // Deliberately no GoHighLevel anywhere on this screen: a client without one gets the
  // same product, and the form endpoint is the thing most people come here for.
  function viewContacts() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Contacts</h2><p class="muted">Leads captured by a form, ' +
      'the widget, a webhook or by hand.</p></div>';
    var add = el('button', 'btn btn-primary', '+ New contact');
    var canSync = canAdmin(state.user);
    var sync = null;
    if (canSync) {
      sync = el('button', 'btn btn-ghost', 'Sync with AscendPlatform');
      head.appendChild(sync);
    }
    head.appendChild(add);
    container.appendChild(head);

    // The sync result belongs beside the list, not in a toast that disappears before
    // somebody can read a conflict. It starts hidden and is filled when a run finishes.
    var syncBox = el('div', 'panel-card');
    syncBox.hidden = true;
    container.appendChild(syncBox);

    var capture = el('div', 'panel-card');
    container.appendChild(capture);
    api('GET', '/api/contacts/lead-token').then(function (d) {
      capture.innerHTML = '<h3 class="card-title">Form endpoint</h3>' +
        '<p class="fine-print">POST JSON here and the contact appears below. Safe to paste ' +
        'into a page: it can add a contact and read nothing.</p>';
      capture.appendChild(el('code', null, d.url));
      var copy = el('button', 'btn btn-ghost btn-sm', 'Copy URL');
      copy.onclick = function () {
        navigator.clipboard.writeText(d.url).then(function () {
          copy.textContent = 'Copied';
          setTimeout(function () { copy.textContent = 'Copy URL'; }, 1200);
        }).catch(function () { toast('Copy failed', false); });
      };
      capture.appendChild(copy);
      if (d.snippet) {
        capture.appendChild(el('p', 'fine-print', 'Or paste a ready-made form into an ' +
          'existing page - it posts to the URL above and hides a honeypot field from bots.'));
        var code = el('pre', 'code-block', d.snippet);
        code.style.whiteSpace = 'pre-wrap';
        code.style.maxHeight = '220px';
        code.style.overflow = 'auto';
        capture.appendChild(code);
        var copyForm = el('button', 'btn btn-ghost btn-sm', 'Copy form');
        copyForm.onclick = function () {
          navigator.clipboard.writeText(d.snippet).then(function () {
            copyForm.textContent = 'Copied';
            setTimeout(function () { copyForm.textContent = 'Copy form'; }, 1200);
          }).catch(function () { toast('Copy failed', false); });
        };
        capture.appendChild(copyForm);
      }
    }).catch(function () { capture.style.display = 'none'; });

    var body = el('div', 'mt');
    container.appendChild(body);

    var shown = 200;      // grows with Load more; the server caps one request at 500
    function refresh() {
      api('GET', '/api/contacts?limit=' + shown).then(function (d) {
        body.innerHTML = '';
        var rows = d.contacts || [];
        if (!rows.length) {
          body.innerHTML = '<div class="empty-state">' +
            '<div class="big">' + icon('user') + '</div>' +
            '<strong>No contacts yet</strong>' +
            '<p>Post a form to the endpoint above, or add one by hand.</p></div>';
          return;
        }
        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Name</th><th>Email</th><th>Source</th>' +
          '<th>Status</th><th>Added</th></tr></thead>';
        var tbody = el('tbody');
        rows.forEach(function (row) {
          var tr = el('tr');
          tr.innerHTML = '<td>' + esc(row.name || '—') + '</td>' +
            '<td class="muted">' + esc(row.email || '—') + '</td>' +
            '<td><span class="badge">' + esc(row.source) + '</span></td>' +
            '<td><span class="badge">' + esc(row.status) + '</span></td>' +
            '<td class="muted">' + esc(String(row.created_at || '').slice(0, 16)) + '</td>';
          tr.style.cursor = 'pointer';
          tr.title = 'Edit status, tags and notes';
          tr.onclick = function () {
            formModal('Edit ' + (row.name || row.email || 'contact'), [
              { name: 'status', label: 'Status', type: 'select',
                options: ['new', 'contacted', 'qualified', 'customer', 'archived'] },
              { name: 'tags', label: 'Tags (comma separated)',
                value: (row.tags || []).join(', ') },
              { name: 'notes', label: 'Notes', value: row.notes || '' }
            ], function (data, fail, ok) {
              api('PUT', '/api/contacts/' + row.id, data).then(function () {
                ok();
                toast('Contact updated', true);
                refresh();
              }).catch(function (e) { fail(e.message); });
            });
          };
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
        // Never truncate silently: a list that stops at 200 without saying so is how
        // someone concludes a lead never arrived.
        if (rows.length >= shown && shown < 500) {
          var more = el('button', 'btn btn-ghost', 'Load more');
          more.onclick = function () {
            shown = Math.min(500, shown + 200);
            refresh();
          };
          body.appendChild(more);
        } else if (rows.length >= 500) {
          body.appendChild(el('p', 'fine-print',
            'Showing the first 500 - narrow it with the status filter.'));
        }
      }).catch(function (e) {
        body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
      });
    }

    function renderSyncReport(result) {
      result = result || {};
      syncBox.innerHTML = '<h3 class="card-title">Sync with AscendPlatform</h3>' +
        '<p class="muted">' + (result.pushed || 0) + ' sent | ' +
        (result.pulled || 0) + ' received | ' + (result.conflicts || []).length +
        ' conflict' + ((result.conflicts || []).length === 1 ? '' : 's') + ' | ' +
        (result.failed || []).length + ' failed | ' +
        (result.skipped || []).length + ' skipped</p>';
      (result.warnings || []).forEach(function (warning) {
        syncBox.appendChild(el('p', 'fine-print', warning));
      });
      function report(title, rows, describe) {
        if (!rows || !rows.length) return;
        var details = el('details');
        details.appendChild(el('summary', null, title + ' (' + rows.length + ')'));
        var list = el('ul');
        rows.forEach(function (row) {
          var li = el('li', 'fine-print', describe(row));
          list.appendChild(li);
        });
        details.appendChild(list);
        syncBox.appendChild(details);
      }
      report('Conflicts', result.conflicts, function (row) {
        return (row.contact || 'contact') + ': ' + (row.field || 'field') +
          ' - ' + (row.resolution || 'resolved');
      });
      report('Needs attention', result.failed, function (row) {
        return (row.contact || 'contact') + ': ' + (row.error || 'sync failed');
      });
      report('Skipped', result.skipped, function (row) {
        return (row.contact || 'contact') + ': ' + (row.reason || 'not matched');
      });

      var objects = result.objects || {};
      if (objects.total || objects.error) {
        syncBox.appendChild(el('p', 'fine-print',
          'Workspace records mirrored: ' + (objects.created || 0) + ' created, ' +
          (objects.updated || 0) + ' updated, ' + (objects.skipped || 0) +
          ' unchanged, ' + (objects.failed || []).length + ' failed.' +
          (objects.error ? ' ' + objects.error : '')));
        report('Workspace failures', objects.failed, function (row) {
          return (row.title || row.object || 'record') + ': ' +
            (row.error || 'mirror failed');
        });
        (objects.warnings || []).forEach(function (warning) {
          syncBox.appendChild(el('p', 'fine-print', warning));
        });
      }

      var opportunities = result.opportunities || {};
      if (opportunities.total || opportunities.error) {
        syncBox.appendChild(el('p', 'fine-print',
          'Opportunities mirrored: ' + (opportunities.created || 0) + ' created, ' +
          (opportunities.updated || 0) + ' updated, ' + (opportunities.skipped || 0) +
          ' unchanged, ' + (opportunities.failed || []).length + ' failed.' +
          (opportunities.error ? ' ' + opportunities.error : '')));
        report('Opportunity failures', opportunities.failed, function (row) {
          return (row.contact || 'contact') + ': ' + (row.error || 'mirror failed');
        });
        (opportunities.warnings || []).forEach(function (warning) {
          syncBox.appendChild(el('p', 'fine-print', warning));
        });
      }
      syncBox.hidden = false;
    }

    function pollSync(jobId) {
      var after = 0;
      function tick() {
        api('GET', '/api/jobs/' + jobId + '/events?after=' + after).then(function (d) {
          (d.events || []).forEach(function (ev) {
            after = Math.max(after, Number(ev.seq) || 0);
            if (ev.type === 'progress' && ev.content) {
              syncBox.hidden = false;
              syncBox.innerHTML = '<p class="muted">' + esc(ev.content) + '</p>';
            }
          });
          var job = d.job || {};
          if (['done', 'failed', 'cancelled'].indexOf(job.status) === -1) {
            setTimeout(tick, 1200);
            return;
          }
          sync.disabled = false;
          if (job.status === 'done') {
            var result = {};
            try { result = JSON.parse(job.result || '{}'); } catch (e) { result = {}; }
            renderSyncReport(result);
            refresh();
          } else {
            syncBox.hidden = false;
            syncBox.innerHTML = '<p class="form-error">' +
              esc(job.error || 'The sync stopped before it finished.') + '</p>';
          }
        }).catch(function (e) {
          sync.disabled = false;
          toast(e.message, false);
        });
      }
      tick();
    }

    if (sync) {
      sync.onclick = function () {
        sync.disabled = true;
        syncBox.hidden = false;
        syncBox.innerHTML = '<p class="muted">Starting the contact mirror...</p>';
        api('POST', '/api/contacts/sync').then(function (d) {
          pollSync(d.job_id);
        }).catch(function (e) {
          sync.disabled = false;
          syncBox.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
        });
      };
    }

    add.onclick = function () {
      formModal('New contact', [
        { name: 'name', label: 'Name' },
        { name: 'email', label: 'Email' },
        { name: 'phone', label: 'Phone (optional)' }
      ], function (data, fail, ok) {
        api('POST', '/api/contacts', data).then(function () {
          ok();
          toast('Contact added', true);
          refresh();
        }).catch(function (e) { fail(e.message); });
      });
    };

    refresh();
    return container;
  }

  // ---------- Domains: point it here, prove it, then go live ----------
  // The old DNS modal listed two records and a single yes/no. This shows every record
  // judged separately, what DNS actually answers, and whether a certificate exists -
  // because "it does not work" is almost always one specific record.
  // How a domain row describes its provisioning run. Pure, so the harness in
  // tests/domains_state.mjs can evaluate the real function rather than match strings.
  function provisionState(job) {
    if (!job) return '';
    if (job.status === 'queued' || job.status === 'running') {
      return 'provisioning ' + job.status;
    }
    if (job.status === 'failed' || job.status === 'error') {
      return 'last provision failed';
    }
    return '';
  }
  // End provisionState.

  function viewDomains(openId) {
    var owner = isOwner(state.user);
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = owner
      ? '<div><h2>Domains</h2><p class="muted">Point a domain at this server, watch each ' +
        'record go green, then go live. A certificate is only reported once the site ' +
        'actually answers over HTTPS.</p></div>'
      : '<div><h2>Domains</h2><p class="muted">Connect a domain you own. We will show ' +
        'you the exact records to add, then set up the secure website.</p></div>';
    container.appendChild(head);

    // The one thing the Sites tab says and this tab did not: whether anything will
    // actually be written. A dry-run domain can show green DNS and still fail in the
    // browser, because provisioning only pretends to install the site block.
    var banner = el('div', 'panel-card mt');
    container.appendChild(banner);

    // After a server move, a domain pointing at the old address looks healthy from here and
    // dead from the visitor's side. One button asks DNS about every domain at once.
    var auditBar = el('div', 'chip-input');
    var auditBtn = el('button', 'btn', 'Check all domains');
    var auditHost = el('div');
    auditBar.appendChild(auditBtn);
    container.appendChild(auditBar);
    container.appendChild(auditHost);

    auditBtn.onclick = function () {
      auditBtn.disabled = true;
      auditHost.innerHTML = '';
      api('POST', '/api/domains/audit').then(function (d) {
        auditBtn.disabled = false;
        var stale = (d.rows || []).filter(function (r) { return r.status !== 'ok'; });
        var card = el('div', 'panel-card');
        card.innerHTML = '<h3 class="card-title">' + (d.checked || 0) + ' domain' +
          (d.checked === 1 ? '' : 's') + ' checked against ' +
          esc(d.server_ip || 'unknown') + '</h3>';
        if (!stale.length) {
          card.appendChild(el('p', 'muted', 'Every domain points at this server.'));
        } else {
          var table = el('table', 'table');
          table.innerHTML = '<thead><tr><th>Domain</th><th>Status</th><th>Resolves to</th>' +
            '<th>Note</th></tr></thead>';
          var tbody = el('tbody');
          stale.forEach(function (r) {
            var badge = r.status === 'missing' ? 'badge-queued' : 'badge-failed';
            tbody.innerHTML += '<tr><td>' + esc(r.domain) + '</td><td>' +
              '<span class="badge ' + badge + '">' + esc(r.status) + '</span></td>' +
              '<td><code>' + esc((r.seen || []).join(', ') || '-') + '</code></td>' +
              '<td class="fine-print">' + esc(r.note || '') + '</td></tr>';
          });
          table.appendChild(tbody);
          card.appendChild(table);
          card.appendChild(el('p', 'fine-print', 'A domain that moved on purpose is not ' +
            'broken - this is the list to work through after changing servers.'));
        }
        auditHost.appendChild(card);
      }).catch(function (e) {
        auditBtn.disabled = false;
        toast(e.message, false);
      });
    };

    var body = el('div', 'mt');
    container.appendChild(body);

    function chip(status) {
      var map = { ok: 'badge-done', wrong: 'badge-failed', missing: 'badge-queued',
                  proxied: 'badge-running', unknown: 'badge-failed' };
      return '<span class="badge ' + (map[status] || 'badge-queued') + '">' +
        esc(status || 'unknown') + '</span>';
    }

    function copyText(text, btn) {
      function done() {
        var was = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(function () { btn.textContent = was; }, 1200);
      }
      function fallback() {
        var input = el('input');
        input.value = String(text);
        document.body.appendChild(input);
        input.select();
        try { document.execCommand('copy'); done(); } catch (e) { toast('Copy failed', false); }
        input.remove();
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(String(text)).then(done).catch(fallback);
      } else {
        fallback();
      }
    }

    // ---------- Add a domain, the way GHL does it ----------
    // Type it, read the two rows, decide. Nothing is created until "Add this domain", so a
    // typo costs a re-type rather than a site row somebody has to go and delete.
    var addCard = el('div', 'panel-card');
    addCard.innerHTML = '<h3 class="card-title">Connect a new domain</h3>' +
      '<p class="fine-print">Type a domain you own. You get the exact records to add at ' +
      'your domain provider - copy them across, then press Check again until every row is ' +
      'green. Nothing is created until you add it.</p>';
    var addBar = el('div', 'chip-input');
    var addInput = el('input');
    addInput.placeholder = 'example.com';
    addInput.setAttribute('aria-label', 'Domain to add');
    var addGo = el('button', 'btn btn-primary', 'Get records');
    addBar.appendChild(addInput);
    addBar.appendChild(addGo);
    addCard.appendChild(addBar);
    var addPreview = el('div');
    addCard.appendChild(addPreview);

    addGo.onclick = function () {
      var name = (addInput.value || '').trim();
      if (!name) { toast('Type a domain first', false); return; }
      addGo.disabled = true;
      api('POST', '/api/domains/preview', { domain: name }).then(function (d) {
        addGo.disabled = false;
        addPreview.innerHTML = '';
        (d.warnings || []).forEach(function (text) {
          addPreview.appendChild(el('p', 'form-error', text));
        });
        addPreview.appendChild(el('p', 'fine-print', 'Point your domain at ' +
          esc(d.server_ip || 'the server address') +
          (owner ? ' (' + esc(d.server_ip_source || 'not set') + ')' : '') + '.'));

        var table = el('table', 'table');
        table.innerHTML = '<thead><tr><th>Type</th><th>Name</th><th>Value</th><th>TTL</th>' +
          '<th></th></tr></thead>';
        var tbody = el('tbody');
        (d.records || []).forEach(function (rec) {
          var tr = el('tr');
          tr.innerHTML = '<td><span class="badge">' + esc(rec.type) + '</span></td>' +
            '<td>' + esc(rec.name) + '</td><td><code>' + esc(rec.value) + '</code>' +
            (rec.note ? '<div class="fine-print">' + esc(rec.note) + '</div>' : '') +
            '</td><td>' + esc(String(rec.ttl || '')) + '</td>';
          var cell = el('td', 'row-actions');
          var copyOne = el('button', 'btn btn-ghost btn-sm', 'Copy');
          copyOne.onclick = function () { copyText(rec.copy || rec.value, copyOne); };
          cell.appendChild(copyOne);
          tr.appendChild(cell);
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        addPreview.appendChild(table);

        var acts = el('div', 'row-actions');
        var copyAll = el('button', 'btn btn-ghost', 'Copy all');
        copyAll.onclick = function () { copyText(d.copy_all || '', copyAll); };
        var adopt = el('button', 'btn btn-primary', 'Add this domain');
        adopt.onclick = function () {
          adopt.disabled = true;
          api('POST', '/api/sites', { domain: d.domain, target: 'local' })
            .then(function (res) {
              var site = res.site || {};
              toast('Added ' + d.domain + ' - now add the records above', true);
              refresh();
              if (site.id) location.hash = '#/app/domains/' + site.id;
            })
            .catch(function (e) { adopt.disabled = false; toast(e.message, false); });
        };
        acts.appendChild(copyAll);
        acts.appendChild(adopt);
        addPreview.appendChild(acts);
      }).catch(function (e) {
        addGo.disabled = false;
        toast(e.message, false);
      });
    };
    container.insertBefore(addCard, body);

    function refresh() {
      // The job list is what turns "dns_ok" into "provisioning queued": the site row
      // cannot know about the run that was started for it, and a failed run has to say
      // so here rather than only inside the transcript.
      var jobsPromise = api('GET', '/api/jobs').catch(function () { return { jobs: [] }; });
      Promise.all([api('GET', '/api/sites?summary=1'), jobsPromise]).then(function (results) {
        var d = results[0] || {};
        body.innerHTML = '';
        banner.innerHTML = owner
          ? '<h3>How this works</h3><div class="muted">' +
            (d.dry_run
              ? 'Dry-run: DNS checks and certificates are real, but provisioning writes ' +
                'nothing and reloads nothing until you set <code>ALLOW_LIVE_ACTIONS=1</code>.'
              : 'Live: provisioning writes the page and the site block, reloads the web ' +
                'server, then waits for the certificate. One approval covers the whole run.') +
            (d.server_ip
              ? '<br>Server IP: <b>' + esc(d.server_ip) + '</b>'
              : '<br>Set <code>SERVER_IP</code> so the DNS hints are accurate.') +
            '</div>'
          : '<h3>Connecting your domain</h3><div class="muted">Add the records we show ' +
            'at your domain provider. Once they are live, press Check and we will finish ' +
            'the secure setup.' + (d.server_ip
              ? '<br>Point the domain at <b>' + esc(d.server_ip) + '</b>.' : '') + '</div>';
        var sites = d.sites || [];
        // Newest first from the API, so the first match per domain is the current run.
        var latest = {};
        (((results[1] || {}).jobs) || []).forEach(function (job) {
          if (job.task !== 'site_provision') return;
          var key = String(job.prompt || '').trim().toLowerCase();
          if (!latest[key]) latest[key] = job;
        });
        if (!sites.length) {
          body.innerHTML = '<div class="empty-state">' +
            '<div class="big">' + icon('globe') + '</div>' +
            '<strong>No domains yet</strong>' +
            '<p>Add one under Website first — a domain has to exist before it can be ' +
            'pointed here.</p></div>';
          return;
        }
        sites.forEach(function (site) {
          var key = String(site.domain || '').trim().toLowerCase();
          body.appendChild(row(site, latest[key]));
          if (String(openId) === String(site.id)) body.appendChild(panel(site));
        });
      }).catch(function (e) {
        body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
      });
    }

    function row(site, prov) {
      var card = el('div', 'panel-card dom-row');
      var cls = site.status === 'live' ? 'badge-done'
        : (site.status === 'dns_ok' ? 'badge-running'
        : (site.status === 'error' ? 'badge-failed' : 'badge-queued'));
      var working = prov && (prov.status === 'queued' || prov.status === 'running');
      var state = provisionState(prov);
      card.innerHTML = '<div><div class="dom-name">' + esc(site.domain) + '</div>' +
        '<div class="dom-meta"><span class="badge ' + cls + '">' +
        esc(owner ? String(site.status || 'pending').replace('_', ' ')
                  : (site.status === 'live' ? 'Live'
                     : (site.status === 'error' ? 'Needs attention' : 'Connecting'))) +
        '</span>' +
        (owner && site.dns_last_ip ? ' · resolves to ' + esc(site.dns_last_ip) : '') +
        (owner && site.ssl_status ? ' · ssl ' + esc(site.ssl_status) : '') +
        (owner && state ? ' · ' + esc(state) : '') + '</div>' +
        (owner && site.last_error
          ? '<div class="fine-print">' + esc(site.last_error) + '</div>' : '') +
        '</div>';

      var actions = el('div', 'row-actions');
      var open = el('button', 'btn btn-ghost btn-sm',
        String(openId) === String(site.id) ? 'Hide' : 'Records');
      open.onclick = function () {
        location.hash = String(openId) === String(site.id)
          ? '#/app/domains' : '#/app/domains/' + site.id;
      };
      actions.appendChild(open);

      if (working) {
        var inspect = el('button', 'btn btn-ghost btn-sm', 'View job');
        inspect.onclick = function () { location.hash = '#/app/jobs/' + prov.id; };
        actions.appendChild(inspect);
      }

      // The row is where someone looks when a domain is not detected, so the two things
      // they need next belong here: a fresh lookup, and the way to publish or remove it.
      // Before this, a typo could only be deleted from the Websites tab.
      var check = el('button', 'btn btn-ghost btn-sm', 'Check DNS');
      check.onclick = function () {
        check.disabled = true;
        api('POST', '/api/sites/' + site.id + '/check').then(function (d) {
          var verdict = d.check || {};
          var queued = d.provision || null;
          var message;
          if (owner) {
            message = verdict.ok
              ? (queued ? 'DNS points here - provisioning queued (job #' + queued.id + ')'
                        : 'DNS points here')
              : 'DNS: ' + (verdict.reason || 'not pointed here yet');
          } else {
            message = verdict.ok
              ? (queued ? 'Your domain is connected. We are preparing your website.'
                        : 'Your domain is connected.')
              : 'Your domain is not connected yet. Check the records above and try again.';
          }
          toast(message, !!verdict.ok);
          refresh();
        }).catch(function (e) { toast(e.message, false); })
          .finally(function () { check.disabled = false; });
      };
      actions.appendChild(check);

      // One action for a site that is already live: install the form proxy without
      // rewriting the page. Provision would do that too, but it also rewrites index.html
      // - and "my page is fine, I just want forms on my domain" should not risk it.
      if (owner) {
      var refreshConfig = el('button', 'btn btn-ghost btn-sm', 'Refresh web config');
      refreshConfig.title = 'Serve forms at ' + site.domain + '/f/... without changing ' +
        'the website content.';
      refreshConfig.onclick = function () {
        if (!window.confirm('Refresh the web server config for ' + site.domain + '?\n\n' +
                            'This makes forms work at ' + site.domain + '/f/... and does ' +
                            'not change the website content.')) return;
        refreshConfig.disabled = true;
        api('POST', '/api/sites/' + site.id + '/refresh-config').then(function (d) {
          var result = (d && d.result) || {};
          toast(result.dry_run
            ? 'Dry-run: the form proxy is ready, but nothing was written'
            : 'Web config refreshed - forms are served at ' + site.domain,
            true);
          refresh();
        }).catch(function (e) { toast(e.message, false); })
          .finally(function () { refreshConfig.disabled = false; });
      };
      actions.appendChild(refreshConfig);
      }

      var provision = el('button', 'btn btn-primary btn-sm',
        owner ? 'Provision' : 'Publish website');
      provision.onclick = function () {
        provision.disabled = true;
        api('POST', '/api/sites/' + site.id + '/provision').then(function (d) {
          var job = (d && d.job) || {};
          if (owner) {
            toast('Provisioning queued - job #' + (job.id || '?'), true);
            if (job.id) location.hash = '#/app/jobs/' + job.id;
          } else {
            toast('We are preparing your website. This can take a moment.', true);
            refresh();
          }
        }).catch(function (e) { toast(e.message, false); })
          .finally(function () { provision.disabled = false; });
      };
      actions.appendChild(provision);

      if (canAdmin(state.user)) {
        var remove = el('button', 'btn btn-danger btn-sm', 'Remove domain');
        remove.onclick = function () {
          if (!confirm('Remove ' + site.domain + '? ' +
                       'The website will stop being served. Your DNS records are ' +
                       'unchanged, and the website files and history are kept.')) return;
          remove.disabled = true;
          api('DELETE', '/api/sites/' + site.id).then(function (res) {
            toast(res.dry_run
              ? (owner ? (res.message || 'Dry-run: nothing was removed')
                       : 'We could not remove the domain right now. Please contact your agency.')
              : (res.message || 'Removed ' + site.domain), true);
            // Do not leave a Records panel open under an id that no longer exists.
            if (res.removed && String(openId) === String(site.id)) {
              location.hash = '#/app/domains';
            } else {
              refresh();
            }
          }).catch(function (e) {
            toast(e.message, false);
            remove.disabled = false;
          });
        };
        actions.appendChild(remove);
      }

      card.appendChild(actions);
      return card;
    }

    function panel(site) {
      var box = el('div', 'panel-card dom-panel');
      box.innerHTML = '<p class="muted">Checking…</p>';

      function load() {
        api('GET', '/api/sites/' + site.id + '/dns-report').then(function (d) {
          box.innerHTML = '';
          var report = d.report || {};
          box.appendChild(el('h3', 'card-title', 'Where ' + site.domain + ' should point'));
          box.appendChild(el('p', 'fine-print', 'Server address ' + esc(d.server_ip || 'unknown') +
            (owner ? ' (' + esc(d.server_ip_source || 'not set') + ')' : '')));

          var table = el('table', 'table');
          table.innerHTML = '<thead><tr><th>Type</th><th>Name</th><th>Value</th>' +
            '<th>Status</th><th></th></tr></thead>';
          var tbody = el('tbody');
          (d.records || []).forEach(function (rec) {
            var judged = (report.rows || []).filter(function (r) {
              return r.type === rec.type && r.name === rec.name;
            })[0] || {};
            var tr = el('tr');
            tr.innerHTML = '<td><span class="badge">' + esc(rec.type) + '</span></td>' +
              '<td>' + esc(rec.name) + '</td>' +
              '<td><code>' + esc(rec.value) + '</code>' +
              (judged.note ? '<div class="fine-print">' + esc(judged.note) + '</div>' : '') +
              '</td><td>' + chip(judged.status) + '</td>';
            var cell = el('td', 'row-actions');
            var copy = el('button', 'btn btn-ghost btn-sm', 'Copy');
            copy.onclick = function () { copyText(rec.copy || rec.value, copy); };
            cell.appendChild(copy);
            tr.appendChild(cell);
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          box.appendChild(table);

          var copyAllBtn = el('button', 'btn btn-ghost btn-sm', 'Copy all');
          copyAllBtn.onclick = function () {
            copyText((d.records || []).map(function (rec) {
              return rec.copy || rec.value;
            }).join('\n'), copyAllBtn);
          };
          box.appendChild(copyAllBtn);

          var actions = el('div', 'row-actions');
          var check = el('button', 'btn btn-ghost', 'Check again');
          check.onclick = function () {
            check.disabled = true;
            api('POST', '/api/sites/' + site.id + '/verify').then(function (res) {
              var probe = res.probe || {};
              toast(probe.ok
                    ? (owner ? 'Live - certificate verified' : 'Your website is live.')
                    : (owner ? (probe.hint || ('DNS: ' + (report.reason || 'not pointed here')))
                             : 'Your domain is not connected yet. Check the records above.'),
                    !!probe.ok);
              refresh();
            }).catch(function (e) { toast(e.message, false); })
              .finally(function () { check.disabled = false; });
          };
          actions.appendChild(check);

          var live = el('button', 'btn btn-primary',
            owner ? 'Go live' : 'Publish website');
          live.disabled = !report.ok;
          if (!report.ok) {
            live.title = owner ? (report.reason || 'DNS is not pointed here yet')
                               : 'Your domain must be connected first';
          }
          live.onclick = function () {
            live.disabled = true;
            api('POST', '/api/sites/' + site.id + '/provision').then(function (res) {
              toast(owner
                    ? 'Provisioning queued - job #' + ((res.job || {}).id || '?')
                    : 'We are preparing your website. This can take a moment.', true);
              refresh();
            }).catch(function (e) {
              live.disabled = false;
              toast(e.message, false);
            });
          };
          actions.appendChild(live);
          box.appendChild(actions);

          if (report.reason) {
            box.appendChild(el('p', 'form-error',
              owner ? report.reason : 'Your domain is not connected yet.'));
          }
          var probe = d.probe || {};
          var cert;
          if (owner) {
            if (probe.ok) {
              cert = 'Certificate: ' + (probe.issuer || 'issued') +
                (probe.expires ? ' · expires ' + probe.expires : '') +
                ' · HTTPS answered ' + probe.status_code;
            } else if (probe.error) {
              cert = 'Certificate: ' + (probe.hint || probe.error);
            } else {
              cert = 'Certificate: pending — issued after the site answers, so this is not ' +
                'a failure yet.';
            }
          } else {
            cert = probe.ok ? 'Security certificate ready.'
                            : 'Security certificate pending — we finish this after your ' +
                              'domain connects.';
          }
          box.appendChild(el('p', 'fine-print', cert));
          if (owner && report.ok && !probe.ok) {
            box.appendChild(el('p', 'fine-print', 'DNS is right, so the next step is Go ' +
              'live: it writes the page and the site block, reloads the web server, then ' +
              'waits for the certificate. Until that runs a browser shows ' +
              'ERR_SSL_PROTOCOL_ERROR - that is the missing site block, not a DNS fault.'));
          }
        }).catch(function (e) {
          box.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
        });
      }

      load();
      return box;
    }

    refresh();
    return container;
  }

  // Plain words for the screen. A client should never have to read "form.submitted",
  // "agent" or "awaiting_approval" to understand what their automation does.
  function automationStepTitles(steps) {
    return (steps || []).map(function (step) {
      return step.title || step.id || '';
    }).filter(Boolean).join(' → ');
  }

  function automationStepLabel(type) {
    return ({
      agent: 'AI', tool: 'Action', condition: 'Decision', gate: 'Approval',
      notify: 'Message', stop: 'Finish', delay: 'Wait'
    })[type] || String(type || '');
  }

  function automationRunLabel(status) {
    return ({
      queued: 'Starting', running: 'Running', done: 'Finished',
      failed: 'Needs attention', awaiting_approval: 'Waiting for your approval',
      waiting: 'Waiting', cancelled: 'Stopped'
    })[status] || String(status || '').replace(/_/g, ' ');
  }

  function automationTriggerLabel(a) {
    if (a && a.trigger_label) return a.trigger_label;
    if (a && a.trigger_type === 'schedule') return 'On a schedule';
    if (a && a.trigger_type === 'event') return 'When something happens';
    return 'Only when you press Try it now';
  }

  function viewAutomations() {
    if (!canAdmin(state.user)) {
      var denied = el('div', 'container');
      var deniedCard = el('div', 'panel-card empty-state');
      deniedCard.innerHTML = '<strong>Automations are managed by an admin.</strong>' +
        '<p>Ask your workspace admin to make changes.</p>';
      denied.appendChild(deniedCard);
      return denied;
    }
    var container = el('div', 'container');
    // The trigger picker speaks in form names, not ids. One request per visit is enough;
    // if it fails, the advanced field still lets an operator type an event name.
    var formChoices = [];
    api('GET', '/api/forms').then(function (d) {
      formChoices = (d.forms || []).map(function (form) {
        return { id: form.id, name: form.name || ('Form #' + form.id) };
      });
    }).catch(function () { /* keep the advanced fallback */ });
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Automations</h2><p class="muted">Things that happen by ' +
      'themselves, like saving a new enquiry to your contacts. You approve each one once.</p></div>';
    var add = el('button', 'btn btn-primary', 'New automation');
    head.appendChild(add);
    container.appendChild(head);

    // Ask in words, on this page: the draft it produces lands in the strip below, so the
    // question and the answer share a screen instead of living in the floating widget.
    var composer = el('div', 'panel-card mt');
    composer.innerHTML = '<h3 class="card-title">What should happen automatically?</h3>' +
      '<p class="fine-print">For example: “When someone sends the Tour Enquiry form, ' +
      'save them as a contact and tag them as a tour enquiry.” Tell it once — you approve ' +
      'the result below.</p>';
    var askBox = el('textarea', 'prompt-box');
    askBox.placeholder = 'When someone sends the Tour Enquiry form, add them to my contacts…';
    composer.appendChild(askBox);
    var askBtn = el('button', 'btn btn-primary', 'Create it for me');
    askBtn.onclick = function () {
      var text = (askBox.value || '').trim();
      if (!text) {
        toast('Say what should happen first', false);
        return;
      }
      askBtn.disabled = true;
      api('POST', '/api/manager/chat',
          { message: text, history: [], automation_id: 0,
            context: 'Automations screen. Draft it and stop - the approval card on this ' +
                     'screen is the yes, so do not ask a separate question.' })
        .then(function () {
          toast('Writing it now — it will appear below for your approval', true);
          askBox.value = '';
          var tries = 0;
          var timer = setInterval(function () {
            tries += 1;
            loadDrafts();
            if (tries >= 20) {
              clearInterval(timer);
              askBtn.disabled = false;
            }
          }, 3000);
        })
        .catch(function (e) {
          askBtn.disabled = false;
          toast(e.message, false);
        });
    };
    composer.appendChild(askBtn);
    container.appendChild(composer);

    // Tools the agent asked for. Approving records them; this build has no executor, and
    // the screen says so rather than implying the tool now works.
    var toolsHost = el('div');
    container.appendChild(toolsHost);

    function decideTool(id, what) {
      api('POST', '/api/agent-tools/' + id + '/' + what).then(function () {
        toast('Tool ' + (what === 'approve' ? 'approved' : 'rejected'), true);
        loadTools();
      }).catch(function (e) { toast(e.message, false); });
    }

    function loadTools() {
      api('GET', '/api/agent-tools?status=pending').then(function (d) {
        toolsHost.innerHTML = '';
        var rows = d.tools || [];
        if (!rows.length) return;
        var card = el('div', 'panel-card mt');
        card.appendChild(el('h3', 'card-title', 'New abilities (' + rows.length + ')'));
        card.appendChild(el('p', 'fine-print', 'The assistant asked for these. Approve one ' +
          'only if you want it available to the team.'));
        rows.forEach(function (tool) {
          var row = el('div', 'auto-proposal');
          var label = el('div');
          label.innerHTML = '<strong>' + esc(tool.name) + '</strong>' +
            '<div class="fine-print">' + esc(tool.kind) + ' · ' +
            esc(tool.description || 'no description') + '</div>' +
            '<pre class="fine-print" style="white-space:pre-wrap;max-height:120px;' +
            'overflow:auto;margin:4px 0 0">' + esc(String(tool.body || '').slice(0, 600)) +
            '</pre>';
          row.appendChild(label);
          var yes = el('button', 'btn btn-primary btn-sm', 'Approve');
          yes.onclick = function () { decideTool(tool.id, 'approve'); };
          var no = el('button', 'btn btn-ghost btn-sm', 'Not now');
          no.onclick = function () { decideTool(tool.id, 'reject'); };
          row.appendChild(yes);
          row.appendChild(no);
          card.appendChild(row);
        });
        toolsHost.appendChild(card);
      }).catch(function () { toolsHost.style.display = 'none'; });
    }

    // Drafts for automations that do not exist yet. The first thing an agent produces is
    // a brand new one, and until this strip there was nowhere to accept it.
    var draftsHost = el('div');
    container.appendChild(draftsHost);

    function loadDrafts() {
      api('GET', '/api/automations/proposals?status=pending').then(function (d) {
        draftsHost.innerHTML = '';
        var fresh = (d.proposals || []).filter(function (p) { return !p.automation_id; });
        if (!fresh.length) return;
        var card = el('div', 'panel-card mt');
        card.appendChild(el('h3', 'card-title', 'Ready for your approval (' + fresh.length + ')'));
        fresh.forEach(function (draft) {
          var row = el('div', 'auto-proposal');
          var label = el('div');
          label.innerHTML = '<strong>' + esc(draft.name || 'Untitled') + '</strong>' +
            '<div class="fine-print">' +
            esc(draft.note || (automationTriggerLabel(draft) +
              ((draft.steps || []).length ? ' → ' + automationStepTitles(draft.steps) : ''))) +
            '</div>';
          row.appendChild(label);

          var apply = el('button', 'btn btn-primary btn-sm', 'Approve & turn on');
          apply.onclick = function () {
            apply.disabled = true;
            api('POST', '/api/automations/proposals/' + draft.id + '/apply').then(function (res) {
              var saved = (res.automation || {});
              toast('It’s on. “' + (saved.name || draft.name) + '” will now run by itself', true);
              loadDrafts();
              refresh();
            }).catch(function (e) {
              apply.disabled = false;
              toast(e.message, false);
            });
          };

          // The chart, before you commit to it: the same renderer the editor uses, so a
          // draft looks exactly like the automation it will become.
          var preview = el('button', 'btn btn-ghost btn-sm', 'See the steps');
          preview.onclick = function () {
            var modal = el('div', 'auto-modal');
            var panel = el('div', 'auto-panel');
            var top = el('div', 'auto-panel-head');
            top.appendChild(el('h3', 'card-title', draft.name || 'Draft'));
            var close = el('button', 'btn', 'Close');
            close.onclick = function () { modal.parentNode.removeChild(modal); };
            top.appendChild(close);
            panel.appendChild(top);
            panel.appendChild(el('p', 'fine-print', 'A preview — nothing changes until you ' +
              'approve it.'));
            var layout = {};
            (draft.steps || []).forEach(function (step, index) {
              layout[step.id] = { x: 20, y: 20 + index * 84 };
            });
            // Same canvas as the editor, so a draft can be zoomed and expanded before you
            // decide anything - a 12-step sequence is unreadable at thumbnail size.
            var shell = graphShell(function () {
              return { steps: draft.steps || [], layout: layout, states: {} };
            }, {});
            panel.appendChild(shell.node);
            panel.appendChild(el('p', 'fine-print', automationStepTitles(draft.steps)));
            modal.appendChild(panel);
            document.body.appendChild(modal);
          };
          row.appendChild(preview);

          var drop = el('button', 'btn btn-ghost btn-sm', 'Discard');
          drop.onclick = function () {
            api('POST', '/api/automations/proposals/' + draft.id + '/discard').then(function () {
              toast('Draft discarded', true);
              loadDrafts();
            }).catch(function (e) { toast(e.message, false); });
          };

          row.appendChild(apply);
          row.appendChild(drop);
          card.appendChild(row);
        });
        draftsHost.appendChild(card);
      }).catch(function () { /* no drafts is not an error */ });
    }

    var body = el('div');
    container.appendChild(body);

    var STARTER = [
      { id: 'draft', type: 'agent', title: 'Draft the work',
        config: { prompt: 'Write a short launch email for this client.' }, next: ['approve'] },
      { id: 'approve', type: 'gate', title: 'Approve before anything leaves', next: ['report'] },
      { id: 'report', type: 'notify', title: 'Report back to the client', next: ['end'] },
      { id: 'end', type: 'stop', title: 'Done', next: [] }
    ];

    function card(a) {
      var box = el('div', 'panel-card auto-card');
      var steps = (a.steps || []).length;
      var trigger = automationTriggerLabel(a);
      box.innerHTML =
        '<div class="auto-head"><div><div class="auto-name">' + esc(a.name) + '</div>' +
        '<div class="auto-meta">' + esc(a.enabled ? 'On' : 'Paused') + ' · ' +
        esc(trigger) + ' · ' + steps + (steps === 1 ? ' action' : ' actions') +
        (a.last_run_at ? ' · last ran ' +
        esc(String(a.last_run_at).slice(0, 16)) : ' · not run yet') +
        (a.last_run ? ' · <span class="run-status ' + esc(a.last_run.status) + '">' +
          esc(automationRunLabel(a.last_run.status)) + '</span>' : '') +
        '</div></div></div>';
      var actions = el('div', 'auto-actions');
      var toggle = el('button', 'btn', a.enabled ? 'Pause' : 'Turn on');
      toggle.onclick = function () {
        api('PUT', '/api/automations/' + a.id, { enabled: !a.enabled })
          .then(refresh).catch(function (e) { toast(e.message, false); });
      };
      var edit = el('button', 'btn', 'Edit');
      edit.onclick = function () { openEditor(a); };
      var del = el('button', 'btn', 'Delete');
      del.onclick = function () {
        if (!confirm('Delete "' + a.name + '"?')) return;
        api('DELETE', '/api/automations/' + a.id).then(refresh)
          .catch(function (e) { toast(e.message, false); });
      };
      var run = el('button', 'btn', 'Try it now');
      run.onclick = function () {
        run.disabled = true;
        api('POST', '/api/automations/' + a.id + '/run').then(function (d) {
          toast('Starting a test run…');
          watchJob(d.job_id, run, function () { run.disabled = false; });
        }).catch(function (e) {
          run.disabled = false;
          toast(e.message, false);
        });
      };
      if (a.last_run && a.last_run.status === 'awaiting_approval') {
        var go = el('button', 'btn btn-primary', 'Approve & continue');
        go.onclick = function () {
          go.disabled = true;
          api('POST', '/api/automations/' + a.id + '/resume').then(function (d) {
            toast('Continuing…');
            watchJob(d.job_id, go, function () { go.disabled = false; refresh(); });
          }).catch(function (e) { go.disabled = false; toast(e.message, false); });
        };
        actions.appendChild(go);
      }
      actions.appendChild(toggle); actions.appendChild(run);
      actions.appendChild(edit); actions.appendChild(del);
      box.appendChild(actions);
      return box;
    }

    function refresh() {
      api('GET', '/api/automations').then(function (d) {
        body.innerHTML = '';
        var rows = d.automations || [];
        if (!rows.length) {
          var blank = el('div', 'panel-card empty-state');
          blank.innerHTML = '<div class="big">' + icon('cog') + '</div>' +
            '<strong>Nothing runs automatically yet.</strong>' +
            '<p>Describe what you want above. The assistant builds it and you approve it ' +
            'once.</p>';
          body.appendChild(blank);
          return;
        }
        rows.forEach(function (a) { body.appendChild(card(a)); });
      }).catch(function (e) { toast(e.message, false); });
    }

    // The editor is a step list for now; the canvas arrives next, reading this same shape.
    function openEditor(a) {
      state.openAutomation = a ? a.id : 0;
      var modal = el('div', 'auto-modal');
      var panel = el('div', 'auto-panel');
      var head = el('div', 'auto-panel-head');
      head.appendChild(el('h3', 'card-title', a ? a.name : 'New automation'));
      var close = el('button', 'btn', 'Close');
      close.onclick = function () {
        state.openAutomation = 0;      // the manager must not keep editing a closed graph
        modal.parentNode.removeChild(modal);
      };
      head.appendChild(close);
      panel.appendChild(head);

      var nameField = el('input');
      nameField.placeholder = 'Name, e.g. Save tour enquiries';
      nameField.value = a ? a.name : '';
      panel.appendChild(nameField);

      // The trigger in the client's words. The form picker lists actual form names; the
      // raw event field is under "Something else" for an operator who needs it.
      var triggerConfig = (a && a.trigger_config) || {};
      var currentKind = 'manual';
      if (a && a.trigger_type === 'schedule') currentKind = 'schedule';
      if (a && a.trigger_type === 'event') {
        if (triggerConfig.event === 'form.submitted') currentKind = 'form';
        else if (triggerConfig.event === 'contact.created') currentKind = 'contact';
        else currentKind = 'other';
      }
      var whenLabel = el('p', 'fine-print', 'When should it run?');
      whenLabel.style.margin = '0';
      panel.appendChild(whenLabel);

      var triggerKind = el('select', 'auto-trigger');
      triggerKind.setAttribute('aria-label', 'When should it run');
      var triggerOptions = [
        { value: 'manual', label: 'Only when I press Try it now' },
        { value: 'form', label: 'When someone sends a form' },
        { value: 'contact', label: 'When a new contact is added' },
        { value: 'schedule', label: 'On a schedule' }
      ];
      // Only a legacy draft that already has an unusual event keeps that option. A normal
      // user never sees a technical choice.
      if (currentKind === 'other') {
        triggerOptions.push({ value: 'other', label: 'Something else (advanced)' });
      }
      triggerOptions.forEach(function (option) {
        var node = el('option', null, option.label);
        node.value = option.value;
        triggerKind.appendChild(node);
      });
      triggerKind.value = currentKind;
      panel.appendChild(triggerKind);

      var formSelect = el('select', 'auto-event');
      formSelect.setAttribute('aria-label', 'Which form');
      if (!formChoices.length) {
        var noForms = el('option', null, 'No forms yet — ask the assistant to make one');
        noForms.value = '';
        formSelect.appendChild(noForms);
      }
      formChoices.forEach(function (form) {
        var option = el('option', null, form.name);
        option.value = String(form.id);
        formSelect.appendChild(option);
      });
      var savedForm = triggerConfig.form_id ? String(triggerConfig.form_id) : '';
      if (savedForm && !formChoices.some(function (form) {
        return String(form.id) === savedForm;
      })) {
        var keptForm = el('option', null, 'Form #' + savedForm);
        keptForm.value = savedForm;
        formSelect.appendChild(keptForm);
      }
      formSelect.value = savedForm;
      panel.appendChild(formSelect);

      var scheduleSelect = el('select', 'auto-event');
      scheduleSelect.setAttribute('aria-label', 'How often');
      var scheduleOptions = [
        { value: '60', label: 'Every hour' },
        { value: '1440', label: 'Every day' },
        { value: '10080', label: 'Every week' }
      ];
      var savedEvery = String(triggerConfig.every_minutes || 1440);
      if (!scheduleOptions.some(function (option) { return option.value === savedEvery; })) {
        scheduleOptions.push({ value: savedEvery, label: 'Every ' + savedEvery + ' minutes' });
      }
      scheduleOptions.forEach(function (option) {
        var node = el('option', null, option.label);
        node.value = option.value;
        scheduleSelect.appendChild(node);
      });
      scheduleSelect.value = savedEvery;
      panel.appendChild(scheduleSelect);

      var eventField = el('input', 'auto-event');
      eventField.placeholder = 'Event name, e.g. contact.created';
      eventField.setAttribute('aria-label', 'Advanced event name');
      eventField.value = triggerConfig.event || '';
      panel.appendChild(eventField);

      function syncTriggerFields() {
        formSelect.style.display = triggerKind.value === 'form' ? '' : 'none';
        scheduleSelect.style.display = triggerKind.value === 'schedule' ? '' : 'none';
        eventField.style.display = triggerKind.value === 'other' ? '' : 'none';
      }
      triggerKind.onchange = syncTriggerFields;
      syncTriggerFields();

      var stepsTitle = el('p', 'fine-print', 'What it does');
      stepsTitle.style.margin = '0';
      panel.appendChild(stepsTitle);

      var steps = a ? a.steps : STARTER;
      var list = el('div', 'auto-steps');
      list.innerHTML = '<div class="auto-step-head">Actions</div>';
      var advancedToggle = el('label', 'fine-print auto-advanced');
      var advancedBox = el('input');
      advancedBox.type = 'checkbox';
      advancedBox.onchange = function () {
        list.classList.toggle('show-advanced', advancedBox.checked);
      };
      advancedToggle.appendChild(advancedBox);
      advancedToggle.appendChild(document.createTextNode(' Advanced settings'));
      panel.appendChild(advancedToggle);
      steps.forEach(function (step, index) {
        var row = el('div', 'auto-step');
        row.innerHTML = '<span class="auto-step-num">' + (index + 1) + '</span>' +
          '<span class="auto-step-title">' + esc(step.title || step.id) + '</span>' +
          '<span class="auto-step-type">' + esc(automationStepLabel(step.type)) + '</span>';
        // A condition is a count compared with a number - "fewer than 1 email". The editor
        // writes those three fields straight into step.config, which Save posts unchanged.
        if (step.type === 'condition') {
          var cfg = step.config || (step.config = {});
          var rules = el('div', 'auto-cond');
          function condPicker(options, key, fallback) {
            var sel = el('select', 'auto-cond-select');
            options.forEach(function (o) {
              var opt = el('option', null, o.label);
              opt.value = o.value;
              sel.appendChild(opt);
            });
            sel.value = cfg[key] || fallback;
            sel.onchange = function () { cfg[key] = sel.value; };
            return sel;
          }
          rules.appendChild(condPicker([
            { value: 'drafts', label: 'draft pages' },
            { value: 'sites', label: 'websites' },
            { value: 'failed_jobs', label: 'failed tasks' }
          ], 'fact', 'drafts')).setAttribute('aria-label', 'Condition fact');
          rules.appendChild(condPicker([
            { value: '==', label: 'is' }, { value: '!=', label: 'is not' },
            { value: '>', label: 'more than' }, { value: '>=', label: 'at least' },
            { value: '<', label: 'fewer than' }, { value: '<=', label: 'at most' }
          ], 'op', '>')).setAttribute('aria-label', 'Comparison');
          var num = el('input', 'auto-cond-num');
          num.type = 'number';
          num.min = '0';
          num.setAttribute('aria-label', 'Value');
          num.value = cfg.value === undefined ? '0' : String(cfg.value);
          num.oninput = function () { cfg.value = parseInt(num.value, 10) || 0; };
          rules.appendChild(num);
          var kindField = el('input', 'auto-cond-kind');
          kindField.placeholder = 'type, e.g. email';
          kindField.value = cfg.kind || '';
          kindField.oninput = function () { cfg.kind = kindField.value.trim(); };
          rules.appendChild(kindField);
          row.appendChild(rules);
        }
        // Tool steps used to require hand-written JSON in the graph. Two fields: the tool's
        // name, and its args as JSON with {{contact.id}} available at run time.
        if (step.type === 'tool') {
          var toolCfg = step.config || (step.config = {});
          var toolRow = el('div', 'auto-cond');
          var toolName = el('input', 'auto-cond-kind');
          toolName.placeholder = 'action, e.g. send_email';
          toolName.setAttribute('aria-label', 'Tool name');
          toolName.value = toolCfg.tool || '';
          toolName.oninput = function () { toolCfg.tool = toolName.value.trim(); };
          toolRow.appendChild(toolName);

          var argsField = el('input', 'auto-args');
          argsField.placeholder = '{"template_id": 3, "contact_id": "{{contact.id}}"}';
          argsField.setAttribute('aria-label', 'Tool arguments as JSON');
          argsField.value = toolCfg.args ? JSON.stringify(toolCfg.args) : '';
          argsField.style.minWidth = '320px';
          argsField.oninput = function () {
            var text = argsField.value.trim();
            if (!text) {
              toolCfg.args = {};
              argsField.title = 'No arguments';
              return;
            }
            try {
              toolCfg.args = JSON.parse(text);
              argsField.title = 'Valid JSON';
            } catch (e) {
              // Keep the typed text: Save refuses an invalid step rather than losing it.
              argsField.title = 'Not valid JSON yet - ' + e.message;
            }
          };
          toolRow.appendChild(argsField);
          row.appendChild(toolRow);
        }
        list.appendChild(row);
      });
      panel.appendChild(list);

      // Read-only graph: the same steps JSON, drawn. Zoom and full screen come from the
      // shell, drag from attachDrag - the canvas is shared with the draft preview.
      var layout = {};
      steps.forEach(function (step, index) {
        var saved = (a && a.layout && a.layout[step.id]) || null;
        layout[step.id] = saved
          ? { x: saved.x, y: saved.y }
          : { x: 20, y: 20 + index * 84 };
      });
      var nodeStates = {};
      var shell = graphShell(function () {
        // steps is reassigned when a draft is previewed, so read it, never close over it.
        return { steps: steps, layout: layout, states: nodeStates };
      }, { draggable: true });
      function draw() { shell.draw(); }
      panel.appendChild(shell.node);
      panel.appendChild(el('p', 'fine-print',
        'Drag a step to move it. Scroll or pinch to zoom, Expand for the whole canvas.'));

      // A draft from the agent is offered here, never applied silently: preview swaps the
      // canvas over to the proposed graph, Apply is the only thing that saves it.
      var bannerHost = el('div');
      panel.appendChild(bannerHost);
      var previewOf = null;

      if (a) {
        api('GET', '/api/automations/proposals?status=pending').then(function (d) {
          var draft = (d.proposals || []).filter(function (p) {
            return String(p.automation_id) === String(a.id);
          })[0];
          if (!draft) return;
          var bar = el('div', 'auto-proposal');
          var label = el('div');
          label.innerHTML = '<strong>A change is waiting for your approval</strong>' +
            '<div class="fine-print">' + esc(draft.note || ((draft.steps || []).length +
              ' actions · proposed ' + String(draft.created_at || '').slice(11, 16))) +
            '</div>';
          bar.appendChild(label);

          var view = el('button', 'btn btn-ghost btn-sm', 'See the draft');
          var savedSteps = null;
          var savedLayout = null;
          view.onclick = function () {
            if (savedSteps) {
              steps = savedSteps;
              Object.keys(layout).forEach(function (key) { delete layout[key]; });
              Object.keys(savedLayout).forEach(function (key) { layout[key] = savedLayout[key]; });
              savedSteps = null;
              savedLayout = null;
              modal.dataset.previewing = '';
              view.textContent = 'See the draft';
              draw();
              return;
            }
            savedSteps = steps;
            savedLayout = JSON.parse(JSON.stringify(layout));
            steps = draft.steps;
            steps.forEach(function (step, index) {
              if (!layout[step.id]) layout[step.id] = { x: 20, y: 20 + index * 84 };
            });
            modal.dataset.previewing = '1';
            view.textContent = 'Hide the draft';
            draw();
          };

          var apply = el('button', 'btn btn-primary btn-sm', 'Approve & turn on');
          apply.onclick = function () {
            apply.disabled = true;
            api('POST', '/api/automations/proposals/' + draft.id + '/apply').then(function () {
              toast('Approved — this automation now runs by itself', true);
              modal.parentNode.removeChild(modal);
              refresh();
            }).catch(function (e) {
              apply.disabled = false;
              toast(e.message, false);
            });
          };

          var drop = el('button', 'btn btn-ghost btn-sm', 'Discard');
          drop.onclick = function () {
            api('POST', '/api/automations/proposals/' + draft.id + '/discard').then(function () {
              bannerHost.innerHTML = '';
              toast('Draft discarded', true);
            }).catch(function (e) { toast(e.message, false); });
          };

          bar.appendChild(view);
          bar.appendChild(apply);
          bar.appendChild(drop);
          bannerHost.appendChild(bar);
          previewOf = draft;
        }).catch(function () { /* no banner is not an error */ });
      }

      // Live states: the job already emits {"node": id, "status": ...} per step, so this
      // only has to recolour boxes - no new backend, no websocket.
      function followRun(jobId, runBtn) {
        var after = 0;
        var timer = setInterval(function () {
          api('GET', '/api/jobs/' + jobId + '/events?after=' + after).then(function (res) {
            (res.events || []).forEach(function (ev) {
              after = Math.max(after, ev.seq || 0);
              if (ev.type !== 'node') return;
              var data = null;
              try { data = JSON.parse(ev.content || '{}'); } catch (e) { return; }
              if (data && data.node) nodeStates[data.node] = String(data.status || '');
            });
            draw();
            var status = (res.job || {}).status;
            if (['done', 'failed', 'cancelled'].indexOf(status) !== -1) {
              clearInterval(timer);
              runBtn.disabled = false;
              refresh();
            }
          }).catch(function () { clearInterval(timer); runBtn.disabled = false; });
        }, 1500);
      }

      var save = el('button', 'btn btn-primary', 'Save changes');
      save.onclick = function () {
        // While previewing, the canvas shows the *draft*. Saving here would quietly write
        // the assistant's graph over the saved one and skip the whole review step.
        if (modal.dataset.previewing) {
          toast('You are looking at a draft — approve it, or hide the draft to save your ' +
                'own edits', false);
          return;
        }
        var triggerType = 'manual';
        var triggerConfig = {};
        if (triggerKind.value === 'form') {
          if (!formSelect.value) {
            toast('Pick a form first', false);
            return;
          }
          triggerType = 'event';
          triggerConfig = { event: 'form.submitted',
                            form_id: parseInt(formSelect.value, 10) || 0 };
        } else if (triggerKind.value === 'contact') {
          triggerType = 'event';
          triggerConfig = { event: 'contact.created' };
        } else if (triggerKind.value === 'schedule') {
          triggerType = 'schedule';
          triggerConfig = { every_minutes: parseInt(scheduleSelect.value, 10) || 1440 };
        } else if (triggerKind.value === 'other') {
          triggerType = 'event';
          triggerConfig = { event: (eventField.value || '').trim() };
        }
        var payload = { name: nameField.value, steps: steps, trigger_type: triggerType,
                        trigger_config: triggerConfig,
                        layout: layout };
        var req = a ? api('PUT', '/api/automations/' + a.id, payload)
                    : api('POST', '/api/automations', payload);
        req.then(function () {
          toast('Saved');
          modal.parentNode.removeChild(modal);
          refresh();
        }).catch(function (e) { toast(e.message, false); });
      };
      panel.appendChild(save);

      if (a) {
        var runBtn = el('button', 'btn', 'Try it and watch');
        runBtn.onclick = function () {
          panel.querySelectorAll('.auto-node.done, .auto-node.running').forEach(function () {
            this.classList.remove('done', 'running');
          });
          nodeStates = {};
          draw();
          runBtn.disabled = true;
          api('POST', '/api/automations/' + a.id + '/run').then(function (d) {
            toast('Starting a test run…');
            followRun(d.job_id, runBtn);
          }).catch(function (e) {
            runBtn.disabled = false;
            toast(e.message, false);
          });
        };
        panel.appendChild(runBtn);
      }
      modal.appendChild(panel);
      modal.onclick = function (ev) { if (ev.target === modal) close.onclick(); };
      document.body.appendChild(modal);
    }

    add.onclick = function () { openEditor(null); };
    refresh();
    loadDrafts();
    loadTools();
    return container;
  }

  // One SVG, no library: boxes and bezier links. Positions come from the saved layout, or
  // fall back to a vertical stack so a graph is never unreadable.
  function graphSvg(steps, layout, states) {
    var NODE_W = 210, NODE_H = 44, GAP = 40, pos = {}, i;
    for (i = 0; i < steps.length; i++) {
      var saved = layout && layout[steps[i].id];
      pos[steps[i].id] = {
        x: saved ? saved.x : 20,
        y: saved ? saved.y : 20 + i * (NODE_H + GAP)
      };
    }
    var width = 20, height = 20;
    Object.keys(pos).forEach(function (id) {
      width = Math.max(width, pos[id].x + NODE_W + 20);
      height = Math.max(height, pos[id].y + NODE_H + 20);
    });
    var out = ['<svg class="auto-svg" viewBox="0 0 ' + width + ' ' + height + '" ' +
      'width="100%" role="img" aria-label="Automation flow">',
      '<defs><marker id="auto-arrow" viewBox="0 0 10 10" refX="9" refY="5" ' +
      'markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" ' +
      'fill="#94a3b8"/></marker></defs>'];
    steps.forEach(function (step) {
      (step.next || []).forEach(function (target) {
        var a = pos[step.id], b = pos[target];
        if (!a || !b) return;
        var x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H, x2 = b.x + NODE_W / 2, y2 = b.y;
        var mid = y1 + Math.max(18, (y2 - y1) / 2);
        out.push('<path d="M' + x1 + ' ' + y1 + ' C ' + x1 + ' ' + mid + ', ' + x2 + ' ' +
          mid + ', ' + x2 + ' ' + y2 + '" fill="none" stroke="#cbd5e1" stroke-width="2" ' +
          'marker-end="url(#auto-arrow)"/>');
      });
    });
    steps.forEach(function (step) {
      var p = pos[step.id];
      out.push('<g data-node="' + esc(step.id) + '" class="auto-node ' +
        esc((states && states[step.id]) || '') + '"><rect x="' + p.x + '" y="' + p.y + '" width="' + NODE_W + '" height="' +
        NODE_H + '" rx="10" fill="#fff" stroke="' +
        (step.type === 'gate' ? '#4338ca' : '#e2e8f0') + '" stroke-width="' +
        (step.type === 'gate' ? 2 : 1) + '"/>' +
        '<text x="' + (p.x + 12) + '" y="' + (p.y + 19) + '" font-size="12.5" ' +
        'font-weight="600" fill="#111827" font-family="inherit">' +
        esc(String(step.title || step.id).slice(0, 26)) + '</text>' +
        '<text x="' + (p.x + 12) + '" y="' + (p.y + 34) + '" font-size="10.5" ' +
        'fill="#94a3b8" letter-spacing=".06em" font-family="inherit">' +
        esc(automationStepLabel(step.type)) + '</text></g>');
    });
    out.push('</svg>');
    return out.join('');
  }

  // ---------- The graph canvas: zoom, pan, and a full-screen mode ----------
  // Zoom drives the SVG's viewBox rather than a CSS transform. That is the whole trick:
  // attachDrag() converts pointer travel through rect.width / viewBox.width, so leaving the
  // geometry in the element keeps node dragging exact at any zoom - a CSS scale would apply
  // that factor twice and make every drag feel wrong. k = 1 fits the whole graph; 8 is 8x.
  function graphShell(read, opts) {
    opts = opts || {};
    var MAX_K = 8;
    var wrap = el('div', 'auto-graph');
    var canvas = el('div', 'auto-canvas');
    var bar = el('div', 'auto-zoom');
    var minus = el('button', 'btn btn-ghost btn-sm', '−');
    var pct = el('span', 'auto-zoom-pct', '100%');
    var plus = el('button', 'btn btn-ghost btn-sm', '+');
    var fit = el('button', 'btn btn-ghost btn-sm', 'Fit');
    var expand = el('button', 'btn btn-ghost btn-sm', 'Expand');
    minus.type = plus.type = fit.type = expand.type = 'button';
    minus.title = 'Zoom out';
    plus.title = 'Zoom in';
    fit.title = 'Show the whole graph';
    expand.title = 'Fill the screen';
    pct.title = '100% fits the whole graph';
    [minus, pct, plus, fit, expand].forEach(function (node) { bar.appendChild(node); });
    wrap.appendChild(canvas);
    wrap.appendChild(bar);

    var view = { k: 1, x: 0, y: 0 };
    var bounds = { w: 1, h: 1 };

    function svg() { return canvas.querySelector('svg'); }

    function apply() {
      var node = svg();
      if (!node) return;
      var w = bounds.w / view.k, h = bounds.h / view.k;
      view.x = Math.max(0, Math.min(Math.max(0, bounds.w - w), view.x));
      view.y = Math.max(0, Math.min(Math.max(0, bounds.h - h), view.y));
      node.setAttribute('viewBox', [view.x, view.y, w, h].join(' '));
      pct.textContent = Math.round(view.k * 100) + '%';
    }

    // Zoom about a screen point, so the node under the cursor stays under the cursor.
    function zoomTo(k, clientX, clientY) {
      var node = svg();
      if (!node) return;
      var rect = node.getBoundingClientRect();
      var w = bounds.w / view.k, h = bounds.h / view.k;
      var fx = rect.width ? (clientX - rect.left) / rect.width : 0.5;
      var fy = rect.height ? (clientY - rect.top) / rect.height : 0.5;
      var px = view.x + fx * w, py = view.y + fy * h;
      k = Math.max(1, Math.min(MAX_K, k));
      var w2 = bounds.w / k, h2 = bounds.h / k;
      view.k = k;
      view.x = px - fx * w2;
      view.y = py - fy * h2;
      apply();
    }

    function zoomAround(k) {
      var rect = canvas.getBoundingClientRect();
      zoomTo(k, rect.left + rect.width / 2, rect.top + rect.height / 2);
    }

    function reset() { view.k = 1; view.x = 0; view.y = 0; apply(); }

    function draw() {
      var state = read();
      canvas.innerHTML = graphSvg(state.steps || [], state.layout || {}, state.states || {});
      var node = svg();
      if (node) {
        bounds.w = node.viewBox.baseVal.width || 1;
        bounds.h = node.viewBox.baseVal.height || 1;
      }
      apply();   // a redraw resets the viewBox to content bounds; put the view back
      if (opts.draggable) attachDrag(canvas, state.layout || {}, draw);
    }

    minus.onclick = function () { zoomAround(view.k / 1.25); };
    plus.onclick = function () { zoomAround(view.k * 1.25); };
    fit.onclick = reset;

    function setFull(on) {
      wrap.classList.toggle('is-full', on);
      document.body.classList.toggle('graph-full', on);
      expand.textContent = on ? 'Collapse' : 'Expand';
      expand.title = on ? 'Back to the panel (Esc)' : 'Fill the screen';
      apply();
    }
    expand.onclick = function () { setFull(!wrap.classList.contains('is-full')); };

    // Closing the editor while expanded must not leave the page scroll locked, so the
    // canvas collapses itself the moment it leaves the document.
    if (typeof MutationObserver === 'function') {
      var watcher = new MutationObserver(function () {
        if (wrap.isConnected) return;
        if (wrap.classList.contains('is-full')) setFull(false);
        watcher.disconnect();
      });
      watcher.observe(document.body, { childList: true, subtree: true });
    }

    // In the panel the page keeps its scroll; full screen the wheel zooms - and ctrl/cmd
    // zooms anywhere, which is also what a trackpad pinch arrives as.
    canvas.addEventListener('wheel', function (ev) {
      if (!wrap.classList.contains('is-full') && !ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      zoomTo(view.k * (ev.deltaY < 0 ? 1.12 : 1 / 1.12), ev.clientX, ev.clientY);
    }, { passive: false });

    canvas.addEventListener('dblclick', function (ev) {
      if (ev.target.closest('[data-node]')) return;
      reset();
    });

    // Pan with one pointer, pinch with two. A press that starts on a node belongs to
    // attachDrag's handler, so it never pans the canvas underneath it.
    var pointers = {}, drag = null, pinch = null;

    function pair() {
      return Object.keys(pointers).map(function (id) { return pointers[id]; });
    }

    function begin() {
      var pts = pair();
      if (pts.length === 1) {
        drag = { x: pts[0].x, y: pts[0].y, vx: view.x, vy: view.y };
        pinch = null;
        canvas.classList.add('panning');
      } else if (pts.length === 2) {
        drag = null;
        pinch = {
          dist: Math.max(1, Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y)),
          k: view.k,
          cx: (pts[0].x + pts[1].x) / 2,
          cy: (pts[0].y + pts[1].y) / 2
        };
      }
    }

    function move(ev) {
      if (!pointers[ev.pointerId]) return;
      pointers[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
      var node = svg();
      if (!node) return;
      var rect = node.getBoundingClientRect();
      var pts = pair();
      if (pinch && pts.length >= 2) {
        var dist = Math.max(1, Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y));
        zoomTo(pinch.k * (dist / pinch.dist), pinch.cx, pinch.cy);
        return;
      }
      if (!drag || !rect.width || !rect.height) return;
      var w = bounds.w / view.k, h = bounds.h / view.k;
      view.x = drag.vx - (ev.clientX - drag.x) * (w / rect.width);
      view.y = drag.vy - (ev.clientY - drag.y) * (h / rect.height);
      apply();
    }

    function end(ev) {
      delete pointers[ev.pointerId];
      if (!Object.keys(pointers).length) {
        drag = null;
        pinch = null;
        canvas.classList.remove('panning');
      } else {
        begin();
      }
    }

    canvas.addEventListener('pointerdown', function (ev) {
      if (ev.button && ev.button !== 0) return;
      if (ev.target.closest('[data-node]')) return;
      pointers[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
      begin();
    });
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);

    // Esc to leave, +/-/0 to zoom - only while expanded, and only while this canvas is on
    // the page (an editor opened twice must not answer for the one that was closed).
    document.addEventListener('keydown', function (ev) {
      if (!wrap.isConnected || !wrap.classList.contains('is-full')) return;
      if (ev.key === 'Escape') { setFull(false); return; }
      if (ev.key === '+' || ev.key === '=') { zoomAround(view.k * 1.25); ev.preventDefault(); }
      if (ev.key === '-') { zoomAround(view.k / 1.25); ev.preventDefault(); }
      if (ev.key === '0') { reset(); ev.preventDefault(); }
    });

    draw();
    return { node: wrap, draw: draw, view: view, fit: reset };
  }

  // Pointer drag on an SVG group. The scale factor matters: the SVG scales to the panel
  // width, so pixels of mouse travel are not pixels of viewBox.
  function attachDrag(container, layout, redraw) {
    var svg = container.querySelector('svg');
    if (!svg) return;
    var vb = svg.viewBox.baseVal;
    Array.prototype.forEach.call(svg.querySelectorAll('[data-node]'), function (g) {
      g.style.cursor = 'grab';
      g.addEventListener('pointerdown', function (ev) {
        var id = g.getAttribute('data-node');
        ev.preventDefault();
        var rect = svg.getBoundingClientRect();
        var scale = vb.width ? rect.width / vb.width : 1;
        var startX = ev.clientX, startY = ev.clientY;
        var from = { x: layout[id].x, y: layout[id].y };
        g.style.cursor = 'grabbing';
        function move(e) {
          var next = {
            x: Math.max(0, Math.round(from.x + (e.clientX - startX) / scale)),
            y: Math.max(0, Math.round(from.y + (e.clientY - startY) / scale))
          };
          g.setAttribute('transform', 'translate(' + (next.x - from.x) + ',' +
            (next.y - from.y) + ')');
          layout[id] = next;
        }
        function up() {
          window.removeEventListener('pointermove', move);
          window.removeEventListener('pointerup', up);
          g.style.cursor = 'grab';
          redraw();
        }
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
      });
    });
  }

  // ---------- Forms: what a client sends people, and what comes back ----------
  // The list is deliberately plain - name, kind, live state, answers so far - because those
  // are the three things a client checks. Editing arrives next; today a form can be created,
  // published and read, which is enough to start collecting real answers.
  function viewForms() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Forms</h2><p class="muted">Forms, surveys and quizzes. ' +
      'Each one lives at its own link, and every answer lands in the list beside it.</p></div>';
    var add = el('button', 'btn btn-primary', 'New form');
    var refresh = el('button', 'btn btn-ghost', 'Refresh');
    head.appendChild(add);
    head.appendChild(refresh);
    container.appendChild(head);

    var body = el('div');
    container.appendChild(body);

    function load() {
      body.innerHTML = '';
      api('GET', '/api/forms').then(function (d) {
        var rows = d.forms || [];
        if (!rows.length) {
          var empty = el('div', 'panel-card empty-state');
          empty.innerHTML = '<div class="big">' + icon('form') + '</div>' +
            '<strong>No forms yet.</strong>' +
            '<p>Create one, or ask the assistant for a quiz and it will draft it.</p>';
          body.appendChild(empty);
          return;
        }
        rows.forEach(function (item) { body.appendChild(card(item)); });
      }).catch(function (e) { toast(e.message, false); });
    }

    function card(item) {
      var box = el('div', 'panel-card auto-card');
      // The API decides: the client's own live domain first, the platform as fallback.
      // A form shared from here must not send a visitor to the platform when the
      // client has a perfectly good domain of their own.
      var link = absoluteLink(item.public_url || ('/f/' + item.slug));
      var status = item.status === 'published' ? 'Live'
        : (item.status === 'archived' ? 'Archived' : 'Draft');
      box.innerHTML = '<div class="auto-head"><div><div class="auto-name">' + esc(item.name) +
        '</div><div class="auto-meta">' + esc(item.kind) + ' · ' + item.question_count +
        (item.question_count === 1 ? ' question' : ' questions') + ' · ' +
        (item.responses || 0) + ' answer' + ((item.responses || 0) === 1 ? '' : 's') +
        ' · <span class="run-status ' + esc(item.status) + '">' + esc(status) + '</span></div>' +
        '</div></div><div class="fine-print">' + esc(link) + '</div>';

      var actions = el('div', 'auto-actions');

      var open = el('button', 'btn btn-ghost btn-sm', 'Open');
      open.onclick = function () { window.open(link, '_blank', 'noopener'); };

      var copy = el('button', 'btn btn-ghost btn-sm', 'Copy link');
      copy.onclick = function () {
        navigator.clipboard.writeText(link)
          .then(function () { toast('Link copied', true); })
          .catch(function () { toast('Copy failed', false); });
      };

      var answers = el('button', 'btn btn-ghost btn-sm', 'Responses');
      answers.onclick = function () { location.hash = '#/app/forms/' + item.id + '/responses'; };

      var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
      edit.onclick = function () { location.hash = '#/app/forms/' + item.id; };

      var toggle = el('button', 'btn btn-sm',
                      item.status === 'published' ? 'Unpublish' : 'Publish');
      toggle.onclick = function () {
        toggle.disabled = true;
        api('PUT', '/api/forms/' + item.id,
            { status: item.status === 'published' ? 'draft' : 'published' })
          .then(function (saved) {
            var updated = (saved && saved.form) || {};
            toast(item.status === 'published' ? 'Unpublished'
                 : 'Live at ' + absoluteLink(updated.public_url || link), true);
            load();
          })
          .catch(function (e) { toggle.disabled = false; toast(e.message, false); });
      };

      var drop = el('button', 'btn btn-ghost btn-sm', 'Delete');
      drop.onclick = function () {
        if (!window.confirm('Delete “' + item.name + '”? A form with answers is archived ' +
                            'instead of removed.')) return;
        api('DELETE', '/api/forms/' + item.id).then(function (d) {
          toast(d.archived ? 'Archived - it has ' + d.responses + ' answers' : 'Deleted', true);
          load();
        }).catch(function (e) { toast(e.message, false); });
      };

      [open, copy, answers, edit, toggle, drop].forEach(function (button) {
        actions.appendChild(button);
      });
      box.appendChild(actions);
      return box;
    }

    add.onclick = function () {
      var modal = el('div', 'auto-modal');
      var panel = el('div', 'auto-panel');
      panel.appendChild(el('h3', 'card-title', 'New form'));
      var name = el('input');
      name.placeholder = 'e.g. Trip planner quiz';
      name.setAttribute('aria-label', 'Form name');
      var kind = el('select');
      ['form', 'survey', 'quiz'].forEach(function (option) {
        var item = el('option', null, option);
        item.value = option;
        kind.appendChild(item);
      });
      panel.appendChild(name);
      panel.appendChild(kind);
      panel.appendChild(el('p', 'fine-print', 'It starts with one question you can edit; ' +
        'pages, branching and scoring arrive in the builder.'));
      var row = el('div', 'chip-input');
      var save = el('button', 'btn btn-primary', 'Create');
      var cancel = el('button', 'btn', 'Cancel');
      function close() { if (modal.parentNode) modal.parentNode.removeChild(modal); }
      cancel.onclick = close;
      save.onclick = function () {
        save.disabled = true;
        api('POST', '/api/forms', {
          name: (name.value || '').trim() || 'Untitled form',
          kind: kind.value,
          questions: [{ id: 'q1', type: 'text', label: 'What would you like to know?', page: 0 }]
        }).then(function (d) {
          close();
          toast('Created “' + d.form.name + '”', true);
          load();
        }).catch(function (e) { save.disabled = false; toast(e.message, false); });
      };
      row.appendChild(save);
      row.appendChild(cancel);
      panel.appendChild(row);
      modal.appendChild(panel);
      document.body.appendChild(modal);
      name.focus();
    };
    refresh.onclick = load;
    load();
    return container;
  }

  // Every answer to one form, newest first. The CSV is the same rows, so nobody has to retype
  // a report out of a table.
  function viewFormResponses(formId) {
    var container = el('div');
    var head = el('div');
    container.appendChild(head);
    var bar = el('div', 'chip-input');
    var back = el('button', 'btn btn-ghost', 'Back to forms');
    back.onclick = function () { location.hash = '#/app/forms'; };
    var csv = el('a', 'btn btn-ghost', 'Download CSV');
    csv.href = '/api/forms/' + formId + '/responses.csv';
    bar.appendChild(back);
    bar.appendChild(csv);
    container.appendChild(bar);
    var body = el('div');
    container.appendChild(body);

    api('GET', '/api/forms/' + formId + '/responses').then(function (d) {
      var item = d.form || {};
      var rows = d.responses || [];
      var questions = item.questions || [];
      head.innerHTML = '<h2>' + esc(item.name || 'Form') + '</h2><p class="muted">' +
        esc(item.kind || '') + ' · ' + rows.length + ' answer' +
        (rows.length === 1 ? '' : 's') + ' · ' +
        esc(absoluteLink(item.public_url || ('/f/' + (item.slug || '')))) + ' · ' +
        esc(item.status || 'draft') + '</p>';
      if (!rows.length) {
        var empty = el('div', 'panel-card empty-state');
        empty.innerHTML = '<div class="big">' + icon('form') + '</div>' +
          '<strong>No answers yet.</strong>' +
          '<p>Publish the form and share its link.</p>';
        body.appendChild(empty);
        return;
      }
      var table = el('table', 'table');
      table.innerHTML = '<thead><tr><th>When</th><th>Score</th>' +
        questions.map(function (question) {
          return '<th>' + esc(question.label || question.id) + '</th>';
        }).join('') + '</tr></thead>';
      var tbody = el('tbody');
      rows.forEach(function (row) {
        var cells = questions.map(function (question) {
          var value = (row.answers || {})[question.id];
          if (Array.isArray(value)) value = value.join(', ');
          return '<td>' + esc(value === undefined || value === '' ? '-' : String(value)) +
            '</td>';
        }).join('');
        tbody.innerHTML += '<tr><td>' + esc(String(row.created_at || '').slice(0, 16)) +
          '</td><td>' + (row.max_score ? row.score + '/' + row.max_score : '-') +
          '</td>' + cells + '</tr>';
      });
      table.appendChild(tbody);
      body.appendChild(table);
      if (item.kind === 'quiz') {
        body.appendChild(el('p', 'fine-print', 'Scores count only the questions each ' +
          'visitor was actually asked, so a branching quiz is marked out of its own path.'));
      }
    }).catch(function (e) {
      var problem = el('div', 'panel-card empty-state');
      problem.innerHTML = '<p><strong>' + esc(e.message) + '</strong></p>';
      body.appendChild(problem);
      toast(e.message, false);
    });
    return container;
  }

  // ---------- The form builder ----------
  // One document edited in place: questions, pages and settings live in `draft`, and only
  // Save writes them. Structural changes (add, delete, move) re-render; typing does not,
  // because re-rendering the field you are typing in is how a builder loses keystrokes.
  function viewFormBuilder(formId) {
    var container = el('div');
    var body = el('div');
    container.appendChild(body);
    var draft = null;

    function load() {
      api('GET', '/api/forms/' + formId).then(function (d) {
        draft = d.form || {};
        draft.questions = draft.questions || [];
        draft.settings = draft.settings || {};
        draft.pages = draft.pages || [];
        render();
      }).catch(function (e) { toast(e.message, false); });
    }

    function save(patch) {
      var payload = { name: draft.name, kind: draft.kind, questions: draft.questions,
                      pages: draft.pages, settings: draft.settings };
      Object.keys(patch || {}).forEach(function (key) { payload[key] = patch[key]; });
      return api('PUT', '/api/forms/' + formId, payload).then(function (d) {
        draft = d.form;
        toast(patch && patch.status
          ? (patch.status === 'published'
              ? 'Live at ' + absoluteLink(draft.public_url || ('/f/' + draft.slug))
              : 'Unpublished')
          : 'Saved', true);
        render();
      }).catch(function (e) { toast(e.message, false); });
    }

    function highestPage() {
      return draft.questions.reduce(function (top, question) {
        return Math.max(top, Number(question.page) || 0);
      }, 0);
    }

    function nextId() {
      var n = draft.questions.length + 1;
      while (draft.questions.some(function (q) { return q.id === 'q' + n; })) n += 1;
      return 'q' + n;
    }

    function row(question, index) {
      var box = el('div', 'panel-card');
      var head = el('div', 'auto-head');
      var title = el('strong', null, 'Question ' + (index + 1));
      head.appendChild(title);
      var tools = el('div', 'chip-input');
      var up = el('button', 'btn btn-ghost btn-sm', '↑');
      var down = el('button', 'btn btn-ghost btn-sm', '↓');
      var drop = el('button', 'btn btn-ghost btn-sm', 'Remove');
      up.disabled = index === 0;
      down.disabled = index === draft.questions.length - 1;
      up.onclick = function () { swap(index, index - 1); };
      down.onclick = function () { swap(index, index + 1); };
      drop.onclick = function () {
        draft.questions.splice(index, 1);
        render();
      };
      [up, down, drop].forEach(function (button) { tools.appendChild(button); });
      head.appendChild(tools);
      box.appendChild(head);

      var label = el('input');
      label.value = question.label || '';
      label.placeholder = 'The question, in the words your visitor reads';
      label.setAttribute('aria-label', 'Question label');
      label.oninput = function () { question.label = label.value; };
      box.appendChild(label);

      var line = el('div', 'chip-input');
      var type = el('select');
      ['text', 'email', 'phone', 'textarea', 'number', 'date', 'select', 'radio',
       'checkbox', 'rating'].forEach(function (kind) {
        var option = el('option', null, kind);
        option.value = kind;
        type.appendChild(option);
      });
      type.value = question.type || 'text';
      type.onchange = function () { question.type = type.value; render(); };
      line.appendChild(type);

      var page = el('input');
      page.type = 'number';
      page.min = '0';
      page.value = Number(question.page) || 0;
      page.title = 'Page number - questions on the same page appear together';
      page.style.width = '70px';
      page.onchange = function () { question.page = Math.max(0, Number(page.value) || 0); };
      line.appendChild(page);

      var required = el('label', 'opt');
      var check = el('input');
      check.type = 'checkbox';
      check.checked = !!question.required;
      check.onchange = function () { question.required = check.checked; };
      required.appendChild(check);
      required.appendChild(el('span', null, 'required'));
      line.appendChild(required);
      box.appendChild(line);

      if (['select', 'radio', 'checkbox'].indexOf(question.type) >= 0) {
        var options = el('input');
        options.value = (question.options || []).map(function (option) {
          return typeof option === 'string' ? option : (option.label || option.value);
        }).join(', ');
        options.placeholder = 'Options, comma separated';
        options.oninput = function () {
          question.options = options.value.split(',')
            .map(function (text) { return text.trim(); })
            .filter(function (text) { return text; });
        };
        box.appendChild(options);
      }

      if (draft.kind === 'quiz') {
        var marks = el('div', 'chip-input');
        var points = el('input');
        points.type = 'number';
        points.min = '0';
        points.style.width = '70px';
        points.value = (question.score || {}).points || 0;
        points.title = 'Points for a correct answer';
        points.onchange = function () {
          question.score = question.score || {};
          question.score.points = Math.max(0, Number(points.value) || 0);
        };
        var correct = el('input');
        correct.value = (question.score || {}).correct || '';
        correct.placeholder = 'Correct answer (must match an option)';
        correct.oninput = function () {
          question.score = question.score || {};
          question.score.correct = correct.value;
        };
        marks.appendChild(points);
        marks.appendChild(correct);
        box.appendChild(marks);
      }
      box.appendChild(rule(question));
      return box;
    }

    function swap(from, to) {
      var moved = draft.questions.splice(from, 1)[0];
      draft.questions.splice(to, 0, moved);
      render();
    }

    // One branching rule per question in the UI. The engine allows a list; expressing it as
    // "after this answer, go there" is what a person actually means, and one line per
    // question is easier to read than a rules table. The first rule wins, which is the
    // engine's rule too.
    function rule(question) {
      var box = el('div', 'chip-input');
      var existing = (question.logic || [])[0] || {};
      var when = existing.when || {};
      box.appendChild(el('span', 'fine-print', 'If this answer'));
      var op = el('select');
      [['is_set', 'is given'], ['eq', 'is'], ['ne', 'is not'], ['contains', 'contains'],
       ['gt', 'is more than'], ['lt', 'is less than'], ['is_empty', 'is empty']
      ].forEach(function (pair) {
        var option = el('option', null, pair[1]);
        option.value = pair[0];
        op.appendChild(option);
      });
      op.value = when.op || 'is_set';
      box.appendChild(op);
      var value = el('input');
      value.value = when.value === undefined || when.value === null ? '' : String(when.value);
      value.placeholder = 'value';
      value.style.maxWidth = '160px';
      box.appendChild(value);
      box.appendChild(el('span', 'fine-print', 'then go to'));
      var target = el('select');
      var cont = el('option', null, 'the next question');
      cont.value = '';
      target.appendChild(cont);
      draft.questions.forEach(function (other) {
        var option = el('option', null, (other.label || other.id).slice(0, 40));
        option.value = other.id;
        target.appendChild(option);
      });
      var stop = el('option', null, 'the end of the form');
      stop.value = 'end';
      target.appendChild(stop);
      var pages = Math.max(highestPage() + 1, 1);
      for (var page = 0; page < pages; page++) {
        var jump = el('option', null, 'page ' + (page + 1));
        jump.value = 'page:' + page;
        target.appendChild(jump);
      }
      target.value = existing.goto || '';
      box.appendChild(target);

      function sync() {
        var goto = target.value;
        if (!goto) {
          delete question.logic;
          return;
        }
        question.logic = [{ when: { q: question.id, op: op.value, value: value.value },
                            goto: goto }];
      }
      op.onchange = sync;
      value.oninput = sync;
      target.onchange = sync;
      return box;
    }

    function render() {
      body.innerHTML = '';
      var head = el('div');
      var live = draft.status === 'published' ? 'Live' : (draft.status || 'draft');
      head.innerHTML = '<h2>' + esc(draft.name || 'Form') + '</h2><p class="muted">' +
        esc(draft.kind || 'form') + ' · ' + draft.questions.length + ' question' +
        (draft.questions.length === 1 ? '' : 's') + ' · ' + (highestPage() + 1) + ' page' +
        (highestPage() ? 's' : '') + ' · ' + esc(live) + ' · ' +
        esc(absoluteLink(draft.public_url || ('/f/' + (draft.slug || '')))) + '</p>';
      body.appendChild(head);

      var bar = el('div', 'chip-input');
      var back = el('button', 'btn btn-ghost', 'Back to forms');
      back.onclick = function () { location.hash = '#/app/forms'; };
      var preview = el('button', 'btn btn-ghost', 'Preview');
      preview.onclick = function () {
        window.open(location.origin + '/f/' + draft.slug + '?preview=1', '_blank', 'noopener');
      };
      var add = el('button', 'btn', 'Add question');
      add.onclick = function () {
        draft.questions.push({ id: nextId(), type: 'text', label: '',
                               page: highestPage(), required: false });
        render();
      };
      var addPage = el('button', 'btn btn-ghost', 'Add page');
      addPage.onclick = function () {
        draft.questions.push({ id: nextId(), type: 'text', label: '',
                               page: highestPage() + 1, required: false });
        render();
      };
      var saveBtn = el('button', 'btn btn-primary', 'Save');
      saveBtn.onclick = function () { save(); };
      var publish = el('button', 'btn',
                       draft.status === 'published' ? 'Unpublish' : 'Publish');
      publish.onclick = function () {
        save({ status: draft.status === 'published' ? 'draft' : 'published' });
      };
      [back, preview, add, addPage, saveBtn, publish].forEach(function (button) {
        bar.appendChild(button);
      });
      body.appendChild(bar);

      var meta = el('div', 'panel-card');
      var name = el('input');
      name.value = draft.name || '';
      name.setAttribute('aria-label', 'Form name');
      name.oninput = function () { draft.name = name.value; };
      meta.appendChild(name);
      var kind = el('select');
      ['form', 'survey', 'quiz'].forEach(function (option) {
        var item = el('option', null, option);
        item.value = option;
        kind.appendChild(item);
      });
      kind.value = draft.kind || 'form';
      kind.onchange = function () { draft.kind = kind.value; render(); };
      meta.appendChild(kind);
      var description = el('input');
      description.value = draft.settings.description || '';
      description.placeholder = 'A line under the title';
      description.oninput = function () { draft.settings.description = description.value; };
      meta.appendChild(description);
      var submit = el('input');
      submit.value = draft.settings.submit_label || '';
      submit.placeholder = 'Button label, e.g. Send enquiry';
      submit.oninput = function () { draft.settings.submit_label = submit.value; };
      meta.appendChild(submit);
      var brandRow = el('label', 'opt');
      brandRow.appendChild(el('span', null, 'Accent colour'));
      var brand = el('input');
      brand.type = 'color';
      brand.value = draft.settings.brand || '#4f46e5';
      brand.oninput = function () { draft.settings.brand = brand.value; };
      brandRow.appendChild(brand);
      meta.appendChild(brandRow);
      var success = el('input');
      success.value = draft.settings.success || '';
      success.placeholder = 'Thank-you message after submitting';
      success.oninput = function () { draft.settings.success = success.value; };
      meta.appendChild(success);
      var contact = el('label', 'opt');
      var contactCheck = el('input');
      contactCheck.type = 'checkbox';
      contactCheck.checked = draft.settings.create_contact !== false;
      contactCheck.onchange = function () {
        draft.settings.create_contact = contactCheck.checked;
      };
      contact.appendChild(contactCheck);
      contact.appendChild(el('span', null, 'Link submissions to CRM'));
      meta.appendChild(contact);
      meta.appendChild(el('p', 'fine-print', 'A contact is created from the name and ' +
        'email answers. Map the fields below when your questions use different names.'));

      // The API has always accepted settings.contact as {role: question id}; this is
      // the missing console control. Without it, a form with questions like "Who are
      // you?" could collect a lead but never create a CRM contact.
      var crmMap = el('div', 'chip-input');
      draft.settings.contact = draft.settings.contact || {};
      [['name', 'Name'], ['email', 'Email'], ['phone', 'Phone']].forEach(function (pair) {
        var role = pair[0];
        var select = el('select');
        var auto = el('option', null, 'Automatic ' + pair[1].toLowerCase());
        auto.value = '';
        select.appendChild(auto);
        draft.questions.forEach(function (question) {
          var option = el('option', null, (question.label || question.id).slice(0, 50));
          option.value = question.id;
          select.appendChild(option);
        });
        select.value = draft.settings.contact[role] || '';
        select.onchange = function () {
          if (select.value) draft.settings.contact[role] = select.value;
          else delete draft.settings.contact[role];
        };
        select.setAttribute('aria-label', 'CRM ' + pair[1] + ' field');
        crmMap.appendChild(select);
      });
      meta.appendChild(crmMap);
      body.appendChild(meta);

      if (!draft.questions.length) {
        body.appendChild(el('p', 'muted', 'No questions yet - press Add question.'));
      }
      draft.questions.forEach(function (question, index) {
        body.appendChild(row(question, index));
      });
      body.appendChild(el('p', 'fine-print', 'Branching: a rule sends the visitor to ' +
        'another question, another page, or the end. Save, then Preview to walk it.'));
    }

    load();
    return container;
  }

  // ---------- Workspace switcher ----------
  // One row under the logo. The operator picks a client to act as; a client just sees
  // their own name. The choice lives in the session, so every scoped view follows it.
  function workspaceSwitcher() {
    var row = el('div', 'side-workspace');
    row.appendChild(el('div', 'side-workspace-label', 'Workspace'));
    api('GET', '/api/session/org').then(function (d) {
      if (!((d.orgs || []).length)) {
        row.appendChild(el('div', 'side-workspace-name', d.name || 'No client'));
        return;
      }
      var pick = el('select', 'side-workspace-select');
      var all = el('option', null, 'All clients');
      all.value = '0';
      pick.appendChild(all);
      d.orgs.forEach(function (o) {
        var opt = el('option', null, o.name);
        opt.value = o.id;
        pick.appendChild(opt);
      });
      pick.value = d.acting_org ? String(d.acting_org) : '0';
      pick.onchange = function () {
        var chosen = pick.value === '0' ? null : pick.value;
        api('POST', '/api/session/org', { org_id: chosen }).then(function () {
          // The remembered chat and draft are keyed by the acting client now. Update the
          // user object in place so the next mount reads the right slot without a reload.
          if (state.user) state.user.acting_org = chosen ? Number(chosen) : null;
          toast(chosen ? 'Now acting as ' + pick.options[pick.selectedIndex].text
                       : 'Showing all clients', true);
          creditCache.at = 0;   // the sidebar bar must follow the new tenant at once
          // The kept Studio was built for the old tenant: its thread and canvas are
          // another client's work now, so it must not come back on the next visit.
          discardStudio();
          render();     // re-render the current route against the new tenant
        }).catch(function (e) { toast(e.message, false); });
      };
      row.appendChild(pick);
    }).catch(function () { row.style.display = 'none'; });
    return row;
  }

  // ---------- Floating manager ----------
  // Lives outside the router so it survives navigation: a manager you have to find a
  // tab for is not a manager.
  function managerWidget() {
    if (document.getElementById('mgr-fab')) return;
    var fab = el('button', 'mgr-fab', 'Ask');
    fab.id = 'mgr-fab';
    var panel = el('div', 'mgr-panel hidden');
    var log = el('div', 'mgr-log');
    var say = el('input');
    say.placeholder = 'Ask about traffic, sites or drafts…';
    var send = el('button', 'mgr-send', '↑');
    send.setAttribute('aria-label', 'Send');
    var row = el('div', 'mgr-composer');
    row.appendChild(say); row.appendChild(send);

    var head = el('div', 'mgr-head');
    var mark = el('div', 'mgr-mark');
    mark.innerHTML = '<img src="/admin/favicon-64.png" alt="" />';
    var titles = el('div', null);
    titles.appendChild(el('div', 'mgr-title', 'Manager'));
    var sub = el('div', 'mgr-sub');
    sub.innerHTML = '<span class="dot"></span>ready';
    titles.appendChild(sub);
    head.appendChild(mark); head.appendChild(titles);

    var empty = el('div', 'mgr-empty');
    empty.appendChild(el('p', null, 'Ask me anything about the account — or hand off some work.'));
    var chips = el('div', 'mgr-chips');
    ['How is traffic this week?', 'Write a launch email', 'Review my site'].forEach(function (t) {
      var chip = el('button', 'mgr-chip', t);
      chip.onclick = function () { say.value = t; send.onclick(); };
      chips.appendChild(chip);
    });
    empty.appendChild(chips);

    log.appendChild(empty);
    panel.appendChild(head);
    panel.appendChild(log);
    panel.appendChild(row);
    document.body.appendChild(panel);
    document.body.appendChild(fab);

    function line(text, cls) {
      if (empty.parentNode) empty.parentNode.removeChild(empty);
      log.appendChild(el('div', cls || 'mgr-msg', text));
      log.scrollTop = log.scrollHeight;
    }

    fab.onclick = function () {
      panel.classList.toggle('hidden');
      if (!panel.classList.contains('hidden')) say.focus();
    };

    send.onclick = function () {
      var text = (say.value || '').trim();
      if (!text) return;
      say.value = '';
      line(text, 'mgr-msg mine');
      var thinking = el('span', 'activity running');
      log.appendChild(thinking);
      send.disabled = true;
      ensureChatSession().then(function (session) {
        if (!session) throw new Error('The chat could not be started.');
        return api('POST', '/api/manager/chat', {
          message: text,
          session_id: session,
          // Tells the manager which graph the user is looking at, so a change request
          // revises it instead of drafting a second automation.
          automation_id: state.openAutomation || 0
        });
      })
        .then(function (d) {
          watchJob(d.job_id, thinking, function () {
            api('GET', '/api/jobs/' + d.job_id + '/events?after=0').then(function (r) {
              if (thinking.parentNode) thinking.parentNode.removeChild(thinking);
              var said = (r.events || []).filter(function (ev) {
                return ev.source === 'Manager' && ev.type === 'message';
              }).pop();
              var result = (r.job && r.job.result) || '';
              var answer = said ? said.content
                : (result || ('The assistant could not answer: ' +
                  (jobFailure(r.events) || ('open job #' + d.job_id + ' to see why.'))));
              line(answer, 'mgr-msg');
              // Questions arrive as their own event, so they get a card with inputs rather
              // than being folded into the answer text where they scroll away.
              var asked = (r.events || []).filter(function (ev) {
                return ev.source === 'Manager' && ev.type === 'question';
              }).pop();
              if (asked) {
                var questions = [];
                try { questions = JSON.parse(asked.content || '[]'); } catch (e) { questions = []; }
                if (questions.length) {
                  var card = el('div', 'mgr-questions');
                  card.appendChild(el('div', 'mgr-q-title', 'The team needs a little more'));
                  var inputs = questions.map(function (question) {
                    var field = el('div', 'mgr-q');
                    field.appendChild(el('label', 'mgr-q-label', String(question)));
                    var input = el('input', 'mgr-q-input');
                    field.appendChild(input);
                    card.appendChild(field);
                    return input;
                  });
                  var reply = el('button', 'btn btn-primary btn-sm', 'Send answers');
                  reply.onclick = function () {
                    var parts = inputs.map(function (input, index) {
                      var text = (input.value || '').trim();
                      return text ? questions[index] + ' ' + text : '';
                    }).filter(function (part) { return part; });
                    if (!parts.length) {
                      toast('Answer at least one question', false);
                      return;
                    }
                    reply.disabled = true;
                    line(parts.join(' '), 'mgr-msg me');
                    card.parentNode.removeChild(card);
                    // The send handler reads the widget's own box, so the answers go there
                    // and a normal turn carries them - no second code path to keep in step.
                    var host = log.closest ? log.closest('.mgr, .manager, .chat, .widget') : null;
                    var box = (host || document).querySelector('textarea, input[type="text"]');
                    if (!box) {
                      toast('Type the answers into the box and send', false);
                      reply.disabled = false;
                      return;
                    }
                    box.value = parts.join(' ');
                    send.click();
                  };
                  card.appendChild(reply);
                  log.appendChild(card);
                }
              }
              var built = (r.events || []).filter(function (ev) {
                return ev.source === 'Manager' && ev.type === 'build';
              }).pop();
              if (built) {
                var info = {};
                try { info = JSON.parse(built.content || '{}'); } catch (e) { info = {}; }
                if (info.build_id) showBuildCard(log, info.build_id);
              }
              send.disabled = false;
            }).catch(function () { send.disabled = false; });
          });
        })
        .catch(function (e) {
          if (thinking.parentNode) thinking.parentNode.removeChild(thinking);
          line(e.message, 'form-error');
          send.disabled = false;
        });
    };

    say.onkeydown = function (ev) { if (ev.key === 'Enter') send.onclick(); };
  }

  // ---------- The Studio, kept alive across app tabs ----------
  // The Studio owns the voice and the job pollers, so a tab switch must not end the
  // call. Its node is moved into a hidden holder - never cloned - so every listener and
  // closure survives, and a strip in the corner is the compact door back.
  var studioKept = { node: null, cleanups: [], pending: '' };
  var lastRoute = '';
  var studioStripEl = null;
  var syncOrbAfterRender = null;

  function keepStudio(app) {
    var shell = app.querySelector('.app-shell');
    if (!shell) { runCleanups(); app.innerHTML = ''; return; }
    var holder = document.getElementById('studio-kept');
    if (!holder) {
      holder = el('div', 'studio-kept');
      holder.id = 'studio-kept';
      holder.hidden = true;
      document.body.appendChild(holder);
    }
    holder.appendChild(shell);
    studioKept.node = shell;
    studioKept.cleanups = cleanups;
    studioKept.pending = '';
    cleanups = [];
    app.innerHTML = '';
    showStudioStrip();
  }

  function restoreStudio(app) {
    runCleanups();                    // the tab being left
    app.innerHTML = '';
    app.appendChild(studioKept.node);
    cleanups = studioKept.cleanups;
    var pending = studioKept.pending;
    studioKept = { node: null, cleanups: [], pending: '' };
    lastRoute = 'app/studio';
    hideStudioStrip();
    // Keep the URL on the draft so a reload lands on the same page, not an empty canvas.
    var draft = readStudioDraft();
    if (draft) {
      try { history.replaceState(null, '', '#/app/studio/deliverable/' + draft.id); }
      catch (e) { /* a blocked history call is not fatal */ }
    }
    if (pending === 'talk') {
      try { app.querySelector('.studio-voice-row .btn').click(); }
      catch (e) { /* no button: the user can press Talk */ }
    }
  }

  function discardStudio() {
    if (!studioKept.node) return;
    var saved = cleanups;
    cleanups = studioKept.cleanups;
    runCleanups();
    cleanups = saved;
    if (studioKept.node.parentNode) studioKept.node.parentNode.removeChild(studioKept.node);
    studioKept = { node: null, cleanups: [], pending: '' };
    hideStudioStrip();
  }

  function ensureStudioStrip() {
    if (studioStripEl) return studioStripEl;
    var strip = el('div', 'studio-strip');
    strip.id = 'studio-strip';
    strip.hidden = true;
    var dot = el('span', 'studio-strip-dot');
    var state = el('span', 'studio-strip-state', 'Studio');
    var last = el('span', 'studio-strip-last');
    var talk = el('button', 'btn btn-ghost btn-sm', 'Talk');
    var open = el('button', 'btn btn-ghost btn-sm', 'Open');
    talk.type = 'button';
    open.type = 'button';
    talk.onclick = function () {
      if (!studioKept.node) return;
      // Already talking? The panel is still docked in the kept view, so restoring the
      // Studio is enough; pressing the view own Talk would raise the full-screen overlay
      // instead of keeping the call in its pane.
      var running = window.AscendVoice && window.AscendVoice.running &&
        window.AscendVoice.running();
      studioKept.pending = running ? '' : 'talk';
      location.hash = '#/app/studio';
    };
    open.onclick = function () { location.hash = '#/app/studio'; };
    strip.appendChild(dot);
    strip.appendChild(state);
    strip.appendChild(last);
    strip.appendChild(talk);
    strip.appendChild(open);
    document.body.appendChild(strip);
    studioStripEl = strip;
    setInterval(refreshStudioStrip, 2000);
    return strip;
  }

  function refreshStudioStrip() {
    var strip = studioStripEl;
    if (!strip || strip.hidden) return;
    var phase = (window.AscendVoice && window.AscendVoice.phase)
      ? window.AscendVoice.phase() : 'idle';
    var states = { connecting: 'Connecting', listening: 'Listening',
                   thinking: 'Thinking', speaking: 'Speaking', error: 'Voice error' };
    strip.querySelector('.studio-strip-state').textContent = states[phase] || 'Studio';
    strip.className = 'studio-strip phase-' + phase;
    var thread = studioKept.node && studioKept.node.querySelector('.studio-thread');
    var rows = thread ? thread.querySelectorAll('.asst-body') : [];
    var latest = rows.length ? rows[rows.length - 1] : null;
    strip.querySelector('.studio-strip-last').textContent =
      latest ? String(latest.textContent || '').slice(0, 90) : '';
  }

  function showStudioStrip() {
    var strip = ensureStudioStrip();
    strip.hidden = false;
    refreshStudioStrip();
    if (syncOrbAfterRender) syncOrbAfterRender();
  }

  function hideStudioStrip() {
    if (studioStripEl) studioStripEl.hidden = true;
    if (syncOrbAfterRender) syncOrbAfterRender();
  }

  // ---------- Router ----------
  function render() {
    var app = document.getElementById('app');
    var parts = (location.hash || '').replace(/^#/, '').split('/').filter(Boolean);
    var previous = lastRoute;
    var studioRoot = parts[0] === 'app' && parts[1] === 'studio' && parts.length === 2;
    var studioDeep = parts[0] === 'app' && parts[1] === 'studio' && parts.length > 2;

    // A deeper Studio link asks for another page, and the operator's console is another
    // app: both end the kept session. Ordinary app tabs keep it in the background.
    if (studioKept.node && (studioDeep || parts[0] !== 'app')) discardStudio();
    if (studioRoot && studioKept.node) { restoreStudio(app); return; }

    lastRoute = parts.join('/');
    if (previous === 'app/studio' && parts[0] === 'app' && parts[1] !== 'studio') {
      keepStudio(app);
    } else {
      runCleanups();
      app.innerHTML = '';
    }

    // These links have to work before anyone is signed in, so they are handled ahead
    // of the auth gate and rendered without the app shell.
    if (parts[0] === 'accept' && parts[1]) {
      app.appendChild(viewAccept(decodeURIComponent(parts[1])));
      return;
    }
    if (parts[0] === 'forgot-password') {
      app.appendChild(viewForgotPassword());
      return;
    }
    if (parts[0] === 'reset' && parts[1]) {
      app.appendChild(viewResetPassword(decodeURIComponent(parts[1])));
      return;
    }

    if (!state.user) { app.appendChild(viewLogin()); return; }

    // Landing: the operator belongs in the console, a client in their workspace.
    if (!parts.length) {
      location.replace(isOwner(state.user) ? AREAS.platform.home : AREAS.app.home);
      return;
    }
    // A bookmark, or an in-page link written before the split. Rebuild it inside the
    // workspace instead of bouncing the user somewhere unrelated.
    if (parts[0] !== 'app' && parts[0] !== 'platform') {
      location.replace('#/app/' + parts.join('/'));
      return;
    }
    // #/platform/* is the operator's console. Sending a client home is friendlier
    // than letting them render it and collect a 403 from every request.
    if (parts[0] === 'platform' && !isOwner(state.user)) {
      location.replace(AREAS.app.home);
      return;
    }
    // The Assistant tab became the Studio. An old bookmark, the tour or a chat link
    // still points here, and rendering a second, canvas-less thread would be a worse
    // version of the same screen.
    if (parts[0] === 'app' && parts[1] === 'assistant') {
      location.replace('#/app/studio');
      return;
    }
    // Settings and Team are admin-only (nav hides them); a bookmark should not collect
    // a 403 toast.
    if (parts[0] === 'app' && (parts[1] === 'settings' || parts[1] === 'team')
        && !canAdmin(state.user)) {
      location.replace(AREAS.app.home);
      return;
    }
    // Jobs is the raw transcript, with tool and backend detail. Clients get the
    // friendly Studio progress instead; the owner keeps the operator view.
    if (parts[0] === 'app' && parts[1] === 'jobs' && !isOwner(state.user)) {
      location.replace(AREAS.app.home);
      return;
    }

    var area = parts[0];
    var tab = parts[1] || '';
    var rest = parts.slice(2);

    var shell = el('div', 'app-shell');
    shell.appendChild(sidebar(parts));
    var main = el('div', 'main');
    main.appendChild(topbar());
    var content = el('div', 'content');
    main.appendChild(content);
    shell.appendChild(main);
    app.appendChild(shell);

    var view;
    if (area === 'platform') {
      if (tab === 'clients' && rest[0]) view = viewClient(rest[0], rest[1] || 'overview');
      else if (tab === 'people') view = viewPlatformPeople();
      else if (tab === 'bugs') view = viewBugTracker();
      else if (tab === 'domains') view = viewPlatformDomains();
      else if (tab === 'usage') view = viewPlatformUsage();
      else if (tab === 'servers') view = viewServers();
      else if (tab === 'email') view = viewPlatformEmail();
      else if (tab === 'sources') view = viewDataSources();
      else if (tab === 'agents') view = viewAgents();
      else if (tab === 'settings') view = viewSettings();
      else view = viewClients();
    } else if (tab === 'bot' && rest[0] === 'new') view = viewEditor(null);
    else if (tab === 'bot' && rest[1] === 'stats') view = viewStats(rest[0]);
    else if (tab === 'bot' && rest[1] === 'conversations') view = viewConversations(rest[0]);
    else if (tab === 'bot' && rest[0]) view = viewEditor(rest[0]);
    else if (tab === 'profile') view = viewProfile();
    else if (tab === 'bots') view = viewBots();
    else if (tab === 'conversations') view = viewConversationsIndex();
    else if (tab === 'domains') view = viewDomains(rest[0]);
    else if (tab === 'contacts') view = viewContacts();
    else if (tab === 'forms' && rest[1] === 'responses') view = viewFormResponses(rest[0]);
    else if (tab === 'forms' && rest[0]) view = viewFormBuilder(rest[0]);
    else if (tab === 'forms') view = viewForms();
    else if (tab === 'media') view = viewMedia();
    else if (tab === 'studio') view = viewStudio(rest[0] || '', rest[1] || '');
    else if (tab === 'usage') view = viewUsage();
    else if (tab === 'analytics') view = viewAnalytics(rest[0] || '');
    else if (tab === 'automations') view = viewAutomations();
    else if (tab === 'sites') view = viewSites();
    else if (tab === 'jobs' && rest[0]) view = viewJob(rest[0]);
    else if (tab === 'jobs') view = viewJobs();
    else if (tab === 'team') view = viewUsers();
    else if (tab === 'settings') view = viewSettings();
    else if (CONTENT_TABS[tab]) view = viewDeliverables(tab);
    else if (isSoonTab(tab)) view = viewSoon(tab);
    else view = viewDashboard();

    content.appendChild(view);
  }

  // ---------- The always-there assistant ----------
  // A floating entry point on every screen, because an assistant you have to navigate to is an
  // assistant nobody uses. Same endpoint as the Studio: it is a second door, not a
  // second brain.
  function assistOrb() {
    var orb = el('button', 'assist-orb', '✦');
    orb.title = 'Ask the AI';
    orb.setAttribute('aria-label', 'Ask the AI');
    document.body.appendChild(orb);
    var sheet = null;

    function closeAll() {
      if (sheet && sheet.parentNode) sheet.parentNode.removeChild(sheet);
      sheet = null;
    }

    function open() {
      if (sheet) { closeAll(); return; }
      sheet = el('div', 'assist-sheet');
      var head = el('div', 'assist-head');
      head.appendChild(el('strong', null, 'Ask the AI'));
      var fresh = el('button', 'btn btn-ghost btn-sm', 'New');
      fresh.onclick = function () {
        // The only way to start a new thread. Typing, talking and the Studio all share
        // the same active session until this clears it.
        writeStudioSession(null);
        log.innerHTML = '';
        greeting();
        input.focus();
      };
      var toPage = el('button', 'btn btn-ghost btn-sm', 'Full screen');
      toPage.onclick = function () { closeAll(); location.hash = '#/app/studio'; };
      var close = el('button', 'btn btn-ghost btn-sm', '×');
      close.onclick = closeAll;
      head.appendChild(fresh);
      head.appendChild(toPage);
      head.appendChild(close);
      sheet.appendChild(head);

      var log = el('div', 'assist-log');
      sheet.appendChild(log);
      var row = el('div', 'chip-input');
      var input = el('input');
      input.placeholder = 'What do you want done?';
      input.setAttribute('aria-label', 'Ask the AI');
      var go = el('button', 'btn btn-primary btn-sm', 'Send');
      var mic = el('button', 'btn btn-ghost btn-sm', 'Talk');
      mic.style.display = 'none';
      row.appendChild(input);
      row.appendChild(go);
      row.appendChild(mic);
      sheet.appendChild(row);
      document.body.appendChild(sheet);
      input.focus();

      function say(who, text) {
        log.appendChild(el('div', 'assist-line ' + who, text));
        log.scrollTop = log.scrollHeight;
        return log.lastChild;
      }

      function greeting() {
        log.innerHTML = '';
        say('them', 'Ask me to build, change or explain anything on this page.');
      }

      // The sheet used to keep its own last-8 array, so closing it started a blank
      // conversation and voice turns never reached the Manager. The server session is
      // the thread now: opening the sheet shows the same one the Studio has.
      function loadThread() {
        ensureChatSession().then(function (id) {
          if (!id) { greeting(); return; }
          api('GET', '/api/assistant/sessions/' + id).then(function (d) {
            var messages = (d.messages || []).slice(-30);
            log.innerHTML = '';
            if (!messages.length) { greeting(); return; }
            messages.forEach(function (message) {
              say(message.role === 'user' ? 'me' : 'them', message.content);
            });
          }).catch(function (e) {
            if (e && e.status === 404) writeStudioSession(null);
            greeting();
          });
        });
      }

      // The hash is the only reliable statement of where the user is: #/app/forms/12/responses
      // is a different question from "the forms screen", and the manager can only ask about a
      // record it has the id for.
      var parts = (location.hash || '').replace(/^#\/?/, '').split('/');
      var page = parts[1] || 'home';
      var record = parts[2] || '';
      var where = record ? (page + ' screen, record #' + record) : (page + ' screen');
      loadThread();

      function ask(text) {
        say('me', text);
        var wait = say('them', 'Working…');
        function send(sessionId, retried) {
          return api('POST', '/api/manager/chat', {
            message: text,
            // The screen is prompt context; the server stores the user's words as the
            // turn, so the chat title is not "[context: ...]".
            context: where,
            session_id: sessionId,
            automation_id: state.openAutomation || 0
          }).catch(function (e) {
            if (!retried && e.status === 404 && e.message === 'That chat was not found') {
              writeStudioSession(null);
              return ensureChatSession().then(function (id) {
                if (!id) throw e;
                return send(id, true);
              });
            }
            throw e;
          });
        }
        ensureChatSession().then(function (id) {
          if (!id) throw new Error('The chat could not be started.');
          return send(id, false);
        }).then(function (d) {
          if (d.session_id) writeStudioSession(d.session_id);
          watchJob(d.job_id, el('span', 'activity running'), function () {
            api('GET', '/api/jobs/' + d.job_id + '/events?after=0').then(function (r) {
              var said = (r.events || []).filter(function (ev) {
                return ev.source === 'Manager' && ev.type === 'message';
              }).pop();
              var result = (r.job && r.job.result) || '';
              var answer = said ? said.content
                : (result || ('The assistant could not answer: ' +
                  (jobFailure(r.events) || ('open job #' + d.job_id + ' to see why.'))));
              if (wait.parentNode) wait.parentNode.removeChild(wait);
              say('them', answer);
            }).catch(function () { wait.textContent = 'Something went wrong.'; });
          });
        }).catch(function (e) { wait.textContent = e.message; });
      }

      go.onclick = function () {
        var text = (input.value || '').trim();
        if (!text) return;
        input.value = '';
        ask(text);
      };
      input.onkeydown = function (event) {
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); go.click(); }
      };

      api('GET', '/api/voice/status').then(function (s) {
        if (!s.available) return;
        mic.style.display = '';
        mic.onclick = function () {
          // The sheet stays for typing; Talk hands the screen to the one voice mode. Final
          // turns land in this log and in the same session the typed chat uses.
          if (!window.AscendVoice) { toast('The voice client did not load.', false); return; }
          if (window.AscendVoice.running()) { window.AscendVoice.restore(); return; }
          ensureChatSession().then(function (id) {
            if (!id) { toast('The chat could not be started.', false); return; }
            window.AscendVoice.open({
              page: page,
              source: 'sheet',
              sessionId: id,
              onSession: function (next) { writeStudioSession(next); },
              onState: function (phase, label) { mic.title = label; },
              onTurn: function (turn) {
                say(turn.role === 'user' ? 'me' : 'them', turn.text);
              },
              onJob: function (jobId) {
                say('them', 'The team is working - job #' + jobId);
              }
            });
          });
        };
      }).catch(function () { /* text still works */ });
    }

    orb.onclick = open;

    // The Studio is the full version of this sheet; a second floating door over its
    // own composer is just clutter. Every other screen keeps the quick entry point.
    function syncOrbVisibility() {
      var hash = location.hash || '';
      var onStudio = hash.indexOf('#/app/studio') === 0;
      // A kept Studio already has a door (the strip), so a second floating one would
      // only cover the page it is meant to sit beside.
      var kept = !!studioKept.node && hash.indexOf('#/app/') === 0;
      orb.style.display = (onStudio || kept) ? 'none' : '';
      if (onStudio || kept) closeAll();
    }
    syncOrbAfterRender = syncOrbVisibility;
    window.addEventListener('hashchange', syncOrbVisibility);
    syncOrbVisibility();
  }

  // ---------- Onboarding tours ----------
  // The Studio walkthrough is deliberately only four stops. It names the four things a
  // newcomer must recognise (where to ask, what the canvas is targeting, where the work
  // appears, where the result is reviewed) and then gets out of the way.
  var STUDIO_TOUR_VERSION = '2026-11-studio';

  function studioTourSteps() {
    return [
      { hash: '#/app/studio', target: '[data-tour="studio-composer"]',
        title: 'Start with a plain request',
        text: 'Tell the team what you want in plain English: build a page, rewrite a ' +
              'headline, add a form. Type here, or press Talk and say it. Both go into ' +
              'the same conversation.' },
      { hash: '#/app/studio', target: '[data-tour="studio-working-on"]',
        title: 'Name the page',
        text: 'Working on shows the draft the team is editing. Create something starts ' +
              'a new page; Choose opens an existing draft. The page stays on the canvas ' +
              'while you talk about it.' },
      { hash: '#/app/studio', target: '[data-tour="studio-live"]',
        title: 'Watch the team work',
        text: 'Live job shows each step, any question the team needs answered, and the ' +
              'approvals that keep sensitive actions in your hands. Answer in the chat, ' +
              'or by voice.' },
      { hash: '#/app/studio', target: '[data-tour="studio-preview"]',
        title: 'Review the result',
        text: 'Preview updates when the team saves. Use Mobile to check the phone ' +
              'layout and Open full to see the page properly. Chats on the left keep ' +
              'each job separate; New starts a blank canvas.' }
    ];
  }

  // A first-timer's five stops. Shown once per browser; the Studio walkthrough has its
  // own versioned, per-account completion so a new device and a new guide behave
  // predictably.
  function tourSteps() {
    if (isOwner(state.user)) {
      return [
        { hash: '#/app/studio', title: 'Ask, and it builds',
          text: 'Every job is a chat: type or talk in plain English, and the page it is ' +
                'changing stays on the canvas beside it. New starts a fresh page; the chats ' +
                'are on the left.' },
        { hash: '#/app/sites', title: 'Websites',
          text: 'Each client gets a site here. Publish a page, and the domain points at this ' +
                'server.' },
        { hash: '#/app/automations', title: 'Automations',
          text: 'Tell the assistant what should happen, like “when someone sends the Tour ' +
                'Enquiry form, tag them as a tour enquiry”. It writes it, and you approve it ' +
                'once.' },
        { hash: '#/app/forms', title: 'Forms, surveys and quizzes',
          text: 'Build one, publish it, and it lives at its own link. Answers land in the ' +
                'list, and a submission can start an automation.' },
        { hash: '#/app/domains', title: 'Domains',
          text: 'Type a domain and copy two records to your DNS provider. Press Check again ' +
                'until every row is green, then go live.' }
      ];
    }
    return [
      { hash: '#/app/studio', title: 'Ask for what you need',
        text: 'Type or talk in plain English. The page you are working on stays on the ' +
              'canvas beside the chat.' },
      { hash: '#/app/sites', title: 'Your website',
        text: 'Connect your domain and publish pages. We handle the secure certificate.' },
      { hash: '#/app/library', title: 'Your content',
        text: 'Blog posts, landing pages and emails live here, ready to review and publish.' },
      { hash: '#/app/forms', title: 'Forms and leads',
        text: 'Build a form, share its link, and new answers appear under Leads.' },
      { hash: '#/app/settings', title: 'Workspace settings',
        text: 'Add a mailbox if you want email to come from your own domain, and choose ' +
              'how much Analytics automation you want. Leave email blank to use the ' +
              'agency default.' }
    ];
  }

  function runTour(tour, options) {
    options = options || {};
    if (!tour || !tour.length) {
      if (options.onDone) options.onDone();
      return;
    }
    var index = 0;
    var completed = false;
    var activeTarget = null;
    var veil = el('div', 'tour-veil');
    var card = el('div', 'tour-card');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('tabindex', '-1');
    veil.appendChild(card);
    document.body.appendChild(veil);

    function onKey(event) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      markSeen();
      close(true);
    }
    document.addEventListener('keydown', onKey);

    function clearTarget() {
      if (!activeTarget) return;
      try { activeTarget.classList.remove('tour-focus'); } catch (e) { /* detached */ }
      activeTarget = null;
    }

    function close(skipped) {
      if (completed) return;
      completed = true;
      clearTarget();
      document.removeEventListener('keydown', onKey);
      if (window.AscendVoice && window.AscendVoice.running()) window.AscendVoice.stop();
      if (veil.parentNode) veil.parentNode.removeChild(veil);
      var callback = skipped ? options.onSkip : options.onFinish;
      if (callback) callback();
      else if (options.onDone) options.onDone();
    }

    function markSeen() {
      if (!options.storeKey) return;
      try { localStorage.setItem(options.storeKey, '1'); } catch (e) { /* private mode */ }
    }

    function centreCard() {
      card.className = 'tour-card';
      card.style.left = '';
      card.style.top = '';
      card.style.right = '';
      card.style.bottom = '';
    }

    function placeCard(step) {
      clearTarget();
      if (!step.target) { centreCard(); return; }
      var target = document.querySelector(step.target);
      if (!target) { centreCard(); return; }
      activeTarget = target;
      try { target.classList.add('tour-focus'); } catch (e) { /* detached */ }
      try { target.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      catch (e) { /* older browser */ }
      card.className = 'tour-card tour-card-anchored';
      // Measure after the card has painted, then choose above or below the target.
      requestAnimationFrame(function () {
        var rect = target.getBoundingClientRect();
        var width = card.offsetWidth || 440;
        var height = card.offsetHeight || 220;
        var gap = 16;
        var left = Math.min(Math.max(gap, rect.left), window.innerWidth - width - gap);
        var top = rect.bottom + gap;
        if (top + height > window.innerHeight - gap) {
          top = Math.max(gap, rect.top - height - gap);
        }
        card.style.left = left + 'px';
        card.style.top = top + 'px';
        card.style.right = 'auto';
        card.style.bottom = 'auto';
      });
    }

    function waitForTarget(step, attempt) {
      if (!step.target || document.querySelector(step.target) || attempt >= 25) {
        placeCard(step);
        return;
      }
      setTimeout(function () { waitForTarget(step, attempt + 1); }, 80);
    }

    function show() {
      var step = tour[index];
      if (step.hash) location.hash = step.hash;
      clearTarget();
      card.innerHTML = '';
      card.appendChild(el('p', 'fine-print',
        'Step ' + (index + 1) + ' of ' + tour.length));
      card.appendChild(el('h3', 'card-title', step.title));
      card.appendChild(el('p', 'muted', step.text));
      var row = el('div', 'chip-input');
      var talk = el('button', 'btn btn-ghost', 'Talk about this');
      talk.onclick = function () {
        if (!window.AscendVoice) return;
        window.AscendVoice.open({
          page: 'tour, step: ' + step.title,
          source: 'tour',
          // The scripted tour is not a chat. A real work request during it creates the
          // first session; the tour's own turns never enter the thread.
          persistTurns: false,
          onSession: function (id) { writeStudioSession(id); },
          onState: function (phase, label) { talk.textContent = label; },
          onTurn: function () {},
          onClose: function () { talk.textContent = 'Talk about this'; }
        });
      };
      var back = el('button', 'btn btn-ghost', 'Back');
      back.disabled = index === 0;
      back.onclick = function () {
        if (!index) return;
        index -= 1;
        show();
      };
      var skip = el('button', 'btn btn-ghost', 'Skip');
      skip.onclick = function () { markSeen(); close(true); };
      var next = el('button', 'btn btn-primary',
                    index === tour.length - 1 ? 'Finish' : 'Next');
      next.onclick = function () {
        if (index === tour.length - 1) { markSeen(); close(false); return; }
        index += 1;
        show();
      };
      [talk, back, skip, next].forEach(function (button) { row.appendChild(button); });
      card.appendChild(row);
      try { card.focus(); } catch (e) { /* not focusable */ }
      waitForTarget(step, 0);
    }
    show();
  }

  function startTour(force, options) {
    options = options || {};
    var key = 'tour:' + ((state.user && state.user.id) || 0);
    try {
      if (!force && localStorage.getItem(key)) {
        if (options.onDone) options.onDone();
        return;
      }
    } catch (e) {
      if (options.onDone) options.onDone();
      return;                     // private mode: no tour rather than a tour every load
    }
    var tour = tourSteps();
    if (options.skipStudio) {
      tour = tour.filter(function (step) {
        return step.hash !== '#/app/studio';
      });
    }
    if (!tour.length) {
      if (options.onDone) options.onDone();
      return;
    }
    runTour(tour, {
      storeKey: key,
      onFinish: function () {
        if (options.onDone) options.onDone();
      },
      onSkip: function () {
        if (options.onDone) options.onDone();
      }
    });
  }

  function studioTourCurrent() {
    return !!(state.user && (state.user.studio_tour_at || '').trim() &&
              (state.user.studio_tour_version || '') === STUDIO_TOUR_VERSION);
  }

  function markStudioTour() {
    if (!state.user) return;
    // Optimistic locally; the server timestamp is the cross-device truth.
    state.user.studio_tour_at = state.user.studio_tour_at || new Date().toISOString();
    state.user.studio_tour_version = STUDIO_TOUR_VERSION;
    api('POST', '/api/auth/studio-tour', {}).then(function (saved) {
      if (!saved) return;
      state.user.studio_tour_at = saved.studio_tour_at || state.user.studio_tour_at;
      state.user.studio_tour_version =
        saved.studio_tour_version || STUDIO_TOUR_VERSION;
    }).catch(function () { /* next login retries */ });
  }

  function showStudioTourComplete() {
    var veil = el('div', 'tour-veil');
    var card = el('div', 'tour-card');
    veil.appendChild(card);
    document.body.appendChild(veil);
    card.appendChild(el('p', 'fine-print', 'Studio walkthrough'));
    card.appendChild(el('h3', 'card-title', 'You\'re ready to use the Studio'));
    card.appendChild(el('p', 'muted',
      'Start with a plain request. The team works in the chat, keeps the page on the ' +
      'canvas and asks before anything sensitive happens.'));
    var row = el('div', 'chip-input');
    var start = el('button', 'btn btn-primary', 'Start in Studio');
    start.onclick = function () {
      if (veil.parentNode) veil.parentNode.removeChild(veil);
      try { location.hash = '#/app/studio'; } catch (e) { /* blocked */ }
    };
    var rest = el('button', 'btn btn-ghost', 'Show me the rest');
    rest.onclick = function () {
      if (veil.parentNode) veil.parentNode.removeChild(veil);
      startTour(true, { skipStudio: true });
    };
    var skip = el('button', 'btn btn-ghost', 'Skip for now');
    skip.onclick = function () { if (veil.parentNode) veil.parentNode.removeChild(veil); };
    row.appendChild(start);
    row.appendChild(rest);
    row.appendChild(skip);
    card.appendChild(row);
  }

  function startStudioTour(force, onDone, showComplete) {
    if (!force && studioTourCurrent()) {
      if (onDone) onDone();
      return;
    }
    runTour(studioTourSteps(), {
      onFinish: function () {
        markStudioTour();
        if (showComplete) showStudioTourComplete();
        else if (onDone) onDone();
      },
      onSkip: function () {
        markStudioTour();
        if (onDone) onDone();
      }
    });
  }

  // ---------- The first login: a short spoken welcome ----------
  // Marked per account (users.onboarded_at), not per browser, so a second device does not
  // replay it and a cleared localStorage cannot bring it back. The tour stays separate and
  // follows when the voice overlay closes.
  var INTRO_TEXT =
    'Ask for anything - a page, an email, a whole site - and the team builds it in the ' +
    'Studio, with the work on the canvas beside the chat. Website and Domains put a page on ' +
    'your own domain and handle the secure certificate. Forms turn answers into leads, ' +
    'and you choose when anything is sent.';

  function markOnboarded() {
    if (!state.user || state.user.onboarded_at) return;
    state.user.onboarded_at = new Date().toISOString();
    api('POST', '/api/auth/onboarded', {}).catch(function () { /* next login retries */ });
  }

  // The same words the voice is asked to say, for a browser with no voice configured and
  // for anyone who cannot use a microphone.
  function showTextIntro(after) {
    var veil = el('div', 'tour-veil');
    var card = el('div', 'tour-card');
    veil.appendChild(card);
    document.body.appendChild(veil);
    card.appendChild(el('p', 'fine-print', 'Welcome'));
    card.appendChild(el('h3', 'card-title', 'A quick look at ' + BRAND));
    card.appendChild(el('p', 'muted', INTRO_TEXT));
    var row = el('div', 'chip-input');
    var skip = el('button', 'btn btn-ghost', 'Not now');
    skip.onclick = function () {
      if (veil.parentNode) veil.parentNode.removeChild(veil);
      markStudioTour();
    };
    var go = el('button', 'btn btn-primary', 'Start the walkthrough');
    go.onclick = function () {
      if (veil.parentNode) veil.parentNode.removeChild(veil);
      after();
    };
    row.appendChild(skip);
    row.appendChild(go);
    card.appendChild(row);
  }

  function firstRun() {
    if (!state.user) return;
    // An account that has never seen the welcome gets it first. An existing account that
    // predates this walkthrough goes straight to the Studio guide without repeating the
    // welcome. Completion is versioned and per account.
    if (state.user.onboarded_at) {
      if (!studioTourCurrent()) startStudioTour(false, null, true);
      else startTour(false, { skipStudio: true });
      return;
    }
    markOnboarded();
    var after = function () { startStudioTour(false, null, true); };
    api('GET', '/api/voice/status').then(function (s) {
      if (s && s.available && window.AscendVoice) {
        window.AscendVoice.open({
          page: 'welcome',
          source: 'welcome',
          intro: true,
          // The welcome is a one-off introduction, not a chat. If the user asks for
          // real work, the server starts the thread and the voice client hands us its id.
          persistTurns: false,
          onSession: function (id) { writeStudioSession(id); },
          onClose: after
        });
        return;
      }
      showTextIntro(after);
    }).catch(function () { showTextIntro(after); });
  }

  window.addEventListener('hashchange', render);

  api('GET', '/api/health').then(function (d) {
    // The product name comes from the server (BRAND_NAME), so renaming is a
    // Settings-tab change rather than a code edit.
    if (d && d.brand) BRAND = d.brand;
  }).catch(function () { /* keep the compiled-in default */ }).then(function () {
    return api('GET', '/api/auth/me');
  }).then(function (d) {
    state.user = d.user;
    navVeil();
    // The Ask sheet is the single floating entry point. Its header links to the full
    // Studio, so the old manager panel is no longer booted alongside it.
    assistOrb();
    render();
    // A first login gets the short spoken welcome, then the tour.
    firstRun();
  }).catch(function () {
    state.user = null;
    render();
  });
})();
