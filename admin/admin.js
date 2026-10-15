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

  var BRAND = 'CMGrowthAI';

  // Left-sidebar navigation. `ready: false` tabs render a "coming soon" panel.
  var NAV = [
    { id: 'dashboard', label: 'Dashboard', icon: '📊', hash: '#/' },
    { id: 'profile', label: 'Business Profile', icon: '🏢', hash: '#/profile' },
    { id: 'bots', label: 'Chatbots', icon: '🤖', hash: '#/bots' },
    { id: 'conversations', label: 'Conversations', icon: '💬', hash: '#/conversations' },
    { id: 'jobs', label: 'Jobs', icon: '⚙️', hash: '#/jobs' },
    { id: 'analytics', label: 'Analytics', icon: '📈', hash: '#/analytics', ready: false },
    { id: 'agents', label: 'Agents', icon: '🧠', hash: '#/agents' },
    { id: 'sites', label: 'Sites', icon: '🌐', hash: '#/sites' },
    { id: 'servers', label: 'Servers', icon: '🖥️', hash: '#/servers', adminOnly: true },
    { id: 'users', label: 'Users', icon: '👤', hash: '#/users', adminOnly: true },
    { id: 'settings', label: 'Settings', icon: '🛠️', hash: '#/settings', adminOnly: true }
  ];

  function currentNavId(parts) {
    var first = parts[0] || '';
    if (first === 'bot') return 'bots';
    return first || 'dashboard';
  }

  function api(method, url, body) {
    var opts = { method: method, credentials: 'same-origin', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { var e = new Error(data.error || ('HTTP ' + res.status)); e.status = res.status; throw e; }
        return data;
      });
    });
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

  function toast(msg, ok) {
    var t = el('div', 'toast ' + (ok ? 'toast-success' : 'toast-error'), msg);
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 3000);
  }

  function initials(name) {
    var parts = (name || '?').trim().split(/\s+/);
    return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
  }

  function avatarHTML(bot) {
    if (bot.logo_url) return '<img src="' + esc(bot.logo_url) + '" alt="">';
    return esc(initials(bot.name));
  }

  // ---------- Shell: sidebar + topbar ----------
  function sidebar(parts) {
    var aside = el('aside', 'sidebar');
    var brand = el('div', 'side-brand');
    brand.innerHTML = '<span class="dot"></span>' + esc(BRAND);
    aside.appendChild(brand);

    var nav = el('nav', 'side-nav');
    var active = currentNavId(parts);
    NAV.forEach(function (item) {
      if (item.adminOnly && state.user.role !== 'admin') return;
      var link = el('a', 'side-item' + (item.id === active ? ' active' : ''));
      link.href = item.hash;
      link.innerHTML = '<span class="side-icon">' + item.icon + '</span>' +
        '<span class="side-label">' + esc(item.label) + '</span>' +
        (item.ready === false ? '<span class="side-soon">Soon</span>' : '');
      link.onclick = function () { document.body.classList.remove('nav-open'); };
      nav.appendChild(link);
    });
    aside.appendChild(nav);

    var foot = el('div', 'side-foot');
    var who = el('div', 'side-user');
    who.innerHTML = '<span class="avatar">' + esc(initials(state.user.name)) + '</span>' +
      '<div><div class="side-user-name">' + esc(state.user.name) + '</div>' +
      '<div class="side-user-role">' + esc(state.user.role) + '</div></div>';
    foot.appendChild(who);
    var logout = el('button', 'btn btn-ghost btn-sm', 'Log out');
    logout.onclick = function () {
      api('POST', '/api/auth/logout').then(function () { location.reload(); });
    };
    foot.appendChild(logout);
    aside.appendChild(foot);
    return aside;
  }

  function topbar() {
    var bar = el('div', 'topbar');
    var inner = el('div', 'topbar-inner');
    var burger = el('button', 'burger', '☰');
    burger.setAttribute('aria-label', 'Toggle navigation');
    burger.onclick = function () { document.body.classList.toggle('nav-open'); };
    inner.appendChild(burger);
    var brand = el('div', 'brand');
    brand.innerHTML = '<span class="dot"></span>' + esc(BRAND);
    inner.appendChild(brand);
    inner.appendChild(el('div', 'spacer'));
    return bar;
  }

  // ---------- Login ----------
  function viewLogin() {
    var wrap = el('div', 'auth-wrap');
    var card = el('div', 'auth-card');
    card.innerHTML = '<h1>' + esc(BRAND) + '</h1><p class="sub">Sign in to your growth platform</p>';
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
    var snippet = '<script src="' + location.origin + '/widget.js" data-bot="' + bot.id + '"><\/script>';
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
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">🤖</div>No chatbots yet. Click <b>+ New Chatbot</b> to create your first one.</div>';
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
        tr.innerHTML = '<td>' + esc(u.name) + '</td><td>' + esc(u.email) + '</td><td><span class="badge ' + (u.role === 'admin' ? 'badge-admin' : 'badge-member') + '">' + esc(u.role) + '</span></td>';
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
    head.innerHTML = '<span class="settings-icon">' + (group.icon || '&#9881;') + '</span>' +
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
    panel.innerHTML = '<div class="empty-state"><div class="big">' + (meta ? meta.icon : '🚧') +
      '</div>This section is planned.<br><span class="muted">See SYSTEM_PLAN.md for the roadmap.</span></div>';
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
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">💬</div>No chatbots yet.</div>';
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
          grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">🧠</div>No agents yet. Click <b>+ New agent</b>.</div>';
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
          grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">🌐</div>No sites yet. Click <b>+ New site</b>.</div>';
          return;
        }
        sites.forEach(function (site) { grid.appendChild(siteCard(site, refresh)); });
      }).catch(function (e) { toast(e.message, false); });
    }

    refresh();
    return container;
  }

  function siteCard(site, refresh) {
    var card = el('div', 'bot-card');
    var top = el('div', 'bot-card-top');
    var av = el('div', 'bot-avatar');
    av.style.background = site.status === 'live' ? '#16a34a' : (site.status === 'error' ? '#dc2626' : '#4f46e5');
    av.innerHTML = '🌐';
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
    var dns = el('button', 'btn btn-ghost btn-sm', 'DNS setup');
    dns.onclick = function () { showDns(site); };
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
          tableWrap.innerHTML = '<div class="empty-state"><div class="big">🖥️</div>No servers yet.</div>';
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
    keyPath.placeholder = '/opt/cmgrowthai/.ssh/id_deploy';
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
          tableWrap.innerHTML = '<div class="empty-state"><div class="big">⚙️</div>No jobs yet.</div>';
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

  // ---------- Router ----------
  function render() {
    var app = document.getElementById('app');
    runCleanups();
    app.innerHTML = '';
    var parts = (location.hash || '#/').replace(/^#/, '').split('/').filter(Boolean);

    if (!state.user) { app.appendChild(viewLogin()); return; }

    var shell = el('div', 'app-shell');
    shell.appendChild(sidebar(parts));
    var main = el('div', 'main');
    main.appendChild(topbar());
    var content = el('div', 'content');
    main.appendChild(content);
    shell.appendChild(main);
    app.appendChild(shell);

    var view;
    if (parts[0] === 'bot' && parts[1] === 'new') view = viewEditor(null);
    else if (parts[0] === 'bot' && parts[2] === 'stats') view = viewStats(parts[1]);
    else if (parts[0] === 'bot' && parts[2] === 'conversations') view = viewConversations(parts[1]);
    else if (parts[0] === 'bot' && parts[1]) view = viewEditor(parts[1]);
    else if (parts[0] === 'profile') view = viewProfile();
    else if (parts[0] === 'agents') view = viewAgents();
    else if (parts[0] === 'bots') view = viewBots();
    else if (parts[0] === 'conversations') view = viewConversationsIndex();
    else if (parts[0] === 'sites') view = viewSites();
    else if (parts[0] === 'servers') view = viewServers();
    else if (parts[0] === 'users') view = viewUsers();
    else if (parts[0] === 'settings') view = viewSettings();
    else if (parts[0] === 'jobs' && parts[1]) view = viewJob(parts[1]);
    else if (parts[0] === 'jobs') view = viewJobs();
    else if (isSoonTab(parts[0])) view = viewSoon(parts[0]);
    else view = viewDashboard();

    content.appendChild(view);
  }

  window.addEventListener('hashchange', render);

  api('GET', '/api/auth/me').then(function (d) {
    state.user = d.user;
    render();
  }).catch(function () {
    state.user = null;
    render();
  });
})();
