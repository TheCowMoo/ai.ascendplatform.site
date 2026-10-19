/*!
 * Ascend Chat Widget — embeddable per-bot chat.
 * Embed: <script src="https://host/widget.js" data-bot="BOT_ID"></script>
 */
(function () {
  'use strict';

  if (window.AscendChatLoaded) return;
  window.AscendChatLoaded = true;

  var script = document.currentScript;
  var BOT_ID = script && script.getAttribute('data-bot');

  function apiBase() {
    if (script && script.src) { try { return new URL(script.src).origin; } catch (e) { /* ignore */ } }
    return window.location.origin;
  }
  var API_BASE = apiBase();

  if (!BOT_ID) { console.warn('[AscendChat] Missing data-bot attribute.'); return; }

  // ---------------------------------------------------------------------------
  // Brand colour → a usable palette.
  //
  // The accent here is the *client's* choice, so nothing may assume it is dark
  // enough for white text - someone who picks #fde68a would otherwise get a white
  // launcher with white text on it. The readable ink and the soft tints are derived
  // from the hex instead of assumed.
  // ---------------------------------------------------------------------------
  function rgbOf(hex) {
    var match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || '').trim());
    if (!match) return null;
    var h = match[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  // WCAG relative luminance, threshold at 0.42: light brands take dark ink.
  function inkOn(hex) {
    var rgb = rgbOf(hex);
    if (!rgb) return '#ffffff';
    function lin(c) { c = c / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
    var luminance = 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
    return luminance > 0.42 ? '#0b1120' : '#ffffff';
  }

  function rgbaOn(hex, alpha) {
    var rgb = rgbOf(hex) || [79, 70, 229];
    return 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + alpha + ')';
  }

  var CSS = [
    '.ascend-chat { --ac: #4f46e5; --ac-ink: #fff; --ac-soft: rgba(79,70,229,.10); --ac-line: rgba(79,70,229,.24);',
    '  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; }',
    '.ascend-chat * { box-sizing: border-box; margin: 0; padding: 0; }',
    '.ascend-chat .ac-launcher { position: fixed; right: 24px; bottom: 24px; z-index: 2147483000; width: 58px; height: 58px; border-radius: 50%; border: none; cursor: pointer; background: var(--ac); color: var(--ac-ink); box-shadow: 0 1px 2px rgba(11,17,32,.10), 0 8px 24px -6px rgba(11,17,32,.28); display: flex; align-items: center; justify-content: center; transition: transform 160ms cubic-bezier(.2,.7,.3,1), box-shadow 160ms cubic-bezier(.2,.7,.3,1); }',
    '.ascend-chat .ac-launcher:hover { transform: translateY(-2px); box-shadow: 0 1px 2px rgba(11,17,32,.12), 0 14px 30px -8px rgba(11,17,32,.32); }',
    '.ascend-chat .ac-launcher svg { width: 26px; height: 26px; }',
    '.ascend-chat .ac-panel { position: fixed; right: 24px; bottom: 94px; z-index: 2147483000; width: 372px; max-width: calc(100vw - 32px); height: 560px; max-height: calc(100vh - 124px); display: flex; flex-direction: column; background: #fff; border: 1px solid #e6e8ec; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 6px rgba(11,17,32,.06), 0 24px 48px -12px rgba(11,17,32,.22); }',
    '.ascend-chat .ac-header { background: var(--ac); color: var(--ac-ink); padding: 14px 16px; display: flex; align-items: center; gap: 11px; }',
    '.ascend-chat .ac-header .ac-avatar { width: 36px; height: 36px; border-radius: 50%; background: rgba(255,255,255,.18); display: flex; align-items: center; justify-content: center; font-weight: 650; font-size: 13px; overflow: hidden; flex-shrink: 0; }',
    '.ascend-chat .ac-header .ac-avatar img { width: 100%; height: 100%; object-fit: cover; }',
    '.ascend-chat .ac-header .ac-title { font-weight: 600; font-size: 14.5px; letter-spacing: -0.01em; }',
    '.ascend-chat .ac-header .ac-sub { font-size: 11.5px; opacity: .85; margin-top: 2px; display: flex; align-items: center; gap: 5px; }',
    '.ascend-chat .ac-header .ac-sub::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }',
    '.ascend-chat .ac-close { margin-left: auto; background: transparent; border: none; color: inherit; cursor: pointer; opacity: .75; width: 28px; height: 28px; border-radius: 8px; display: flex; align-items: center; justify-content: center; transition: background 140ms, opacity 140ms; }',
    '.ascend-chat .ac-close:hover { opacity: 1; background: rgba(255,255,255,.16); }',
    '.ascend-chat .ac-close svg { width: 16px; height: 16px; }',
    '.ascend-chat .ac-messages { flex: 1; overflow-y: auto; padding: 16px; background: #f8f9fb; display: flex; flex-direction: column; }',
    '.ascend-chat .ac-msg { max-width: 84%; padding: 10px 13px; border-radius: 14px; margin-bottom: 8px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; }',
    '.ascend-chat .ac-msg.ac-bot { background: #fff; border: 1px solid #e6e8ec; border-bottom-left-radius: 5px; color: #1f2937; }',
    '.ascend-chat .ac-msg.ac-user { background: var(--ac); color: var(--ac-ink); margin-left: auto; border-bottom-right-radius: 5px; }',
    '.ascend-chat .ac-msg.ac-error { background: #fef2f2; color: #b42318; border: 1px solid #f7d4d4; }',
    '.ascend-chat .ac-quick { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 16px 12px; background: #f8f9fb; }',
    '.ascend-chat .ac-quick button { background: var(--ac-soft); color: #0b1120; border: 1px solid var(--ac-line); border-radius: 999px; padding: 6px 11px; font-size: 12.5px; font-weight: 500; cursor: pointer; transition: border-color 140ms; }',
    '.ascend-chat .ac-quick button:hover { border-color: var(--ac); }',
    '.ascend-chat .ac-typing { display: flex; gap: 4px; padding: 12px 14px; width: fit-content; background: #fff; border: 1px solid #e6e8ec; border-radius: 14px; border-bottom-left-radius: 5px; margin-bottom: 8px; }',
    '.ascend-chat .ac-typing span { width: 6px; height: 6px; background: #9aa3b2; border-radius: 50%; animation: acBounce 1.2s infinite ease-in-out; }',
    '.ascend-chat .ac-typing span:nth-child(2) { animation-delay: .15s; }',
    '.ascend-chat .ac-typing span:nth-child(3) { animation-delay: .3s; }',
    '@keyframes acBounce { 0%, 60%, 100% { transform: translateY(0); opacity: .5; } 30% { transform: translateY(-3px); opacity: 1; } }',
    '.ascend-chat .ac-input { display: flex; align-items: flex-end; gap: 8px; border-top: 1px solid #e6e8ec; padding: 10px 12px; background: #fff; }',
    '.ascend-chat .ac-input textarea { flex: 1; border: 1px solid #e6e8ec; border-radius: 10px; padding: 10px 12px; font-size: 14px; font-family: inherit; line-height: 1.45; resize: none; height: 42px; outline: none; transition: border-color 140ms, box-shadow 140ms; }',
    '.ascend-chat .ac-input textarea:focus { border-color: var(--ac); box-shadow: 0 0 0 3px var(--ac-soft); }',
    '.ascend-chat .ac-send { height: 42px; background: var(--ac); color: var(--ac-ink); border: none; border-radius: 10px; padding: 0 16px; cursor: pointer; font-weight: 600; font-size: 14px; transition: opacity 140ms; }',
    '.ascend-chat .ac-send:disabled { opacity: .45; cursor: not-allowed; }',
    '.ascend-chat .ac-panel[hidden] { display: none !important; }',
    '@media (prefers-reduced-motion: reduce) { .ascend-chat * { animation-duration: .001ms !important; transition-duration: .001ms !important; } }'
  ].join('\n');

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function initials(name) {
    var parts = (name || '?').trim().split(/\s+/);
    return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
  }

  function build(config) {
    var style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    var root = el('div', 'ascend-chat');
    var brand = config.brand_color || '#4f46e5';
    root.style.setProperty('--ac', brand);
    // Derived, not assumed: a pale brand needs dark ink on it, and the tints follow
    // whatever hue the client chose.
    root.style.setProperty('--ac-ink', inkOn(brand));
    root.style.setProperty('--ac-soft', rgbaOn(brand, .10));
    root.style.setProperty('--ac-line', rgbaOn(brand, .24));

    var launcher = el('button', 'ac-launcher');
    launcher.setAttribute('aria-label', 'Open chat');
    launcher.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>';

    var panel = el('div', 'ac-panel');
    panel.hidden = true;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', (config.assistant_name || 'Assistant') + ' chat');

    var header = el('div', 'ac-header');
    var av = el('div', 'ac-avatar');
    if (config.logo_url) { av.innerHTML = '<img src="' + config.logo_url + '" alt="">'; }
    else { av.textContent = initials(config.name); }
    var titleWrap = el('div');
    titleWrap.appendChild(el('div', 'ac-title', config.assistant_name || 'Assistant'));
    titleWrap.appendChild(el('div', 'ac-sub', 'Online'));
    var close = el('button', 'ac-close');
    close.setAttribute('aria-label', 'Close chat');
    close.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    header.appendChild(av);
    header.appendChild(titleWrap);
    header.appendChild(close);

    var messages = el('div', 'ac-messages');
    var quick = el('div', 'ac-quick');
    var inputRow = el('div', 'ac-input');
    var textarea = el('textarea');
    textarea.placeholder = 'Type your message…';
    textarea.setAttribute('aria-label', 'Message');
    textarea.rows = 1;
    var send = el('button', 'ac-send', 'Send');
    inputRow.appendChild(textarea);
    inputRow.appendChild(send);

    panel.appendChild(header);
    panel.appendChild(messages);
    panel.appendChild(quick);
    panel.appendChild(inputRow);
    root.appendChild(launcher);
    root.appendChild(panel);
    document.body.appendChild(root);

    return { launcher: launcher, panel: panel, messages: messages, quick: quick, textarea: textarea, send: send };
  }

  function appendMessage(messagesEl, role, text) {
    var cls = role === 'user' ? 'ac-msg ac-user' : (role === 'error' ? 'ac-msg ac-error' : 'ac-msg ac-bot');
    messagesEl.appendChild(el('div', cls, text));
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setTyping(messagesEl, on) {
    var existing = messagesEl.querySelector('.ac-typing');
    if (on && !existing) {
      var typing = el('div', 'ac-typing');
      typing.appendChild(el('span'));
      typing.appendChild(el('span'));
      typing.appendChild(el('span'));
      messagesEl.appendChild(typing);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else if (!on && existing) {
      existing.remove();
    }
  }

  function init(config) {
    var ui = build(config);
    var conversationId = Math.random().toString(36).slice(2) + Date.now().toString(36);
    var history = [];

    if (config.welcome_message) appendMessage(ui.messages, 'bot', config.welcome_message);

    if (config.quick_replies && config.quick_replies.length) {
      config.quick_replies.forEach(function (q) {
        var b = el('button', null, q);
        b.onclick = function () { sendMessage(ui, q); };
        ui.quick.appendChild(b);
      });
    }

    ui.launcher.addEventListener('click', function () {
      ui.panel.hidden = !ui.panel.hidden;
      if (!ui.panel.hidden) ui.textarea.focus();
    });

    ui.panel.querySelector('.ac-close').addEventListener('click', function () {
      ui.panel.hidden = true;
    });

    ui.send.addEventListener('click', function () { sendMessage(ui, ui.textarea.value); });

    ui.textarea.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(ui, ui.textarea.value);
      }
    });

    function sendMessage(ui, text) {
      var clean = (text || '').trim();
      if (!clean) return;

      appendMessage(ui.messages, 'user', clean);
      ui.textarea.value = '';
      history.push({ role: 'user', content: clean });
      if (history.length > 20) history = history.slice(-20);

      ui.send.disabled = true;
      setTyping(ui.messages, true);

      fetch(API_BASE + '/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bot_id: BOT_ID, conversation_id: conversationId, message: clean, history: history.slice(0, -1) })
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          var reply = (data && data.reply) || 'Sorry, I received an empty reply.';
          history.push({ role: 'assistant', content: reply });
          if (history.length > 20) history = history.slice(-20);
          appendMessage(ui.messages, 'bot', reply);
        })
        .catch(function () {
          appendMessage(ui.messages, 'error', 'Network error — please try again in a moment.');
        })
        .finally(function () {
          setTyping(ui.messages, false);
          ui.send.disabled = false;
          ui.textarea.focus();
        });
    }

    window.AscendChat = {
      open: function () { ui.panel.hidden = false; ui.textarea.focus(); },
      close: function () { ui.panel.hidden = true; }
    };
  }

  // Boot: fetch the bot's public config, then render.
  fetch(API_BASE + '/api/bots/' + encodeURIComponent(BOT_ID) + '/public')
    .then(function (r) { return r.json(); })
    .then(function (config) {
      if (config && config.id) init(config);
      else console.warn('[AscendChat] Bot not found:', BOT_ID);
    })
    .catch(function () { console.warn('[AscendChat] Could not load bot config.'); });
})();
