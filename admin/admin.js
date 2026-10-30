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
      home: '#/app/',
      nav: [
        { id: 'dashboard', label: 'Dashboard', icon: 'dashboard', hash: '#/app/dashboard' },
        { id: 'assistant', label: 'Assistant', icon: 'chat', hash: '#/app/assistant' },
        { id: 'analytics', label: 'Analytics', icon: 'trend', hash: '#/app/analytics' },
        { id: 'automations', label: 'Automations', icon: 'cog', hash: '#/app/automations' },
        { id: 'sites', label: 'Websites', icon: 'globe', hash: '#/app/sites' },
        { id: 'domains', label: 'Domains', icon: 'server', hash: '#/app/domains' },
        { id: 'contacts', label: 'Contacts', icon: 'user', hash: '#/app/contacts' },
        { id: 'forms', label: 'Forms', icon: 'form', hash: '#/app/forms' },
        { id: 'media', label: 'Media', icon: 'image', hash: '#/app/media' },
        { id: 'blogs', label: 'Blogs', icon: 'pen', hash: '#/app/blogs' },
        { id: 'funnels', label: 'Funnels', icon: 'funnel', hash: '#/app/funnels' },
        { id: 'emails', label: 'Emails', icon: 'mail', hash: '#/app/emails' },
        { id: 'library', label: 'Library', icon: 'library', hash: '#/app/library' },
        { id: 'bots', label: 'Chatbots', icon: 'bot', hash: '#/app/bots' },
        { id: 'conversations', label: 'Conversations', icon: 'chat', hash: '#/app/conversations' },
        { id: 'profile', label: 'Business Profile', icon: 'building', hash: '#/app/profile' },
        { id: 'jobs', label: 'Jobs', icon: 'cog', hash: '#/app/jobs' },
        { id: 'team', label: 'Team', icon: 'user', hash: '#/app/team', adminOnly: true },
        { id: 'settings', label: 'Settings', icon: 'sliders', hash: '#/app/settings', adminOnly: true }
      ]
    },
    platform: {
      label: 'Platform',
      home: '#/platform/clients',
      nav: [
        { id: 'clients', label: 'Clients', icon: 'store', hash: '#/platform/clients' },
        { id: 'email', label: 'Email & invites', icon: 'mail', hash: '#/platform/email' },
        { id: 'sources', label: 'Data sources', icon: 'dashboard', hash: '#/platform/sources' },
        { id: 'servers', label: 'Servers', icon: 'server', hash: '#/platform/servers' },
        { id: 'agents', label: 'Agents', icon: 'cpu', hash: '#/platform/agents' },
        { id: 'settings', label: 'Platform settings', icon: 'sliders', hash: '#/platform/settings' }
      ]
    }
  };

  // Flat view of both areas, so the "coming soon" helper keeps working.
  var NAV = AREAS.app.nav.concat(AREAS.platform.nav);

  function currentNavId(parts) {
    var first = parts[1] || '';
    if (first === 'bot') return 'bots';
    return first || 'dashboard';
  }

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

    aside.appendChild(el('div', 'side-heading', AREAS[area].label));

    var nav = el('nav', 'side-nav');
    AREAS[area].nav.forEach(function (item) {
      if (item.adminOnly && !canAdmin(state.user)) return;
      var link = el('a', 'side-item' + (item.id === tab ? ' active' : ''));
      link.href = item.hash;
      if (item.id === tab) link.setAttribute('aria-current', 'page');
      link.innerHTML = '<span class="side-icon">' + icon(item.icon) + '</span>' +
        '<span class="side-label">' + esc(item.label) + '</span>' +
        (item.ready === false ? '<span class="side-soon">Soon</span>' : '');
      link.onclick = function () { document.body.classList.remove('nav-open'); };
      nav.appendChild(link);
    });
    aside.appendChild(nav);

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
    return bar;
  }

  // ---------- Login ----------
  function viewLogin() {
    var wrap = el('div', 'auth-wrap');
    var card = el('div', 'auth-card');
    card.innerHTML = '<img class="auth-logo" src="/admin/logo-trim.png" alt="' +
      esc(BRAND) + '" /><h1>' + esc(BRAND) +
      '</h1><p class="sub">Sign in to your growth platform</p>';
    var err = el('div', 'error');
    var emailField = el('div', 'field');
    emailField.innerHTML = '<label>Email</label>';
    var email = el('input'); email.type = 'email'; email.placeholder = 'you@example.com';
    emailField.appendChild(email);
    var pwField = el('div', 'field');
    pwField.innerHTML = '<label>Password</label>';
    var pw = el('input'); pw.type = 'password';
    pwField.appendChild(pw);
    var btn = el('button', 'btn btn-primary', 'Sign in');
    btn.style.width = '100%';
    btn.onclick = submit;
    function submit() {
      err.textContent = '';
      btn.disabled = true;
      api('POST', '/api/auth/login', { email: email.value, password: pw.value })
        .then(function (d) { state.user = d.user; render(); })
        .catch(function (e) { err.textContent = e.message; })
        .finally(function () { btn.disabled = false; });
    }
    card.appendChild(err);
    card.appendChild(emailField);
    card.appendChild(pwField);
    card.appendChild(btn);
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

  function viewDashboard() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Dashboard</h2><p>Your platform at a glance.</p></div>';
    container.appendChild(head);

    var grid = el('div', 'stats-grid');
    container.appendChild(grid);

    var panel = el('div', 'panel-card');
    panel.innerHTML = '<h3>Recent jobs</h3>';
    var recent = el('div');
    panel.appendChild(recent);
    container.appendChild(panel);

    Promise.all([api('GET', '/api/bots'), api('GET', '/api/jobs'), api('GET', '/api/profile')])
      .then(function (results) {
        var bots = results[0].bots || [];
        var jobs = results[1].jobs || [];
        var prof = results[2].profile || {};
        var messages = bots.reduce(function (sum, b) { return sum + (b.message_count || 0); }, 0);

        grid.innerHTML = '';
        grid.appendChild(statCard(bots.length, 'Chatbots'));
        grid.appendChild(statCard(messages, 'Messages'));
        grid.appendChild(statCard(jobs.length, 'Agent jobs'));
        grid.appendChild(statCard(prof.name ? 'Ready' : 'Empty', 'Business Profile'));

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
    var head = el('div', 'page-head');
    var hwrap = el('div');
    hwrap.innerHTML = '<h2>Chatbots</h2><p>Create and manage chatbots for your clients.</p>';
    head.appendChild(hwrap);
    var newBtn = el('button', 'btn btn-primary', '+ New Chatbot');
    newBtn.onclick = function () { location.hash = '#/bot/new'; };
    head.appendChild(newBtn);
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
        var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
        edit.onclick = function () { location.hash = '#/bot/' + bot.id; };
        var stats = el('button', 'btn btn-ghost btn-sm', 'Stats');
        stats.onclick = function () { location.hash = '#/bot/' + bot.id + '/stats'; };
        var conv = el('button', 'btn btn-ghost btn-sm', 'Chats');
        conv.onclick = function () { location.hash = '#/bot/' + bot.id + '/conversations'; };
        var del = el('button', 'btn btn-danger btn-sm', 'Delete');
        del.onclick = function () {
          if (confirm('Delete "' + bot.name + '" and all its messages?')) {
            api('DELETE', '/api/bots/' + bot.id).then(function () { toast('Deleted', true); render(); }).catch(function (e) { toast(e.message, false); });
          }
        };
        actions.appendChild(embed);
        actions.appendChild(edit);
        actions.appendChild(stats);
        actions.appendChild(conv);
        actions.appendChild(del);
        card.appendChild(actions);
        grid.appendChild(card);
      });
    }).catch(function (e) { toast(e.message, false); });

    return container;
  }

  // ---------- Meta channels ----------
  var PLATFORM_LABELS = { messenger: 'Messenger', instagram: 'Instagram', whatsapp: 'WhatsApp' };

  function buildChannelsPanel(botId) {
    var card = el('div', 'panel-card');
    card.style.marginTop = '20px';
    card.appendChild(el('h3', null, 'Meta channels (Messenger / Instagram / WhatsApp)'));
    card.appendChild(el('div', 'muted', 'Route DMs from a Facebook Page, Instagram professional account, or WhatsApp Business number to this bot.'));

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
    card.appendChild(cbField);

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
    card.appendChild(form);

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
          '<span>ID ' + esc(ch.external_id) + '</span>' +
          '<span class="badge ' + (ch.enabled ? 'badge-admin' : 'badge-member') + '">' + (ch.enabled ? 'enabled' : 'disabled') + '</span>';
        row.appendChild(head);
        var creds = el('div', 'bot-card-assistant');
        creds.textContent = 'Token: ' + (ch.has_token ? 'set' : 'missing') + ' · App secret: ' + (ch.has_app_secret ? 'set' : 'missing');
        row.appendChild(creds);
        var vt = el('div', 'bot-card-assistant');
        vt.innerHTML = 'Verify token: <code>' + esc(ch.verify_token) + '</code>';
        row.appendChild(vt);
        var acts = el('div', 'card-actions');
        acts.style.marginTop = '8px';
        var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
        edit.onclick = function () {
          editingId = ch.id;
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

  // ---------- Users ----------
  function viewUsers() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Users</h2><p>Team accounts (admin only).</p></div>';
    container.appendChild(head);

    var form = el('div', 'form-card');
    form.innerHTML = '<h3>Add user</h3>';
    function f(label, input) { var d = el('div', 'field'); d.innerHTML = '<label>' + label + '</label>'; d.appendChild(input); return d; }
    var name = el('input'); name.placeholder = 'Name';
    var email = el('input'); email.type = 'email'; email.placeholder = 'Email';
    var pw = el('input'); pw.type = 'password'; pw.placeholder = 'Password';
    var role = el('select'); role.innerHTML = '<option value="member">Member</option><option value="admin">Admin</option>';
    form.appendChild(f('Name', name));
    form.appendChild(f('Email', email));
    form.appendChild(f('Password', pw));
    form.appendChild(f('Role', role));
    var addBtn = el('button', 'btn btn-primary', 'Add User');
    addBtn.onclick = function () {
      api('POST', '/api/users', { name: name.value, email: email.value, password: pw.value, role: role.value })
        .then(function () { toast('User added', true); render(); })
        .catch(function (e) { toast(e.message, false); });
    };
    form.appendChild(addBtn);
    container.appendChild(form);

    var tableWrap = el('div', 'mt');
    container.appendChild(tableWrap);

    api('GET', '/api/users').then(function (d) {
      var users = d.users || [];
      var table = el('table', 'table');
      table.innerHTML = '<thead><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr></thead>';
      var tbody = el('tbody');
      users.forEach(function (u) {
        var tr = el('tr');
        tr.innerHTML = '<td>' + esc(u.name) + '</td><td>' + esc(u.email) + '</td><td><span class="badge ' + (u.role === 'member' ? 'badge-member' : 'badge-admin') + '">' + esc(u.role) + '</span></td>';
        var td = el('td');
        if (u.id !== state.user.id) {
          var del = el('button', 'btn btn-danger btn-sm', 'Remove');
          del.onclick = function () {
            if (confirm('Remove ' + u.email + '?')) {
              api('DELETE', '/api/users/' + u.id).then(function () { toast('Removed', true); render(); }).catch(function (e) { toast(e.message, false); });
            }
          };
          td.appendChild(del);
        }
        tr.appendChild(td);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      tableWrap.innerHTML = '';
      tableWrap.appendChild(table);
    }).catch(function (e) { toast(e.message, false); });

    return container;
  }

  // ---------- Conversations ----------
  function fmtDate(s) {
    if (!s) return '';
    var d = new Date(s.replace(' ', 'T') + 'Z');
    return isNaN(d.getTime()) ? s : d.toLocaleString();
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
        listEl.appendChild(el('div', 'empty-state', showLeadsOnly ? 'No leads captured yet.' : 'No conversations yet.'));
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

  function viewSettings() {
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

  function saveGroup(group, inputs, refresh) {
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

    api('PUT', '/api/settings', { values: values }).then(function (r) {
      toast('Saved ' + ((r.changed || []).length) + ' setting(s)', true);
      refresh();
    }).catch(function (e) { toast(e.message, false); });
  }

  function clearOne(key, refresh) {
    if (!confirm('Clear ' + key + ' and fall back to the environment?')) return;
    api('PUT', '/api/settings', { clear: [key] }).then(function () {
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
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Business Profile</h2><p>Everything here feeds your AI as its knowledge base.</p></div>';
    container.appendChild(head);

    var inputs = {};
    var services = [];
    var faq = [];
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

    var identity = section('Identity');
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

    var branding = section('Branding', 'Used by your chatbot widget and any content the AI generates.');
    branding.appendChild(row(
      field('Brand colour', 'brand_color', { type: 'color' }),
      field('Font family', 'brand_font', { placeholder: 'Inter, Poppins...' })
    ));
    branding.appendChild(field('Logo URL', 'logo_url', { placeholder: 'https://example.com/logo.png' }));
    container.appendChild(branding);

    var voice = section('Audience & voice');
    voice.appendChild(field('Target audience', 'target_audience', {
      multiline: true, placeholder: 'Small business owners in the UK who want more leads.'
    }));
    voice.appendChild(field('Tone of voice', 'tone_of_voice', {
      multiline: true, placeholder: 'Friendly, concise, never pushy.'
    }));
    container.appendChild(voice);

    // Offerings
    var offerings = section('Offerings & pricing');
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

    var contact = section('Contact');
    contact.appendChild(row(
      field('Support email', 'support_email', { placeholder: 'hello@example.com' }),
      field('Support phone', 'support_phone', { placeholder: '+44 ...' })
    ));
    contact.appendChild(field('Address', 'address', { placeholder: '123 High Street, London' }));
    contact.appendChild(field('Business hours', 'business_hours', { placeholder: 'Mon-Fri 9:00-17:00' }));
    container.appendChild(contact);

    var socialCard = section('Social links');
    var socialRow = el('div', 'form-row');
    SOCIAL_KEYS.forEach(function (key) {
      socialRow.appendChild(field(key.charAt(0).toUpperCase() + key.slice(1), 'soc_' + key, { placeholder: 'https://' }));
    });
    socialCard.appendChild(socialRow);
    container.appendChild(socialCard);

    var policiesCard = section('Policies');
    policiesCard.appendChild(field('Refunds, shipping, guarantees', 'policies', { multiline: true }));
    container.appendChild(policiesCard);

    var faqCard = section('FAQ');
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

    var notesCard = section('Additional notes');
    notesCard.appendChild(field('Anything else the AI should know', 'extra_notes', { multiline: true }));
    container.appendChild(notesCard);

    var previewCard = section('Knowledge base preview', 'This is the exact text your AI receives as its knowledge base.');
    var previewBody = el('pre', 'kb-preview');
    previewBody.style.display = 'none';
    var previewMeta = el('div', 'muted');
    var toggle = el('button', 'btn btn-ghost btn-sm', 'Show');
    toggle.onclick = function () {
      var hidden = previewBody.style.display === 'none';
      previewBody.style.display = hidden ? 'block' : 'none';
      toggle.textContent = hidden ? 'Hide' : 'Show';
    };
    var refresh = el('button', 'btn btn-ghost btn-sm', 'Refresh');
    refresh.onclick = function () { refreshPreview(); };
    var previewBar = el('div', 'chip-input');
    previewBar.appendChild(toggle);
    previewBar.appendChild(refresh);
    previewCard.appendChild(previewBar);
    previewCard.appendChild(previewMeta);
    previewCard.appendChild(previewBody);
    container.appendChild(previewCard);

    var actions = el('div');
    actions.style.cssText = 'display:flex;gap:8px;margin-bottom:24px;';
    var saveBtn = el('button', 'btn btn-primary', 'Save profile');
    var sampleBtn = el('button', 'btn btn-ghost', 'Load sample content');
    actions.appendChild(saveBtn);
    actions.appendChild(sampleBtn);
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
      renderServices();
      renderFaq();
    }

    saveBtn.onclick = function () {
      saveBtn.disabled = true;
      api('PUT', '/api/profile', collect())
        .then(function (d) { toast('Profile saved', true); load(d.profile); refreshPreview(); })
        .catch(function (e) { toast(e.message, false); })
        .finally(function () { saveBtn.disabled = false; });
    };

    sampleBtn.onclick = function () {
      if (!confirm('Replace the current profile with sample content?')) return;
      api('POST', '/api/profile/seed-sample')
        .then(function (d) { toast('Sample loaded', true); load(d.profile); refreshPreview(); })
        .catch(function (e) { toast(e.message, false); });
    };

    renderServices();
    renderFaq();
    api('GET', '/api/profile')
      .then(function (d) { load(d.profile); refreshPreview(); })
      .catch(function (e) { toast(e.message, false); });



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

  function siteStatusBadge(status) {
    return '<span class="badge ' + (SITE_STATUS_CLASS[status] || 'badge-status') + '">' + esc(status) + '</span>';
  }

  function viewSites() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Sites</h2><p>Point a domain at this server and the platform serves it over HTTPS.</p></div>';
    var addBtn = el('button', 'btn btn-primary', '+ New site');
    addBtn.onclick = function () { siteEditor(null, refresh); };
    head.appendChild(addBtn);
    container.appendChild(head);

    var banner = el('div', 'panel-card');
    container.appendChild(banner);
    var grid = el('div', 'grid');
    container.appendChild(grid);

    function refresh() {
      api('GET', '/api/sites').then(function (d) {
        SITE_TARGETS = d.targets || [];
        banner.innerHTML = '<h3>How this works</h3><div class="muted">' +
          (d.dry_run
            ? 'Dry-run: nothing is written to disk or the web server until you set <code>ALLOW_LIVE_ACTIONS=1</code>.'
            : 'Live: provisioning runs the real commands and may ask for approval.') +
          (d.server_ip
            ? '<br>Server IP: <b>' + esc(d.server_ip) + '</b>'
            : '<br>Set <code>SERVER_IP</code> so the DNS hints are accurate.') +
          '</div>';
        grid.innerHTML = '';
        var sites = d.sites || [];
        if (!sites.length) {
          grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">' + icon('globe') + '</div><strong>No websites yet</strong><p>Add a domain and point its DNS at this server to go live.</p></div>';
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
    var card = el('div', 'bot-card');
    var top = el('div', 'bot-card-top');
    var av = el('div', 'bot-avatar');
    av.style.background = site.status === 'live' ? '#16a34a' : (site.status === 'error' ? '#dc2626' : '#4f46e5');
    av.innerHTML = icon('globe');
    top.appendChild(av);
    var names = el('div');
    names.innerHTML = '<div class="bot-card-name">' + esc(site.domain) + '</div>' +
      '<div class="bot-card-assistant">' + esc(site.target) + ' target</div>';
    top.appendChild(names);
    card.appendChild(top);

    var meta = el('div', 'bot-card-meta');
    meta.innerHTML = '<span>' + siteStatusBadge(site.status) + '</span>' +
      '<span>ssl: ' + esc(site.ssl_status) + '</span>';
    card.appendChild(meta);

    if (site.url) {
      var link = el('div');
      link.innerHTML = '<a href="' + esc(site.url) + '" target="_blank" rel="noopener">' + esc(site.url) + '</a>';
      card.appendChild(link);
    }
    if (site.last_error) card.appendChild(el('div', 'muted', site.last_error));
    if (site.dns_last_ip) card.appendChild(el('div', 'muted', 'resolves to ' + esc(site.dns_last_ip)));

    var actions = el('div', 'card-actions');
    var dns = el('button', 'btn btn-ghost btn-sm', 'DNS & SSL');
    // One implementation, in the Domains tab - this used to open a second, older modal.
    dns.onclick = function () { location.hash = '#/app/domains/' + site.id; };
    var check = el('button', 'btn btn-ghost btn-sm', 'Check DNS');
    check.onclick = function () {
      api('POST', '/api/sites/' + site.id + '/check').then(function (d) {
        toast(d.check.ok ? 'DNS points here' : 'DNS: ' + d.check.reason, d.check.ok);
        refresh();
      }).catch(function (e) { toast(e.message, false); });
    };
    var provision = el('button', 'btn btn-primary btn-sm', 'Provision');
    provision.onclick = function () {
      api('POST', '/api/sites/' + site.id + '/provision').then(function (d) {
        toast('Provisioning queued', true);
        location.hash = '#/jobs/' + d.job.id;
      }).catch(function (e) { toast(e.message, false); });
    };
    var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
    edit.onclick = function () { siteEditor(site, refresh); };
    var del = el('button', 'btn btn-danger btn-sm', 'Delete');
    del.onclick = function () {
      if (!confirm('Delete ' + site.domain + '?')) return;
      api('DELETE', '/api/sites/' + site.id)
        .then(function () { toast('Deleted', true); refresh(); })
        .catch(function (e) { toast(e.message, false); });
    };
    actions.appendChild(dns);
    actions.appendChild(check);
    actions.appendChild(provision);
    actions.appendChild(edit);
    actions.appendChild(del);
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
    site = site || {};
    var isNew = !site.id;
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide');
    modal.innerHTML = '<h3>' + (isNew ? 'New site' : 'Edit ' + esc(site.domain)) + '</h3>' +
      '<p class="muted">The domain must already be pointed here (or be pointed after you save).</p>';

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

    var linkedBot = el('input');
    linkedBot.value = site.linked_bot_id || '';
    linkedBot.placeholder = 'optional bot id to embed';

    modal.appendChild(field('Domain', domain));
    modal.appendChild(field('Target', target));
    modal.appendChild(field('Aliases', aliases));
    modal.appendChild(field('Docroot', docroot));
    modal.appendChild(field('Content HTML', content));
    modal.appendChild(field('Linked chatbot', linkedBot));

    var actions = el('div', 'modal-actions');
    var cancel = el('button', 'btn btn-ghost', 'Cancel');
    cancel.onclick = function () { backdrop.remove(); };
    var save = el('button', 'btn btn-primary', 'Save');
    save.onclick = function () {
      var payload = {
        domain: domain.value.trim().toLowerCase(),
        target: target.value,
        aliases: aliases.value.split(',').map(function (a) { return a.trim(); }).filter(Boolean),
        docroot: docroot.value,
        content_html: content.value,
        linked_bot_id: linkedBot.value
      };
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

  function viewJob(jobId) {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Job #' + esc(jobId) + '</h2><p class="muted">Live transcript</p></div>';
    var back = el('button', 'btn btn-ghost', '← Back');
    back.onclick = function () { location.hash = '#/jobs'; };
    head.appendChild(back);
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
  function formModal(title, fields, onSubmit) {
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
    var save = el('button', 'btn btn-primary', 'Save');
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

  // ---------- Clients (platform console: onboarding) ----------
  var PLANS = ['trial', 'standard', 'pro', 'enterprise'];
  var ORG_STATUSES = ['active', 'suspended', 'cancelled'];

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
          del.onclick = function () {
            if (!confirm('Delete "' + org.name + '"? Their logins must be removed first.')) return;
            api('DELETE', '/api/orgs/' + org.id)
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

  function showClientForm(org, done) {
    var isNew = !org;
    var fields = [
      { name: 'name', label: 'Client name', value: org ? org.name : '', placeholder: 'Acme Ltd' },
      { name: 'slug', label: 'Slug', value: org ? org.slug : '',
        help: 'Used in URLs and support. Generated from the name when blank.' },
      { name: 'plan', label: 'Plan', type: 'select', options: PLANS, value: org ? org.plan : 'standard' },
      { name: 'status', label: 'Status', type: 'select', options: ORG_STATUSES, value: org ? org.status : 'active' },
      { name: 'notes', label: 'Notes', type: 'textarea', value: org ? org.notes : '' }
    ];
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

  function viewClient(orgId) {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.appendChild(el('h1', null, 'Client'));
    var back = el('a', 'btn btn-ghost', '← All clients');
    back.href = '#/platform/clients';
    head.appendChild(back);
    container.appendChild(head);
    var body = el('div');
    container.appendChild(body);

    function refresh() {
      api('GET', '/api/orgs/' + orgId).then(function (d) {
        var org = d.org;
        var users = d.users || [];
        body.innerHTML = '';

        var card = el('div', 'panel');
        card.appendChild(el('h3', null, org.name));
        card.appendChild(el('p', 'muted', org.slug + ' · plan ' + org.plan + ' · ' + org.status));
        if (org.notes) card.appendChild(el('p', null, org.notes));
        var edit = el('button', 'btn btn-ghost btn-sm', 'Edit client');
        edit.onclick = function () { showClientForm(org, refresh); };
        card.appendChild(edit);
        body.appendChild(card);

        var panel = el('div', 'panel');
        var bar = el('div', 'panel-head');
        bar.appendChild(el('h3', null, 'Team (' + users.length + ')'));
        var add = el('button', 'btn btn-primary btn-sm', '+ Add login');
        add.onclick = function () { showOrgUserForm(org, refresh); };
        bar.appendChild(add);
        panel.appendChild(bar);

        if (!users.length) {
          panel.appendChild(el('p', 'muted', 'No logins yet - this client cannot sign in.'));
        } else {
          var table = el('table', 'table');
          table.innerHTML = '<thead><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr></thead>';
          var tbody = el('tbody');
          users.forEach(function (user) {
            var tr = el('tr');
            tr.innerHTML = '<td>' + esc(user.name) + '</td>' +
              '<td>' + esc(user.email) + '</td>' +
              '<td><span class="badge ' + (user.role === 'member' ? 'badge-member' : 'badge-admin') + '">' +
              esc(user.role) + '</span></td>';
            var td = el('td', 'row-actions');
            var reset = el('button', 'btn btn-ghost btn-sm', 'Set password');
            reset.onclick = function () { showPasswordForm(user); };
            td.appendChild(reset);
            tr.appendChild(td);
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          panel.appendChild(table);
        }
        body.appendChild(panel);
      }).catch(function (e) { toast(e.message, false); });
    }
    refresh();
    return container;
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
    blogs: { kind: 'blog', title: 'Blogs', blurb: 'Articles written for you and published onto a website.' },
    funnels: { kind: 'funnel', title: 'Funnels', blurb: 'Opt-in pages and the steps that follow them.' },
    emails: { kind: 'email', title: 'Emails', blurb: 'Campaigns and sequences sent to your list.' },
    library: { kind: null, title: 'Library', blurb: 'Everything the team has produced for you, in one place.' }
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
    var backdrop = el('div', 'modal-backdrop');
    var modal = el('div', 'modal modal-wide');
    modal.appendChild(el('h3', null, item.title || 'Preview'));
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
    var close = el('button', 'btn btn-primary', 'Close');
    close.onclick = function () { document.body.removeChild(backdrop); };
    actions.appendChild(copy);
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

  function sendPublish(item, siteId, onDone) {
    api('POST', '/api/deliverables/' + item.id + '/publish', { site_id: siteId })
      .then(function (res) {
        if (res.dry_run) {
          toast('Dry-run: would write ' + res.path + ' (set ALLOW_LIVE_ACTIONS=1)', true);
        } else {
          toast('Published to ' + res.url, true);
        }
        if (onDone) onDone(res);
      })
      .catch(function (e) { toast(e.message, false); });
  }

  function publishDeliverable(item, onDone) {
    api('GET', '/api/sites').then(function (d) {
      var sites = d.sites || [];
      if (!sites.length) {
        toast('Add a domain in the Websites tab first - a page needs somewhere to live', false);
        return;
      }
      if (sites.length === 1) {
        sendPublish(item, sites[0].id, onDone);
        return;
      }
      // The API refuses to guess when a client owns several sites.
      formModal('Publish "' + (item.title || 'page') + '"', [
        { name: 'domain', label: 'Site', type: 'select', options: sites.map(function (s) { return s.domain; }) }
      ], function (data, fail, ok) {
        var chosen = sites.filter(function (s) { return s.domain === data.domain; })[0];
        if (!chosen) { fail('Choose a site'); return; }
        api('POST', '/api/deliverables/' + item.id + '/publish', { site_id: chosen.id })
          .then(function () { ok(); sendPublish(item, chosen.id, onDone); })
          .catch(function (e) { fail(e.message); });
      });
    }).catch(function (e) { toast(e.message, false); });
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
      api('GET', '/api/deliverables?kind=website').then(function (d) {
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
            '<td>' + statusBadge(item.status) + '</td>';
          var td = el('td', 'row-actions');
          var prev = el('button', 'btn btn-ghost btn-sm', 'Preview');
          prev.onclick = function () { previewDeliverable(item); };
          var pub = el('button', 'btn btn-primary btn-sm', 'Publish');
          pub.onclick = function () { publishDeliverable(item, load); };
          var edit = el('button', 'btn btn-ghost btn-sm', 'Edit');
          edit.onclick = function () { showDeliverableForm('website', item, load); };
          td.appendChild(prev);
          td.appendChild(pub);
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
        table.innerHTML = '<thead><tr><th></th><th>Title</th><th>Kind</th>' +
          '<th>Created</th></tr></thead>';
        var tbody = el('tbody');
        var boxes = [];
        rows.forEach(function (item) {
          var tr = el('tr');
          var tick = el('td');
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
    head.appendChild(el('h1', null, meta.title));
    var add = el('button', 'btn btn-primary', '+ New draft');
    add.onclick = function () { showDeliverableForm(meta.kind || 'blog', null, refresh); };
    add.disabled = true;         // enabled once we know there is a client to own the draft
    head.appendChild(add);
    container.appendChild(head);
    container.appendChild(el('p', 'muted', meta.blurb));
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
      var url = '/api/deliverables' + (meta.kind ? '?kind=' + meta.kind : '');
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
          'Blogs ' + (counts.blog || 0) + ' · Funnels ' + (counts.funnel || 0) +
          ' · Emails ' + (counts.email || 0) + ' · Websites ' + (counts.website || 0) +
          ' · Everything ' + (counts.all || 0)));
        if (!items.length) {
          body.appendChild(el('p', 'muted',
            'Nothing here yet. The team writes these, and you can add a draft yourself.'));
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
            '<td><span class="badge badge-' + (item.status === 'published' ? 'admin' : 'member') + '">' +
            esc(item.status) + '</span></td>' +
            '<td class="muted">' + esc((item.updated_at || '').slice(0, 16)) + '</td>';
          var td = el('td', 'row-actions');
          var preview = el('button', 'btn btn-ghost btn-sm', 'Preview');
          preview.onclick = function () { previewDeliverable(item); };
          td.appendChild(preview);
          var open = el('button', 'btn btn-ghost btn-sm', 'Open');
          open.onclick = function () { showDeliverableForm(item.kind, item, refresh); };
          td.appendChild(open);
          if (item.kind === 'website') {
            var pub = el('button', 'btn btn-primary btn-sm', 'Publish');
            pub.onclick = function () { publishDeliverable(item, refresh); };
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
    var isNew = !item;
    formModal(isNew ? 'New ' + kind : item.title, [
      { name: 'title', label: 'Title', value: item ? item.title : '' },
      { name: 'kind', label: 'Kind', type: 'select', options: ['blog', 'funnel', 'email', 'website', 'asset'],
        value: item ? item.kind : kind },
      { name: 'status', label: 'Status', type: 'select',
        options: ['draft', 'scheduled', 'published', 'failed'], value: item ? item.status : 'draft' },
      { name: 'slug', label: 'Slug', value: item ? item.slug : '' },
      { name: 'summary', label: 'Summary', type: 'textarea', value: item ? item.summary : '' },
      { name: 'content', label: 'Content', type: 'textarea', value: item ? item.content : '' },
      { name: 'url', label: 'Published URL', value: item ? item.url : '',
        placeholder: 'https://acme.com/blog/post' }
    ], function (data, fail, ok) {
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
          empty.innerHTML = '<p><strong>No media yet.</strong></p>' +
            '<p class="muted">Upload an image, video or PDF to use it in an email or page.</p>';
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
    var wrap = el('div', 'container');
    var card = el('div', 'form-card');
    card.appendChild(el('h2', null, 'Set your password'));
    var who = el('p', 'muted', 'Checking your invitation…');
    card.appendChild(who);

    var password = el('input');
    password.type = 'password';
    password.placeholder = 'At least 8 characters';
    var confirm = el('input');
    confirm.type = 'password';
    confirm.placeholder = 'Repeat it';
    var error = el('div', 'form-error', '');
    var submit = el('button', 'btn btn-primary', 'Create password and sign in');
    [password, confirm, submit, error].forEach(function (node) { card.appendChild(node); });

    var usable = false;
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
    }).catch(function (e) {
      who.textContent = '';
      error.textContent = e.message;
    });

    submit.onclick = function () {
      if (!usable) return;
      if ((password.value || '').length < 8) {
        error.textContent = 'Use at least 8 characters';
        return;
      }
      if (password.value !== confirm.value) {
        error.textContent = 'The two passwords do not match';
        return;
      }
      submit.disabled = true;
      api('POST', '/api/invites/accept', { token: token, password: password.value })
        .then(function () {
          location.replace('#/');
          location.reload();
        })
        .catch(function (e) {
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
        var orgs = d.orgs || [];
        var names = ['(no client - platform staff)'].concat(
          orgs.map(function (o) { return o.name; })
        );
        formModal('Invite someone', [
          { name: 'email', label: 'Email address' },
          { name: 'name', label: 'Their name (optional)' },
          { name: 'org', label: 'Client', type: 'select', options: names },
          { name: 'role', label: 'Role', type: 'select',
            options: ['client_admin', 'client_member'] }
        ], function (data, fail, ok) {
          var chosen = orgs.filter(function (o) { return o.name === data.org; })[0];
          var payload = { email: data.email, name: data.name, role: data.role };
          if (chosen) payload.org_id = chosen.id;
          api('POST', '/api/invites', payload).then(function (res) {
            ok();
            linkModal('Invitation sent', 'Emailed to ' + res.invite.email + '.', res.link, false);
            load();
          }).catch(function (e) {
            var body = e.data || {};
            if (body.invite) {
              // Created, but the mail did not go out: the account and link are real,
              // so show them rather than making the operator start again.
              ok();
              linkModal('Invitation created, email not sent', e.message, body.link, true);
              load();
            } else {
              fail(e.message);
            }
          });
        });
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
          '<td><span class="badge">' + esc(invite.role) + '</span></td>' +
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
          var add = el('button', 'btn btn-primary', '+ Add source');
          add.onclick = function () { sourceForm(d.kinds, null); };
          body.appendChild(add);
          var sources = d.sources || [];
          if (!sources.length) {
            body.appendChild(el('p', 'muted', 'Nothing configured. Add the client\'s ' +
              'GHL sub-account, or their GA4 property, or read our own site logs.'));
            return;
          }
          var table = el('table', 'table');
          table.innerHTML = '<thead><tr><th>Kind</th><th>Account</th><th>Status</th>' +
            '<th>Last sync</th><th></th></tr></thead>';
          var tbody = el('tbody');
          sources.forEach(function (source) {
            var tr = el('tr');
            var problem = source.last_error
              ? '<div class="form-error">' + esc(source.last_error.slice(0, 90)) + '</div>'
              : '';
            tr.innerHTML = '<td><span class="badge">' + esc(source.kind) + '</span>' +
              '<div class="muted">' + esc(source.label) + '</div></td>' +
              '<td class="muted">' + esc(source.external_id || '—') + problem + '</td>' +
              '<td>' + (source.enabled ? 'enabled' : 'disabled') + '</td>' +
              '<td class="muted">' + esc((source.last_sync_at || 'never').slice(0, 16)) + '</td>';
            var td = el('td', 'row-actions');
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

    function sourceForm(kinds, source) {
      formModal(source ? 'Edit source' : 'Add a data source', [
        { name: 'kind', label: 'Kind', type: 'select',
          options: kinds.map(function (k) { return k.kind; }) },
        { name: 'external_id', label: 'Account / property / location id',
          value: source ? source.external_id : '',
          help: 'GHL: the location id. GA4: properties/123456789. Meta: act_1234567890.' },
        { name: 'label', label: 'Label (optional)', value: source ? source.label : '' },
        { name: 'credential_ref', label: 'Credential reference',
          value: source ? source.credential_ref : '',
          help: 'Path to a mounted key file. Secrets are references here, not values.' }
      ], function (data, fail, ok) {
        var payload = {
          kind: data.kind,
          external_id: data.external_id,
          label: data.label,
          credential_ref: data.credential_ref
        };
        if (state.org) payload.org_id = Number(state.org);
        api('POST', '/api/data-sources', payload).then(function () {
          ok();
          toast('Source saved', true);
          loadSources();
        }).catch(function (e) { fail(e.message); });
      });
    }

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
  function viewAnalytics() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Analytics</h2><p class="muted">This window against the ' +
      'one before it. Same numbers the weekly review reads.</p></div>';
    container.appendChild(head);

    // The tenant comes from the session, like every other page: the sidebar switcher is
    // the one control, so this page can never disagree with it.
    var body = el('div');
    container.appendChild(body);

    function money(n) {
      n = Number(n || 0);
      return n >= 10000 ? (n / 1000).toFixed(1) + 'k' : String(n);
    }

    function card(label, value, delta) {
      var box = el('div', 'panel-card kpi');
      var d = Number(delta || 0);
      var cls = d === 0 ? 'pill flat' : (d > 0 ? 'pill up' : 'pill down');
      box.innerHTML =
        '<div class="kpi-label">' + esc(label) + '</div>' +
        '<div class="kpi-value">' + esc(money(value)) + '</div>' +
        '<div class="' + cls + '">' + (d > 0 ? '▲ ' : (d < 0 ? '▼ ' : '')) +
          Math.abs(d) + '%</div>';
      return box;
    }

    function render() {
      var q = '/api/analytics/summary?days=7';
      api('GET', q).then(function (d) {
        body.innerHTML = '';
        var now = d.current || {};
        var change = d.change || {};
        if (!now.pageviews && !now.sessions) {
          var blank = el('div', 'panel-card empty-state');
          blank.innerHTML = '<div class="empty-icon">◔</div>' +
            '<p><strong>No traffic recorded yet.</strong></p>' +
            '<p class="muted">Sync site logs, or connect a GA4 property for this client.</p>';
          body.appendChild(blank);
          return;
        }
        var grid = el('div', 'kpi-grid');
        grid.appendChild(card('Pageviews', now.pageviews, change.pageviews));
        grid.appendChild(card('Sessions', now.sessions, change.sessions));
        grid.appendChild(card('Errors', now.errors, change.errors));
        body.appendChild(grid);
        body.appendChild(el('p', 'fine-print', 'Sources: ' + (d.sources || []).join(', ') +
          ' · since ' + esc(d.since)));

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
      }).catch(function (e) { toast(e.message, false); });
    }

    api('GET', '/api/session/org').then(function (state) {
      if (state && state.acting_org) { render(); return; }
      var hint = el('div', 'panel-card empty-state');
      hint.innerHTML = '<div class="empty-icon">◔</div>' +
        '<p><strong>Pick a client to see its traffic.</strong></p>' +
        '<p class="muted">Use the workspace switcher under the logo.</p>';
      body.appendChild(hint);
    }).catch(function () { render(); });

    return container;
  }

  // ---------- Assistant: a conversation, not a one-shot command ----------
  // Same endpoint and the same job underneath as the floating widget, but the thread
  // persists, so answering a question is typing in the same box rather than a new errand.
  function viewAssistant() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Assistant</h2><p class="muted">Describe what you want. It ' +
      'asks when the brief is thin, drafts when it is not, and keeps the thread.</p></div>';
    container.appendChild(head);

    var layout = el('div', 'asst-layout');
    var rail = el('aside', 'asst-rail');
    var railHead = el('div', 'asst-rail-head');
    railHead.appendChild(el('h3', 'asst-rail-title', 'Chats'));
    var newChat = el('button', 'btn btn-primary btn-sm', '+ New');
    railHead.appendChild(newChat);
    rail.appendChild(railHead);
    var sessionList = el('div', 'asst-session-list');
    rail.appendChild(sessionList);
    var importHost = el('div', 'asst-import-host');
    rail.appendChild(importHost);
    layout.appendChild(rail);

    var main = el('div', 'asst-main');
    var thread = el('div', 'asst-thread');
    main.appendChild(thread);

    var box = el('textarea', 'prompt-box');
    box.placeholder = 'Build me an automation that…';
    main.appendChild(box);
    var send = el('button', 'btn btn-primary', 'Send');
    main.appendChild(send);

    // Voice is offered only when the server says it can be: no key, or minutes spent, means
    // no microphone rather than a button that fails when pressed.
    var mic = el('button', 'btn btn-ghost', 'Talk');
    mic.style.display = 'none';
    var voiceNote = el('p', 'fine-print');
    main.appendChild(mic);
    main.appendChild(voiceNote);
    layout.appendChild(main);
    container.appendChild(layout);

    var activeSession = null;
    var sessions = [];
    var busy = false;
    var voiceSession = null;
    var voiceCreate = null;
    function setBusy(value) {
      busy = value;
      send.disabled = value;
      newChat.disabled = value;
    }

    api('GET', '/api/voice/status').then(function (s) {
      if (!s.available) {
        voiceNote.textContent = s.reason;
        return;
      }
      mic.style.display = '';
      voiceNote.textContent = 'Voice: ' + s.voice + ' · ' +
        s.remaining_minutes + ' min left today';
      mic.onclick = function () {
        // One voice screen, opened here. The page keeps its thread; the overlay owns the
        // microphone, the orb and the live transcript.
        if (!window.AscendVoice) { toast('The voice client did not load.', false); return; }
        if (window.AscendVoice.running()) { window.AscendVoice.restore(); return; }
        window.AscendVoice.open({
          page: 'assistant',
          source: 'assistant',
          onState: function (phase, label) { voiceNote.textContent = 'Voice: ' + label; },
          onTurn: function (turn) {
            bubble(turn.role === 'user' ? 'me' : 'them', turn.text);
            persistVoiceTurn(turn.role === 'user' ? 'user' : 'assistant', turn.text);
          },
          onJob: function (jobId) {
            voiceNote.textContent = 'The team is working - job #' + jobId;
          }
        });
      };
    }).catch(function () { /* no voice is not an error worth a toast */ });

    function oldThreadKey() {
      var orgKey = (state.user && state.user.org && state.user.org.id) || 'op';
      return 'chat:' + orgKey + ':' + ((state.user && state.user.id) || 0);
    }
    function readOldThread() {
      try {
        var data = JSON.parse(localStorage.getItem(oldThreadKey()) || 'null');
        if (!data || !Array.isArray(data.messages)) return [];
        return data.messages.filter(function (message) {
          return message && (message.role === 'user' || message.role === 'assistant') &&
            typeof message.content === 'string' && message.content.trim();
        });
      } catch (e) { return []; }
    }
    function shortTime(value) {
      if (!value) return '';
      var stamp = Date.parse(String(value).replace(' ', 'T') + 'Z');
      if (isNaN(stamp)) return String(value).slice(0, 10);
      var minutes = Math.max(0, Math.floor((Date.now() - stamp) / 60000));
      if (minutes < 1) return 'just now';
      if (minutes < 60) return minutes + 'm ago';
      var hours = Math.floor(minutes / 60);
      if (hours < 24) return hours + 'h ago';
      var days = Math.floor(hours / 24);
      if (days === 1) return 'Yesterday';
      if (days < 7) return days + 'd ago';
      return String(value).slice(0, 10);
    }
    function renderSessions() {
      sessionList.innerHTML = '';
      if (!sessions.length) {
        sessionList.appendChild(el('p', 'asst-rail-empty',
          'No chats yet. Ask something to start one.'));
        return;
      }
      sessions.forEach(function (session) {
        var button = el('button', 'asst-session' +
          (activeSession && activeSession.id === session.id ? ' active' : ''));
        button.type = 'button';
        button.appendChild(el('span', 'asst-session-title',
          session.title || 'New chat'));
        var meta = shortTime(session.updated_at);
        if (session.message_count) {
          meta += ' · ' + session.message_count + ' message' +
            (session.message_count === 1 ? '' : 's');
        }
        button.appendChild(el('span', 'asst-session-time', meta));
        button.onclick = function () { openSession(session.id); };
        sessionList.appendChild(button);
      });
    }
    function loadSessions(selectId) {
      return api('GET', '/api/assistant/sessions').then(function (d) {
        sessions = d.sessions || [];
        if (selectId) {
          var found = sessions.filter(function (s) { return s.id === selectId; })[0];
          if (found) activeSession = found;
        } else if (activeSession) {
          var still = sessions.filter(function (s) {
            return s.id === activeSession.id;
          })[0];
          activeSession = still || null;
        }
        renderSessions();
        return sessions;
      }).catch(function () {
        sessionList.innerHTML = '';
        sessionList.appendChild(el('p', 'asst-rail-empty', 'Could not load chats.'));
        return [];
      });
    }
    function openSession(id) {
      if (busy) return;
      api('GET', '/api/assistant/sessions/' + id).then(function (d) {
        activeSession = d.session || null;
        voiceSession = null;
        renderMessages(d.messages || []);
        renderSessions();
        box.focus();
      }).catch(function (e) { toast(e.message, false); });
    }
    function startNewChat() {
      if (busy) return;
      activeSession = null;
      voiceSession = null;
      thread.innerHTML = '';
      welcome();
      renderSessions();
      box.focus();
    }
    newChat.onclick = startNewChat;

    function maybeShowImport() {
      var old = readOldThread();
      if (!old.length) return;
      var dismissKey = oldThreadKey() + ':import-dismissed';
      try {
        if (localStorage.getItem(dismissKey)) return;
      } catch (e) { return; }
      importHost.innerHTML = '';
      var card = el('div', 'asst-import');
      card.appendChild(el('p', null,
        'A previous Assistant thread is saved in this browser. Import it into this client?'));
      var row = el('div', 'row-actions');
      var yes = el('button', 'btn btn-primary btn-sm', 'Import');
      var no = el('button', 'btn btn-ghost btn-sm', 'Not now');
      yes.onclick = function () {
        yes.disabled = true;
        api('POST', '/api/assistant/sessions/import', { messages: old })
          .then(function (d) {
            try { localStorage.removeItem(oldThreadKey()); } catch (e) { /* private mode */ }
            importHost.innerHTML = '';
            toast('Previous thread imported', true);
            return loadSessions(d.session && d.session.id).then(function () {
              if (d.session) openSession(d.session.id);
            });
          }).catch(function (e) {
            yes.disabled = false;
            card.appendChild(el('p', 'form-error', e.message));
          });
      };
      no.onclick = function () {
        try { localStorage.setItem(dismissKey, '1'); } catch (e) { /* private mode */ }
        importHost.innerHTML = '';
      };
      row.appendChild(yes);
      row.appendChild(no);
      card.appendChild(row);
      importHost.appendChild(card);
    }

    function sessionForVoice() {
      if (activeSession) return Promise.resolve(activeSession.id);
      if (voiceCreate) return voiceCreate;
      voiceCreate = api('POST', '/api/assistant/sessions', {}).then(function (d) {
        var created = (d && d.session) || {};
        if (!created.id) throw new Error('The chat could not be created.');
        voiceSession = created.id;
        activeSession = created;
        renderSessions();
        return created.id;
      }).finally(function () { voiceCreate = null; });
      return voiceCreate;
    }

    function persistVoiceTurn(role, text) {
      var content = String(text || '').trim();
      if (!content) return;
      sessionForVoice().then(function (id) {
        return api('POST', '/api/assistant/sessions/' + id + '/messages', {
          role: role,
          content: content
        }).then(function () {
          return loadSessions(id);
        });
      }).catch(function () { /* the turn is still visible on screen */ });
    }


    // One shape for every message: a body, and - on the assistant's side - the two actions
    // people actually want, kept out of the way until the pointer is over the message.
    function bubble(who, text) {
      var wrap = el('div', 'asst-msg ' + who);
      wrap.appendChild(el('div', 'asst-body', text));
      if (who === 'them') {
        var actions = el('div', 'asst-actions');
        var copy = el('button', null, 'Copy');
        copy.onclick = function () {
          navigator.clipboard.writeText(String(text)).then(function () {
            copy.textContent = 'Copied';
            setTimeout(function () { copy.textContent = 'Copy'; }, 1200);
          }).catch(function () { toast('Copy failed', false); });
        };
        actions.appendChild(copy);
        wrap.appendChild(actions);
      }
      thread.appendChild(wrap);
      thread.scrollTop = thread.scrollHeight;
      return wrap;
    }
    // A first screen that teaches the product: four prompts, each one a different tool.
    function welcome() {
      var card = el('div', 'asst-welcome');
      card.appendChild(el('h3', null, 'What should we build?'));
      card.appendChild(el('p', 'fine-print', 'The team has its own tools - it can write a ' +
        'template, build a form, draft an automation, add a site. It asks when the brief is ' +
        'thin, and nothing outward is sent without your approval.'));
      var chips = el('div', 'asst-suggest');
      ['Email sequence for my newsletter',
       'A quiz that scores answers and tags people',
       'Write and schedule a blog post',
       'Point a new domain at this server'].forEach(function (text) {
        var chip = el('button', 'asst-chip', text);
        chip.onclick = function () {
          if (card.parentNode) card.parentNode.removeChild(card);
          box.value = text;
          send.click();
        };
        chips.appendChild(chip);
      });
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

    loadSessions().then(function (rows) {
      if (rows.length) openSession(rows[0].id);
      else welcome();
      maybeShowImport();
    });

    box.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send.click();
      }
    });
    box.addEventListener('input', function () {
      box.style.height = 'auto';
      box.style.height = Math.min(160, box.scrollHeight || 48) + 'px';
    });
    main.appendChild(el('p', 'asst-hint', 'Enter sends, Shift+Enter adds a line. ' +
      'Nothing is published, sent or charged without your approval.'));

    function questionCard(questions) {
      var card = el('div', 'panel-card');
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
        if (!parts.length) {
          toast('Answer at least one question', false);
          return;
        }
        card.parentNode.removeChild(card);
        box.value = parts.join(' ');
        send.click();
      };
      card.appendChild(reply);
      thread.appendChild(card);
      thread.scrollTop = thread.scrollHeight;
    }

    // The thinking state is driven by the job's own events, so the animation describes real
    // work instead of spinning for a fixed two seconds and pretending.
    function thinking() {
      var box = el('div', 'asst-msg them think');
      var dots = el('span', 'think-dots');
      dots.appendChild(el('span'));
      dots.appendChild(el('span'));
      dots.appendChild(el('span'));
      box.appendChild(dots);
      var phase = el('span', 'think-phase', 'Thinking');
      box.appendChild(phase);
      var steps = el('div', 'think-steps');
      box.appendChild(steps);
      thread.appendChild(box);
      thread.scrollTop = thread.scrollHeight;
      function scroll() { thread.scrollTop = thread.scrollHeight; }
      return {
        phase: function (text) { phase.textContent = text; scroll(); },
        step: function (text, state) {
          steps.appendChild(el('div', state === 'done' ? 'done' : 'running',
                              (state === 'done' ? '✓ ' : '● ') + String(text).slice(0, 60)));
          scroll();
        },
        done: function () { if (box.parentNode) box.parentNode.removeChild(box); },
        node: box
      };
    }

    send.onclick = function () {
      var text = (box.value || '').trim();
      if (!text) {
        toast('Say what you want first', false);
        return;
      }
      if (busy) return;
      box.value = '';
      var hello = thread.querySelector('.asst-welcome');
      if (hello && hello.parentNode) hello.parentNode.removeChild(hello);
      bubble('me', text);
      setBusy(true);
      var wait = thinking();
      var seen = {};
      var payload = { message: text, automation_id: 0 };
      if (activeSession) payload.session_id = activeSession.id;
      else payload.create_session = true;
      api('POST', '/api/manager/chat', payload).then(function (d) {
        if (d.session_id) {
          if (!activeSession || activeSession.id !== d.session_id) {
            activeSession = { id: d.session_id, title: text };
            renderSessions();
          }
          loadSessions(d.session_id);
        }
        wait.phase('Reading your setup…');
        // Poll the transcript while it works: each tool the manager runs becomes a ticked
        // step, so the wait shows what is happening rather than three dots.
        var poll = setInterval(function () {
          api('GET', '/api/jobs/' + d.job_id + '/events?after=0').then(function (r) {
            (r.events || []).forEach(function (ev) {
              if (ev.source !== 'Manager' || ev.type !== 'tool') return;
              if (seen[ev.content + ':' + ev.id]) return;
              seen[ev.content + ':' + ev.id] = true;
              wait.step(String(ev.content).replace(/_/g, ' '), 'done');
              wait.phase('Working…');
            });
          }).catch(function () { /* a poll that fails is not an error the user needs */ });
        }, 1200);
        watchJob(d.job_id, el('span', 'activity running'), function () {
          clearInterval(poll);
          api('GET', '/api/jobs/' + d.job_id + '/events?after=0').then(function (r) {
            wait.done();
            var said = (r.events || []).filter(function (ev) {
              return ev.source === 'Manager' && ev.type === 'message';
            }).pop();
            var result = (r.job && r.job.result) || '';
            var answer = said ? said.content
              : (result || ('The assistant could not answer: ' +
                (jobFailure(r.events) || ('open job #' + d.job_id + ' to see why.'))));
            bubble('them', answer);
            if (d.session_id) loadSessions(d.session_id);
            var asked = (r.events || []).filter(function (ev) {
              return ev.source === 'Manager' && ev.type === 'question';
            }).pop();
            if (asked) {
              var questions = [];
              try { questions = JSON.parse(asked.content || '[]'); } catch (e) { questions = []; }
              if (questions.length) questionCard(questions);
            }
            setBusy(false);
          }).catch(function () { wait.done(); setBusy(false); });
        });
      }).catch(function (e) {
        wait.done();
        bubble('them', e.message);
        setBusy(false);
      });
    };
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
    head.appendChild(add);
    container.appendChild(head);

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
          body.innerHTML = '<div class="empty-state"><strong>No contacts yet</strong>' +
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
  function viewDomains(openId) {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Domains</h2><p class="muted">Point a domain at this server, ' +
      'watch each record go green, then go live. A certificate is only reported once the ' +
      'site actually answers over HTTPS.</p></div>';
    container.appendChild(head);

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
    addCard.innerHTML = '<h3 class="card-title">Point a new domain here</h3>' +
      '<p class="fine-print">Type a domain you own. You get the exact records to add at ' +
      'your DNS provider - copy them across, then press Check again until every row is ' +
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
        addPreview.appendChild(el('p', 'fine-print', 'Server IP ' +
          esc(d.server_ip || 'unknown') + ' (' + esc(d.server_ip_source || 'not set') + ')'));

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
      api('GET', '/api/sites').then(function (d) {
        body.innerHTML = '';
        var sites = d.sites || [];
        if (!sites.length) {
          body.innerHTML = '<div class="empty-state"><strong>No domains yet</strong>' +
            '<p>Add one under Websites first — a domain has to exist before it can be ' +
            'pointed here.</p></div>';
          return;
        }
        sites.forEach(function (site) {
          body.appendChild(row(site));
          if (String(openId) === String(site.id)) body.appendChild(panel(site));
        });
      }).catch(function (e) {
        body.innerHTML = '<p class="form-error">' + esc(e.message) + '</p>';
      });
    }

    function row(site) {
      var card = el('div', 'panel-card dom-row');
      var cls = site.status === 'live' ? 'badge-done'
        : (site.status === 'dns_ok' ? 'badge-running'
        : (site.status === 'error' ? 'badge-failed' : 'badge-queued'));
      card.innerHTML = '<div><div class="dom-name">' + esc(site.domain) + '</div>' +
        '<div class="dom-meta"><span class="badge ' + cls + '">' +
        esc(String(site.status || 'pending').replace('_', ' ')) + '</span>' +
        (site.dns_last_ip ? ' · resolves to ' + esc(site.dns_last_ip) : '') +
        (site.ssl_status ? ' · ssl ' + esc(site.ssl_status) : '') + '</div></div>';
      var open = el('button', 'btn btn-ghost btn-sm',
        String(openId) === String(site.id) ? 'Hide' : 'Records');
      open.onclick = function () {
        location.hash = String(openId) === String(site.id)
          ? '#/app/domains' : '#/app/domains/' + site.id;
      };
      card.appendChild(open);
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
          box.appendChild(el('p', 'fine-print', 'Server IP ' + esc(d.server_ip || 'unknown') +
            ' (' + esc(d.server_ip_source || 'not set') + ')'));

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
              toast(probe.ok ? 'Live - certificate verified'
                             : (probe.hint || ('DNS: ' + (report.reason || 'not pointed here'))),
                    !!probe.ok);
              refresh();
            }).catch(function (e) { toast(e.message, false); })
              .finally(function () { check.disabled = false; });
          };
          actions.appendChild(check);

          var live = el('button', 'btn btn-primary', 'Go live');
          live.disabled = !report.ok;
          if (!report.ok) live.title = report.reason || 'DNS is not pointed here yet';
          live.onclick = function () {
            live.disabled = true;
            api('POST', '/api/sites/' + site.id + '/provision').then(function (res) {
              toast('Provisioning queued - job #' + ((res.job || {}).id || '?'), true);
            }).catch(function (e) {
              live.disabled = false;
              toast(e.message, false);
            });
          };
          actions.appendChild(live);
          box.appendChild(actions);

          if (report.reason) box.appendChild(el('p', 'form-error', report.reason));
          var probe = d.probe || {};
          var cert;
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
          box.appendChild(el('p', 'fine-print', cert));
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

  function viewAutomations() {
    var container = el('div', 'container');
    var head = el('div', 'page-head');
    head.innerHTML = '<div><h2>Automations</h2><p class="muted">A named graph of steps ' +
      'that runs as a job. Nothing outward-facing runs without an approval gate.</p></div>';
    var add = el('button', 'btn btn-primary', 'New automation');
    head.appendChild(add);
    container.appendChild(head);

    // Ask in words, on this page: the draft it produces lands in the strip below, so the
    // question and the answer share a screen instead of living in the floating widget.
    var composer = el('div', 'panel-card mt');
    composer.innerHTML = '<h3 class="card-title">Describe an automation</h3>' +
      '<p class="fine-print">e.g. “when a contact signs up, tag them and send the welcome ' +
      'email, after I approve it”. The team drafts it — you preview and apply.</p>';
    var askBox = el('textarea', 'prompt-box');
    askBox.placeholder = 'Build me an automation that…';
    composer.appendChild(askBox);
    var askBtn = el('button', 'btn btn-primary', 'Draft it');
    askBtn.onclick = function () {
      var text = (askBox.value || '').trim();
      if (!text) {
        toast('Say what it should do first', false);
        return;
      }
      askBtn.disabled = true;
      api('POST', '/api/manager/chat', { message: text, history: [], automation_id: 0 })
        .then(function () {
          toast('The team is drafting — the draft will appear below', true);
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
        card.appendChild(el('h3', 'card-title', 'Proposed tools (' + rows.length + ')'));
        card.appendChild(el('p', 'fine-print', 'The agent asked for these. Approving records ' +
          'them - nothing executes a tool in this build yet.'));
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
          var no = el('button', 'btn btn-ghost btn-sm', 'Reject');
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
        card.appendChild(el('h3', 'card-title', 'Drafts from the team (' + fresh.length + ')'));
        fresh.forEach(function (draft) {
          var row = el('div', 'auto-proposal');
          var label = el('div');
          label.innerHTML = '<strong>' + esc(draft.name || 'Untitled') + '</strong>' +
            '<div class="fine-print">' + (draft.steps || []).length + ' steps · ' +
            esc((draft.steps || []).map(function (s) { return s.title || s.id; }).join(' → ')) +
            '</div>';
          row.appendChild(label);

          var apply = el('button', 'btn btn-primary btn-sm', 'Apply');
          apply.onclick = function () {
            apply.disabled = true;
            api('POST', '/api/automations/proposals/' + draft.id + '/apply').then(function (res) {
              toast('Saved as “' + ((res.automation || {}).name || draft.name) + '”', true);
              loadDrafts();
              refresh();
            }).catch(function (e) {
              apply.disabled = false;
              toast(e.message, false);
            });
          };

          // The chart, before you commit to it: the same renderer the editor uses, so a
          // draft looks exactly like the automation it will become.
          var preview = el('button', 'btn btn-ghost btn-sm', 'Preview');
          preview.onclick = function () {
            var modal = el('div', 'auto-modal');
            var panel = el('div', 'auto-panel');
            var top = el('div', 'auto-panel-head');
            top.appendChild(el('h3', 'card-title', draft.name || 'Draft'));
            var close = el('button', 'btn', 'Close');
            close.onclick = function () { modal.parentNode.removeChild(modal); };
            top.appendChild(close);
            panel.appendChild(top);
            panel.appendChild(el('p', 'fine-print', 'A draft — nothing is saved until you ' +
              'press Apply.'));
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
            panel.appendChild(el('p', 'fine-print', (draft.steps || []).map(function (step) {
              return (step.title || step.id) + ' (' + step.type + ')';
            }).join(' → ')));
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
      var trigger = a.trigger_type === 'schedule'
        ? 'On a schedule'
        : (a.trigger_type === 'event' ? 'When something happens' : 'Run by hand');
      box.innerHTML =
        '<div class="auto-head"><div><div class="auto-name">' + esc(a.name) + '</div>' +
        '<div class="auto-meta">' + esc(trigger) + ' · ' + steps +
        (steps === 1 ? ' step' : ' steps') + (a.last_run_at ? ' · last run ' +
        esc(String(a.last_run_at).slice(0, 16)) : ' · never run') +
        (a.last_run ? ' · <span class="run-status ' + esc(a.last_run.status) + '">' +
          esc(String(a.last_run.status).replace('_', ' ')) + '</span>' : '') +
        '</div></div></div>';
      var actions = el('div', 'auto-actions');
      var toggle = el('button', 'btn', a.enabled ? 'Enabled' : 'Disabled');
      toggle.onclick = function () {
        api('PUT', '/api/automations/' + a.id, { enabled: !a.enabled })
          .then(refresh).catch(function (e) { toast(e.message, false); });
      };
      var edit = el('button', 'btn', 'Open');
      edit.onclick = function () { openEditor(a); };
      var del = el('button', 'btn', 'Delete');
      del.onclick = function () {
        if (!confirm('Delete "' + a.name + '"?')) return;
        api('DELETE', '/api/automations/' + a.id).then(refresh)
          .catch(function (e) { toast(e.message, false); });
      };
      var run = el('button', 'btn', 'Run now');
      run.onclick = function () {
        run.disabled = true;
        api('POST', '/api/automations/' + a.id + '/run').then(function (d) {
          toast('Queued run #' + d.run_id);
          watchJob(d.job_id, run, function () { run.disabled = false; });
        }).catch(function (e) {
          run.disabled = false;
          toast(e.message, false);
        });
      };
      if (a.last_run && a.last_run.status === 'awaiting_approval') {
        var go = el('button', 'btn btn-primary', 'Approve and continue');
        go.onclick = function () {
          go.disabled = true;
          api('POST', '/api/automations/' + a.id + '/resume').then(function (d) {
            toast('Resumed run #' + d.run_id);
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
          blank.innerHTML = '<div class="empty-icon">⌘</div>' +
            '<p><strong>No automations yet.</strong></p>' +
            '<p class="muted">Start from a template: draft with the team, approve, ' +
            'then report.</p>';
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
      nameField.placeholder = 'Name, e.g. Weekly launch email';
      nameField.value = a ? a.name : '';
      panel.appendChild(nameField);

      // Trigger. Without this only the agent could set "when a contact is created", which
      // made the most useful trigger in the product unreachable from the screen.
      var trigger = el('select', 'auto-trigger');
      trigger.setAttribute('aria-label', 'When this runs');
      [
        { value: 'manual', label: 'Run by hand' },
        { value: 'schedule', label: 'On a schedule' },
        { value: 'event', label: 'When something happens' }
      ].forEach(function (option) {
        var node = el('option', null, option.label);
        node.value = option.value;
        trigger.appendChild(node);
      });
      trigger.value = (a && a.trigger_type) || 'manual';
      panel.appendChild(trigger);

      var eventField = el('input', 'auto-event');
      eventField.placeholder = 'contact.created or form.submitted';
      eventField.setAttribute('aria-label', 'Event name');
      eventField.value = ((a && a.trigger_config) || {}).event || '';
      eventField.style.display = trigger.value === 'event' ? '' : 'none';
      trigger.onchange = function () {
        eventField.style.display = trigger.value === 'event' ? '' : 'none';
      };
      panel.appendChild(eventField);

      var steps = a ? a.steps : STARTER;
      var list = el('div', 'auto-steps');
      list.innerHTML = '<div class="auto-step-head">Steps</div>';
      steps.forEach(function (step, index) {
        var row = el('div', 'auto-step');
        row.innerHTML = '<span class="auto-step-num">' + (index + 1) + '</span>' +
          '<span class="auto-step-title">' + esc(step.title || step.id) + '</span>' +
          '<span class="auto-step-type">' + esc(step.type) + '</span>';
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
            { value: 'drafts', label: 'drafts' },
            { value: 'sites', label: 'websites' },
            { value: 'failed_jobs', label: 'failed jobs' }
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
          kindField.placeholder = 'kind, e.g. email';
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
          toolName.placeholder = 'tool, e.g. send_email';
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
          label.innerHTML = '<strong>A draft is waiting</strong>' +
            '<div class="fine-print">' + (draft.steps || []).length + ' steps · proposed ' +
            esc(String(draft.created_at || '').slice(11, 16)) + '</div>';
          bar.appendChild(label);

          var view = el('button', 'btn btn-ghost btn-sm', 'Preview');
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
              view.textContent = 'Preview';
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
            view.textContent = 'Hide preview';
            draw();
          };

          var apply = el('button', 'btn btn-primary btn-sm', 'Apply');
          apply.onclick = function () {
            apply.disabled = true;
            api('POST', '/api/automations/proposals/' + draft.id + '/apply').then(function () {
              toast('Applied - the automation is updated', true);
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

      var save = el('button', 'btn btn-primary', 'Save');
      save.onclick = function () {
        // While previewing, the canvas shows the *draft*. Saving here would quietly write
        // the agent's graph over the saved one and skip the whole review step.
        if (modal.dataset.previewing) {
          toast('You are previewing a draft - press Apply to accept it, or hide the preview ' +
                'to save your own edits', false);
          return;
        }
        var payload = { name: nameField.value, steps: steps, trigger_type: trigger.value,
                        trigger_config: trigger.value === 'event'
                          ? { event: (eventField.value || '').trim() } : {},
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
        var runBtn = el('button', 'btn', 'Run and watch');
        runBtn.onclick = function () {
          panel.querySelectorAll('.auto-node.done, .auto-node.running').forEach(function () {
            this.classList.remove('done', 'running');
          });
          nodeStates = {};
          draw();
          runBtn.disabled = true;
          api('POST', '/api/automations/' + a.id + '/run').then(function (d) {
            toast('Queued run #' + d.run_id);
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
        esc(String(step.type || '').toUpperCase()) + '</text></g>');
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
    var container = el('div');
    var head = el('div');
    head.innerHTML = '<h2>Forms</h2><p class="muted">Forms, surveys and quizzes. Each one ' +
      'lives at its own link, and every answer lands in the list beside it.</p>';
    container.appendChild(head);

    var bar = el('div', 'chip-input');
    var add = el('button', 'btn btn-primary', 'New form');
    var refresh = el('button', 'btn btn-ghost', 'Refresh');
    bar.appendChild(add);
    bar.appendChild(refresh);
    container.appendChild(bar);

    var body = el('div');
    container.appendChild(body);

    function load() {
      body.innerHTML = '';
      api('GET', '/api/forms').then(function (d) {
        var rows = d.forms || [];
        if (!rows.length) {
          var empty = el('div', 'panel-card empty-state');
          empty.innerHTML = '<p><strong>No forms yet.</strong></p>' +
            '<p class="muted">Create one, or ask the assistant for a quiz and it will draft it.</p>';
          body.appendChild(empty);
          return;
        }
        rows.forEach(function (item) { body.appendChild(card(item)); });
      }).catch(function (e) { toast(e.message, false); });
    }

    function card(item) {
      var box = el('div', 'panel-card auto-card');
      var link = location.origin + '/f/' + item.slug;
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
          .then(function () {
            toast(item.status === 'published' ? 'Unpublished'
                                              : 'Live at /f/' + item.slug, true);
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
        (rows.length === 1 ? '' : 's') + ' · /f/' + esc(item.slug || '') + ' · ' +
        esc(item.status || 'draft') + '</p>';
      if (!rows.length) {
        var empty = el('div', 'panel-card empty-state');
        empty.innerHTML = '<p><strong>No answers yet.</strong></p>' +
          '<p class="muted">Publish the form and share its link.</p>';
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
          ? (patch.status === 'published' ? 'Live at /f/' + draft.slug : 'Unpublished')
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
        (highestPage() ? 's' : '') + ' · ' + esc(live) + ' · /f/' + esc(draft.slug || '') +
        '</p>';
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
      contact.appendChild(el('span', null, 'create a contact from each answer'));
      meta.appendChild(contact);
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
          toast(chosen ? 'Now acting as ' + pick.options[pick.selectedIndex].text
                       : 'Showing all clients', true);
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
    var history = [];   // last few turns, so a follow-up question has something to follow
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
      history.push({ role: 'user', content: text });
      var thinking = el('span', 'activity running');
      log.appendChild(thinking);
      send.disabled = true;
      api('POST', '/api/manager/chat', {
        message: text,
        history: history.slice(-8),
        // Tells the manager which graph the user is looking at, so a change request
        // revises it instead of drafting a second automation.
        automation_id: state.openAutomation || 0
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
              history.push({ role: 'assistant', content: answer });
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
                    history.push({ role: 'user', content: parts.join(' ') });
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

  // ---------- Router ----------
  function render() {
    var app = document.getElementById('app');
    runCleanups();
    app.innerHTML = '';
    var parts = (location.hash || '').replace(/^#/, '').split('/').filter(Boolean);

    // An invitation link has to work before anyone is signed in, so this is handled
    // ahead of the auth gate and rendered without the app shell.
    if (parts[0] === 'accept' && parts[1]) {
      app.appendChild(viewAccept(decodeURIComponent(parts[1])));
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
      if (tab === 'clients' && rest[0]) view = viewClient(rest[0]);
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
    else if (tab === 'assistant') view = viewAssistant();
    else if (tab === 'analytics') view = viewAnalytics();
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
  // assistant nobody uses. Same endpoint as the Assistant page: it is a second door, not a
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
      var toPage = el('button', 'btn btn-ghost btn-sm', 'Full screen');
      toPage.onclick = function () { closeAll(); location.hash = '#/app/assistant'; };
      var close = el('button', 'btn btn-ghost btn-sm', '×');
      close.onclick = closeAll;
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

      say('them', 'Ask me to build, change or explain anything on this page.');
      var history = [];
      // The hash is the only reliable statement of where the user is: #/app/forms/12/responses
      // is a different question from "the forms screen", and the manager can only ask about a
      // record it has the id for.
      var parts = (location.hash || '').replace(/^#\/?/, '').split('/');
      var page = parts[1] || 'home';
      var record = parts[2] || '';
      var where = record ? (page + ' screen, record #' + record) : (page + ' screen');

      function ask(text) {
        say('me', text);
        var wait = say('them', 'Working…');
        history.push({ role: 'user', content: text });
        api('POST', '/api/manager/chat', {
          message: '[context: the user is on the ' + where + ']\n' + text,
          history: history.slice(-8), automation_id: 0
        }).then(function (d) {
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
              history.push({ role: 'assistant', content: answer });
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
          // turns land back in this log, so closing the overlay leaves a readable thread.
          if (!window.AscendVoice) { toast('The voice client did not load.', false); return; }
          if (window.AscendVoice.running()) { window.AscendVoice.restore(); return; }
          window.AscendVoice.open({
            page: page,
            source: 'sheet',
            onState: function (phase, label) { mic.title = label; },
            onTurn: function (turn) {
              say(turn.role === 'user' ? 'me' : 'them', turn.text);
            },
            onJob: function (jobId) {
              say('them', 'The team is working - job #' + jobId);
            }
          });
        };
      }).catch(function () { /* text still works */ });
    }

    orb.onclick = open;
  }

  // ---------- A first-timer's five stops ----------
  // Shown once per browser, skip always visible, and every stop names the thing rather than
  // describing the interface. Remembered in localStorage, which is per device: a
  // users.tour_done column is the upgrade, and this is the honest version until then.
  var TOUR = [
    { hash: '#/app/assistant', title: 'Ask, and it builds',
      text: 'Type or talk in plain English. It asks when the brief is thin, and does the work ' +
            'when it is not - templates, automations, a quiz, a whole site.' },
    { hash: '#/app/sites', title: 'Websites',
      text: 'Each client gets a site here. Publish a page, and the domain points at this server.' },
    { hash: '#/app/automations', title: 'Automations',
      text: 'A sequence of steps: tag, wait a day, ask for approval, send. Drafts arrive from ' +
            'the AI and nothing goes live until you press Apply.' },
    { hash: '#/app/forms', title: 'Forms, surveys and quizzes',
      text: 'Build one, publish it, and it lives at its own link. Answers land in the list, ' +
            'and a submission can start an automation.' },
    { hash: '#/app/domains', title: 'Domains',
      text: 'Type a domain and copy two records to your DNS provider. Press Check again until ' +
            'every row is green, then go live.' }
  ];

  function startTour(force) {
    var key = 'tour:' + ((state.user && state.user.id) || 0);
    try {
      if (!force && localStorage.getItem(key)) return;
    } catch (e) {
      return;                     // private mode: no tour rather than a tour every load
    }
    var index = 0;
    var veil = el('div', 'tour-veil');
    var card = el('div', 'tour-card');
    veil.appendChild(card);
    document.body.appendChild(veil);

    function close() {
      try { localStorage.setItem(key, '1'); } catch (e) { /* private mode */ }
      if (window.AscendVoice && window.AscendVoice.running()) window.AscendVoice.stop();
      if (veil.parentNode) veil.parentNode.removeChild(veil);
    }

    function show() {
      var step = TOUR[index];
      if (step.hash) location.hash = step.hash;
      card.innerHTML = '';
      card.appendChild(el('p', 'fine-print', 'Step ' + (index + 1) + ' of ' + TOUR.length));
      card.appendChild(el('h3', 'card-title', step.title));
      card.appendChild(el('p', 'muted', step.text));
      var row = el('div', 'chip-input');
      var talk = el('button', 'btn btn-ghost', 'Talk about this');
      talk.onclick = function () {
        if (!window.AscendVoice) return;
        window.AscendVoice.open({
          page: 'tour, step: ' + step.title,
          source: 'tour',
          onState: function (phase, label) { talk.textContent = label; },
          onTurn: function () {},
          onClose: function () { talk.textContent = 'Talk about this'; }
        });
      };
      var skip = el('button', 'btn btn-ghost', index === TOUR.length - 1 ? 'Done' : 'Skip');
      skip.onclick = close;
      var next = el('button', 'btn btn-primary',
                    index === TOUR.length - 1 ? 'Finish' : 'Next');
      next.onclick = function () {
        if (index === TOUR.length - 1) { close(); return; }
        index += 1;
        show();
      };
      [talk, skip, next].forEach(function (button) { row.appendChild(button); });
      card.appendChild(row);
    }
    show();
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
    managerWidget();
    assistOrb();
    startTour(false);
    render();
  }).catch(function () {
    state.user = null;
    render();
  });
})();
