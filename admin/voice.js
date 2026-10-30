/* The microphone half of the spoken assistant, and its one screen.
 *
 * The socket goes browser -> Google using a one-use token our server minted, because a WSGI
 * worker must not be asked to hold a WebSocket. That means the browser relays the model's
 * tool calls back to us: /api/voice/tool is what executes, and it runs the same manager job a
 * typed request would. The voice can talk and delegate; it cannot act.
 *
 * Three rules this file exists to keep:
 *   1. "Listening" is only said after setupComplete. An open socket is not a session.
 *   2. Every way a session can fail is shown in the server's own words - error, goAway,
 *      close code and reason - because a silent close is the bug that cost us a week.
 *   3. The orb is driven by the audio's RMS, never by a timer. If it moves, sound is moving.
 *
 * Audio is never recorded or stored anywhere: mic frames go to the session, and what we keep
 * is the transcript the console already shows.
 */
(function () {
  'use strict';

  var PHASES = {
    connecting: 'Connecting…',
    listening: 'Listening',
    thinking: 'Thinking…',
    speaking: 'Speaking',
    error: 'The session stopped'
  };
  var SETUP_TIMEOUT = 12000;          // no setupComplete by now is a failure, not a wait
  var TOOL_TIMEOUT = 30000;
  var MUTE_WHILE_SPEAKING = false;    // true would silence barge-in; see startMic()

  var state = {
    phase: 'idle', ws: null, mic: null, context: null, player: null,
    micSource: null, micNode: null, micSilent: null, micAnalyser: null,
    outAnalyser: null, micData: null, outData: null, raf: 0,
    level: 0, settled: false, intentional: false,
    session: null, turns: [], opts: {}, setupTimer: 0,
    lastError: '', closeInfo: '', calls: 0, sessionId: '',
    generation: 0, opener: null
  };
  var ui = null;
  var sessionCounter = 0;

  function bytesToBase64(bytes) {
    var binary = '';
    for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  function base64ToBytes(text) {
    var binary = atob(text);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // Box filter rather than decimation: 48 kHz -> 16 kHz by picking every third sample aliases
  // the speech band and makes the transcriber's job harder for no saving at all.
  function downsample(input, from, to) {
    if (to === from) return input;
    var ratio = from / to;
    var length = Math.floor(input.length / ratio);
    var out = new Float32Array(length);
    for (var i = 0; i < length; i++) {
      var start = Math.floor(i * ratio);
      var end = Math.min(input.length, Math.floor((i + 1) * ratio));
      var sum = 0;
      var count = 0;
      for (var j = start; j < end; j++) { sum += input[j]; count++; }
      out[i] = count ? sum / count : (input[start] || 0);
    }
    return out;
  }

  // One queue for the model's speech. Sources are scheduled on a playhead rather than started
  // immediately, so chunks join without gaps or overlap; stop() really stops the current one.
  function makePlayer(context) {
    var queue = [];
    var playing = false;
    var playhead = 0;
    var active = [];
    var gain = context.createGain();
    var analyser = context.createAnalyser();
    analyser.fftSize = 512;
    gain.connect(analyser);
    analyser.connect(context.destination);

    function next() {
      if (!queue.length) { playing = false; return; }
      playing = true;
      var samples = queue.shift();
      var buffer = context.createBuffer(1, samples.length, 24000);
      var channel = buffer.getChannelData(0);
      for (var i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
      var source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(gain);
      var when = Math.max(context.currentTime + 0.02, playhead);
      playhead = when + buffer.duration;
      active.push(source);
      source.onended = function () {
        var index = active.indexOf(source);
        if (index >= 0) active.splice(index, 1);
        next();
      };
      source.start(when);
    }

    return {
      analyser: analyser,
      push: function (bytes) {
        // 24 kHz, 16-bit mono, little endian: what the Live API sends back.
        var view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        var count = Math.floor(bytes.length / 2);
        var samples = new Int16Array(count);
        for (var i = 0; i < count; i++) samples[i] = view.getInt16(i * 2, true);
        queue.push(samples);
        if (!playing) next();
      },
      stop: function () {
        queue.length = 0;
        playing = false;
        playhead = 0;
        active.slice().forEach(function (source) {
          try { source.stop(); } catch (e) { /* already ended */ }
        });
        active.length = 0;
      }
    };
  }

  function readLevel(analyser, data) {
    if (!analyser || !data) return 0;
    var sum = 0;
    var i;
    if (analyser.getFloatTimeDomainData) {
      analyser.getFloatTimeDomainData(data);
      for (i = 0; i < data.length; i++) sum += data[i] * data[i];
    } else if (analyser.getByteTimeDomainData) {
      var bytes = new Uint8Array(data.length);
      analyser.getByteTimeDomainData(bytes);
      for (i = 0; i < bytes.length; i++) {
        var value = (bytes[i] - 128) / 128;
        sum += value * value;
      }
    } else {
      return 0;
    }
    return Math.min(1, Math.sqrt(sum / data.length) * 7);
  }

  // ---------- The one screen ----------
  // Built once, hidden until asked for. role=dialog + aria-modal, because it is a mode,
  // not a corner of the page; Esc closes it; focus returns to whatever opened it.
  function ensureUi() {
    if (ui) return ui;

    var overlay = make('div', 'voice-overlay');
    overlay.hidden = true;
    var panel = make('div', 'voice-panel');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', 'voice-title');

    panel.appendChild(make('p', 'voice-kicker', 'Ascend voice'));
    var title = make('h2', 'voice-title', 'Talk to the team');
    title.id = 'voice-title';
    panel.appendChild(title);

    var stage = make('div', 'voice-stage');
    stage.setAttribute('data-phase', 'idle');
    var orb = make('div', 'voice-orb');
    orb.appendChild(make('span', 'voice-ring'));
    orb.appendChild(make('span', 'voice-core'));
    orb.appendChild(make('span', 'voice-dot a'));
    orb.appendChild(make('span', 'voice-dot b'));
    stage.appendChild(orb);
    panel.appendChild(stage);

    var stateLine = make('p', 'voice-state', 'Connecting…');
    stateLine.setAttribute('aria-live', 'polite');
    var detail = make('p', 'voice-detail');
    detail.setAttribute('aria-live', 'polite');
    panel.appendChild(stateLine);
    panel.appendChild(detail);

    var transcript = make('div', 'voice-transcript');
    transcript.setAttribute('aria-live', 'polite');
    panel.appendChild(transcript);

    var note = make('p', 'voice-note');
    panel.appendChild(note);

    var actions = make('div', 'voice-actions');
    var stopButton = make('button', 'btn btn-primary', 'Stop');
    stopButton.type = 'button';
    stopButton.onclick = stop;
    var minimiseButton = make('button', 'btn btn-ghost', 'Minimise');
    minimiseButton.type = 'button';
    minimiseButton.onclick = minimise;
    var retryButton = make('button', 'btn btn-ghost', 'Try again');
    retryButton.type = 'button';
    retryButton.hidden = true;
    retryButton.onclick = retry;
    var copyButton = make('button', 'btn btn-ghost', 'Copy diagnostics');
    copyButton.type = 'button';
    copyButton.onclick = copyDiagnostics;
    actions.appendChild(stopButton);
    actions.appendChild(minimiseButton);
    actions.appendChild(retryButton);
    actions.appendChild(copyButton);
    panel.appendChild(actions);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    var pill = make('div', 'voice-pill');
    pill.hidden = true;
    pill.setAttribute('data-phase', 'idle');
    var pillDot = make('span', 'voice-pill-dot');
    var pillLabel = make('span', 'voice-pill-label', 'Voice');
    var pillOpen = make('button', 'btn btn-ghost btn-sm', 'Open');
    pillOpen.type = 'button';
    pillOpen.onclick = restore;
    var pillStop = make('button', 'btn btn-ghost btn-sm', 'Stop');
    pillStop.type = 'button';
    pillStop.onclick = stop;
    pill.appendChild(pillDot);
    pill.appendChild(pillLabel);
    pill.appendChild(pillOpen);
    pill.appendChild(pillStop);
    document.body.appendChild(pill);

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && ui && !ui.overlay.hidden) stop();
    });

    // A modal must keep Tab inside it. The order is the visible action buttons only.
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Tab' || !ui || ui.overlay.hidden) return;
      var order = [ui.stop, ui.minimise, ui.retry, ui.copy].filter(function (button) {
        return button && !button.hidden;
      });
      if (!order.length) return;
      var first = order[0];
      var last = order[order.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    ui = {
      overlay: overlay, panel: panel, stage: stage, orb: orb,
      state: stateLine, detail: detail, transcript: transcript, note: note,
      stop: stopButton, minimise: minimiseButton, retry: retryButton,
      copy: copyButton, pill: pill, pillDot: pillDot, pillLabel: pillLabel,
      pillOpen: pillOpen
    };
    return ui;
  }

  function isLive() {
    return state.phase === 'connecting' || state.phase === 'listening' ||
      state.phase === 'thinking' || state.phase === 'speaking';
  }

  function setPhase(phase, label, detail) {
    state.phase = phase;
    if (!ui) return;
    ui.stage.setAttribute('data-phase', phase);
    ui.pill.setAttribute('data-phase', phase);
    ui.state.textContent = label || PHASES[phase] || '';
    ui.detail.textContent = detail || '';
    ui.pillLabel.textContent = label || PHASES[phase] || 'Voice';
    ui.stop.textContent = phase === 'error' ? 'Close' : 'Stop';
    ui.retry.hidden = phase !== 'error';
    if (state.opts.onState) {
      try { state.opts.onState(phase, label || PHASES[phase] || ''); } catch (e) { /* a hook is not the product */ }
    }
  }

  function showOverlay() {
    if (!ui) return;
    ui.overlay.hidden = false;
    ui.pill.hidden = true;
    try { ui.stop.focus(); } catch (e) { /* not focusable yet */ }
  }

  function hideAll() {
    if (!ui) return;
    ui.overlay.hidden = true;
    ui.pill.hidden = true;
  }


  function scrollTranscript() {
    if (ui) ui.transcript.scrollTop = ui.transcript.scrollHeight;
  }

  function turnNode(turn) {
    var line = make('div', 'voice-turn ' + turn.role);
    line.appendChild(make('span', 'voice-who', turn.role === 'user' ? 'You' : 'Assistant'));
    line.appendChild(make('p', 'voice-body', turn.text));
    ui.transcript.appendChild(line);
    if (state.turns.length > 40) {
      state.turns.shift();
      if (ui.transcript.firstChild) ui.transcript.removeChild(ui.transcript.firstChild);
    }
    scrollTranscript();
    return line;
  }

  // Transcription arrives as chunks. A chunk that starts with what we already have is the
  // cumulative form and replaces it; anything else is appended. One bubble per turn, always.
  function appendTranscript(role, text, final) {
    if (!ui || !text) return;
    var last = state.turns[state.turns.length - 1];
    if (!last || last.role !== role || last.final) {
      last = { role: role, text: '', final: false };
      state.turns.push(last);
      turnNode(last);
    }
    if (text.indexOf(last.text) === 0) last.text = text;
    else last.text += text;
    var line = ui.transcript.lastChild;
    var body = line && line.querySelector ? line.querySelector('.voice-body') : null;
    if (body) body.textContent = last.text;
    last.final = !!final;
    scrollTranscript();
    if (final) emitTurn(last);
  }

  function emitTurn(turn) {
    if (!turn || !turn.text || !state.opts.onTurn) return;
    try {
      state.opts.onTurn({ role: turn.role, text: turn.text, final: true });
    } catch (e) { /* the transcript is ours; the caller's copy is a bonus */ }
  }

  function finalizeTurn(role) {
    var last = null;
    for (var i = state.turns.length - 1; i >= 0; i--) {
      if (state.turns[i].role === role) { last = state.turns[i]; break; }
    }
    if (!last || last.final) return;
    last.final = true;
    emitTurn(last);
  }

  function appendSystemNote(text) {
    if (!ui || !text) return;
    ui.transcript.appendChild(make('p', 'voice-system', text));
    scrollTranscript();
  }

  function updateNote() {
    if (!ui) return;
    if (!state.session) { ui.note.textContent = 'Preparing the session…'; return; }
    ui.note.textContent = 'Voice ' + state.session.voice + ' · ' + state.session.model +
      ' · ' + state.session.api_version + ' · ' + state.session.remaining_minutes +
      ' min left today';
  }

  function paintLevel() {
    if (!ui) return;
    var value = state.level.toFixed(3);
    ui.orb.style.setProperty('--voice-level', value);
    ui.pill.style.setProperty('--voice-level', value);
  }

  function startLevelLoop() {
    if (state.raf) return;
    function tick() {
      state.raf = window.requestAnimationFrame(tick);
      var target = 0;
      if (state.phase === 'speaking') {
        target = readLevel(state.outAnalyser, state.outData);
      } else if (state.phase === 'listening') {
        target = readLevel(state.micAnalyser, state.micData);
      }
      state.level = state.level * 0.8 + target * 0.2;
      paintLevel();
    }
    state.raf = window.requestAnimationFrame(tick);
  }

  function stopLevelLoop() {
    if (state.raf) {
      try { window.cancelAnimationFrame(state.raf); } catch (e) { /* nothing to cancel */ }
    }
    state.raf = 0;
    state.level = 0;
    paintLevel();
  }


  // ---------- Audio ----------
  function startMic(context) {
    var source = context.createMediaStreamSource(state.mic);
    var analyser = context.createAnalyser();
    analyser.fftSize = 512;
    var silent = context.createGain();
    silent.gain.value = 0;               // the analyser needs a path to the destination
    var node = context.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = function (event) {
      // Frames are only sent once the server has accepted the session; anything earlier
      // belongs to a session that does not exist.
      if (!state.settled || !isLive()) return;
      // Cutting frames while the model speaks would also cut barge-in. AEC plus the
      // server's `interrupted` message is the mechanism; this switch is for echo only.
      if (MUTE_WHILE_SPEAKING && state.phase === 'speaking') return;
      var samples = downsample(event.inputBuffer.getChannelData(0), context.sampleRate, 16000);
      var shorts = new Int16Array(samples.length);
      for (var i = 0; i < samples.length; i++) {
        var sample = Math.max(-1, Math.min(1, samples[i]));
        shorts[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      }
      send({ realtimeInput: { mediaChunks: [{
        mimeType: 'audio/pcm;rate=16000', data: bytesToBase64(new Uint8Array(shorts.buffer)) }] } });
    };
    source.connect(analyser);
    analyser.connect(silent);
    silent.connect(context.destination);
    source.connect(node);
    node.connect(context.destination);   // keeps the node alive; it outputs silence
    state.micSource = source;
    state.micAnalyser = analyser;
    state.micSilent = silent;
    state.micNode = node;
    state.micData = new Float32Array(analyser.fftSize);
  }

  function teardownAudio() {
    stopLevelLoop();
    if (state.player) { state.player.stop(); state.player = null; }
    if (state.micNode) {
      try { state.micNode.disconnect(); } catch (e) { /* already gone */ }
      state.micNode.onaudioprocess = null;
      state.micNode = null;
    }
    if (state.micSource) {
      try { state.micSource.disconnect(); } catch (e) { /* already gone */ }
      state.micSource = null;
    }
    if (state.micSilent) {
      try { state.micSilent.disconnect(); } catch (e) { /* already gone */ }
      state.micSilent = null;
    }
    state.micAnalyser = null;
    state.outAnalyser = null;
    state.micData = null;
    state.outData = null;
    if (state.mic) {
      state.mic.getTracks().forEach(function (track) { track.stop(); });
      state.mic = null;
    }
    if (state.context) {
      try { state.context.close(); } catch (e) { /* already closed */ }
      state.context = null;
    }
  }

  function teardownTransport() {
    if (state.setupTimer) {
      try { window.clearTimeout(state.setupTimer); } catch (e) { /* no timer */ }
      state.setupTimer = 0;
    }
    if (state.ws) {
      var ws = state.ws;
      state.ws = null;                   // the onclose identity check ignores the echo
      try { ws.close(); } catch (e) { /* already closing */ }
    }
    teardownAudio();
  }

  function send(payload) {
    if (!state.ws || state.ws.readyState !== 1) return false;
    try {
      state.ws.send(JSON.stringify(payload));
      return true;
    } catch (e) {
      return false;
    }
  }

  function micMessage(error) {
    var name = error && error.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return 'The microphone was blocked. Allow it in the address bar, then press Try again.';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      return 'No microphone was found. Plug one in, then press Try again.';
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      return 'Another app is using the microphone. Close it, then press Try again.';
    }
    return 'The microphone could not be opened: ' + ((error && error.message) || error);
  }

  // The microphone is opened BEFORE the token is minted: the token has sixty seconds to
  // start a session, and a permission prompt the user reads for a minute would spend it.
  function askMic(generation) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error(
        'This browser does not offer a microphone here. Use Chrome, Edge or Safari over https.'));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    }).then(function (stream) {
      if (generation !== state.generation) {
        stream.getTracks().forEach(function (track) { track.stop(); });
        throw { cancelled: true };
      }
      state.mic = stream;
      return stream;
    }, function (error) {
      throw new Error(micMessage(error));
    });
  }


  function fetchSession() {
    return fetch('/api/voice/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: (state.opts && state.opts.page) || '' })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var error = new Error(data.error || ('Voice is not available (' + response.status + ')'));
          error.payload = data;
          throw error;
        }
        return data;
      });
    });
  }

  function openAudio(generation) {
    var resume = state.context.resume ? state.context.resume().catch(function () {}) : Promise.resolve();
    return resume.then(function () {
      if (generation !== state.generation) throw { cancelled: true };
      state.player = makePlayer(state.context);
      state.outAnalyser = state.player.analyser;
      state.outData = new Float32Array(state.outAnalyser.fftSize);
      startMic(state.context);
      startLevelLoop();
    });
  }

  function connect(data, generation) {
    var ws;
    try {
      ws = new WebSocket(data.endpoint + '?access_token=' + encodeURIComponent(data.token));
    } catch (error) {
      fail('The voice socket could not be opened.', String((error && error.message) || error));
      return;
    }
    state.ws = ws;
    ws.onopen = function () {
      if (state.ws !== ws || generation !== state.generation) return;
      send(data.setup);
      setPhase('connecting', PHASES.connecting,
               'Waiting for the server to accept the session · ' +
               data.model + ' · ' + data.api_version);
      state.setupTimer = window.setTimeout(function () { setupTimeout(generation); }, SETUP_TIMEOUT);
    };
    ws.onmessage = function (event) {
      if (state.ws !== ws || generation !== state.generation) return;
      var message;
      try { message = JSON.parse(event.data); } catch (e) { return; }
      handleMessage(message);
    };
    ws.onerror = function () {
      if (state.ws === ws) state.lastError = 'The voice connection reported a transport error.';
    };
    ws.onclose = function (event) {
      if (state.ws !== ws) return;
      state.ws = null;
      handleClose(event);
    };
  }

  function setupTimeout(generation) {
    if (generation !== state.generation || state.settled || state.phase === 'error') return;
    var model = state.session ? state.session.model : 'unknown';
    var version = state.session ? state.session.api_version : 'unknown';
    fail('The socket opened, but the server never confirmed the session (no setupComplete in 12 seconds).',
         'Model ' + model + ' on ' + version + '. Open Settings -> Voice -> Test; it lists ' +
         'the Live models this key can open and the API version. The Gemini text model in ' +
         'AI providers is not the voice model.');
    teardownTransport();
  }


  // ---------- What the server says ----------
  function handleMessage(message) {
    if (message.setupComplete) return handleSetupComplete();
    if (message.error) return handleServerError(message.error);
    if (message.goAway) return handleGoAway(message.goAway);
    if (message.toolCall) return handleToolCall(message.toolCall);
    if (message.toolCallCancellation) return handleCancellation(message.toolCallCancellation);
    if (message.serverContent) return handleServerContent(message.serverContent);
  }

  // The only place a session becomes "listening". An open socket is not a session.
  function handleSetupComplete() {
    if (state.setupTimer) {
      try { window.clearTimeout(state.setupTimer); } catch (e) { /* no timer */ }
      state.setupTimer = 0;
    }
    state.settled = true;
    setPhase('listening', PHASES.listening, 'Speak normally. Tap Stop when you are done.');
  }

  function handleServerContent(content) {
    content = content || {};
    if (content.interrupted) {
      if (state.player) state.player.stop();
      setPhase('listening');
    }
    var heard = (content.inputTranscription && content.inputTranscription.text) ||
                (content.interimInputTranscription && content.interimInputTranscription.text);
    if (heard) appendTranscript('user', heard, false);

    var said = content.outputTranscription && content.outputTranscription.text;
    if (said) {
      finalizeTurn('user');
      appendTranscript('assistant', said, false);
    }

    var parts = content.modelTurn && content.modelTurn.parts;
    if (parts) {
      parts.forEach(function (part) {
        if (part.inlineData && part.inlineData.data) {
          finalizeTurn('user');
          setPhase('speaking');
          if (state.player) state.player.push(base64ToBytes(part.inlineData.data));
        }
      });
    }

    if (content.turnComplete) {
      finalizeTurn('user');
      finalizeTurn('assistant');
      if (state.phase === 'speaking' || state.phase === 'thinking') setPhase('listening');
    }
    if (content.waitingForInput && state.phase !== 'speaking') setPhase('listening');
  }

  function timeLeftText(value) {
    if (!value) return 'a moment';
    if (typeof value === 'string') return value;
    if (typeof value === 'number') return value + ' seconds';
    if (value.seconds !== undefined) return value.seconds + ' seconds';
    return String(value);
  }

  function handleGoAway(goAway) {
    var left = timeLeftText(goAway && goAway.timeLeft);
    appendSystemNote('The session closes in ' + left + '.');
    if (ui) ui.detail.textContent = 'The session closes in ' + left + '. Press Try again for a new one.';
  }

  function modelHint() {
    if (!state.session) return '';
    return 'Model ' + state.session.model + ' · ' + state.session.api_version + '.';
  }

  function fail(message, detail) {
    state.lastError = message;
    teardownAudio();
    setPhase('error', 'The session stopped', message + (detail ? ' ' + detail : ''));
    if (detail) appendSystemNote(detail);
  }


  function handleServerError(error) {
    var message = (error && (error.message || error.status)) || 'The server refused the session.';
    var code = error && error.code ? ' (code ' + error.code + ')' : '';
    fail('The server refused the session' + code + ': ' + message, modelHint());
    teardownTransport();
  }

  function closeHint(code) {
    switch (code) {
      case 1000: return 'The server closed the session normally.';
      case 1001: return 'The server is going away.';
      case 1006: return 'The connection dropped without a reason - often a blocked WebSocket, a proxy, or a network change.';
      case 1008: return 'The server refused the session (policy or authentication).';
      case 1011: return 'The server hit an internal error.';
      case 1015: return 'The TLS handshake failed.';
      default: return code ? 'The socket closed with code ' + code + '.' : 'The socket closed.';
    }
  }

  function handleClose(event) {
    if (state.setupTimer) {
      try { window.clearTimeout(state.setupTimer); } catch (e) { /* no timer */ }
      state.setupTimer = 0;
    }
    teardownAudio();
    if (state.intentional) { hideAll(); return; }
    if (state.phase === 'error') return;        // a specific error already won
    var code = event && typeof event.code === 'number' ? event.code : 0;
    var reason = event && event.reason ? String(event.reason).trim() : '';
    state.closeInfo = 'code ' + code + (reason ? ' · ' + reason : '');
    var suffix = reason ? ': ' + reason : (code ? ' (code ' + code + ')' : '');
    var detail = closeHint(code) + ' ' + modelHint();
    if (!state.settled) {
      fail('The session was rejected' + suffix, detail);
    } else {
      fail('The session ended' + suffix, detail);
    }
  }


  // ---------- The one tool ----------
  // A stable id per function call: the model may retry the same call, and the server uses
  // this key to refuse a second job. A counter would defeat that by changing on every retry.
  function stableId(call) {
    var text = (call.name || '') + '|' + JSON.stringify(call.args || {});
    var hash = 0;
    for (var i = 0; i < text.length; i++) {
      hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    }
    return 'h' + (hash >>> 0).toString(36);
  }

  function handleToolCall(toolCall) {
    var calls = (toolCall && toolCall.functionCalls) || [];
    if (!calls.length) return;
    setPhase('thinking', PHASES.thinking, 'The team has the request.');
    calls.forEach(delegate);
  }

  function handleCancellation(cancellation) {
    var calls = (cancellation && cancellation.functionCallIds) || [];
    if (calls.length) appendSystemNote('The voice cancelled that request before it was sent.');
  }

  function sendToolResponse(call, message) {
    send({ toolResponse: { functionResponses: [{
      id: call.id, name: call.name, response: { result: message } }] } });
  }

  function delegate(call) {
    var instruction = (call.args && call.args.instruction) || '';
    appendSystemNote(instruction ? 'On it: ' + instruction : 'Handing that to the team…');
    var turnId = state.sessionId + ':' + (call.id || stableId(call));
    state.calls += 1;

    var options = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: call.name, args: call.args || {}, turn_id: turnId })
    };
    var controller = window.AbortController ? new AbortController() : null;
    var timer = 0;
    if (controller) {
      options.signal = controller.signal;
      timer = window.setTimeout(function () { controller.abort(); }, TOOL_TIMEOUT);
    }

    fetch('/api/voice/tool', options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok, data: data };
      });
    }).then(function (result) {
      var data = result.data || {};
      var message = data.message || data.error ||
        (result.ok ? 'The team has it.' : 'The team could not take that request.');
      if (data.job_id && state.opts.onJob) {
        try { state.opts.onJob(data.job_id, message); } catch (e) { /* a hook is not the product */ }
      }
      appendSystemNote(message);
      sendToolResponse(call, message);
    }).catch(function (error) {
      var message = (error && error.name === 'AbortError')
        ? 'The team did not answer in time - say so, and suggest typing it.'
        : 'The team could not be reached - say so, and suggest typing it.';
      appendSystemNote(message);
      sendToolResponse(call, message);
    }).then(function () {
      if (timer) window.clearTimeout(timer);
      if (state.phase === 'thinking') {
        setPhase('thinking', PHASES.thinking, 'Waiting for the voice to pick it up.');
      }
    });
  }


  // ---------- Lifecycle ----------
  function open(options) {
    ensureUi();
    if (isLive()) { restore(); return Promise.resolve(true); }
    if (state.phase === 'error') teardownTransport();
    teardownAudio();

    state.opts = options || {};
    state.session = null;
    state.turns = [];
    state.calls = 0;
    state.settled = false;
    state.intentional = false;
    state.lastError = '';
    state.closeInfo = '';
    state.generation += 1;
    var generation = state.generation;
    state.opener = document.activeElement;

    ui.transcript.innerHTML = '';
    ui.detail.textContent = '';
    ui.retry.hidden = true;
    ui.stop.textContent = 'Stop';
    showOverlay();
    updateNote();

    // Created on the click, not after the awaits: a context born after two awaits can start
    // suspended under the autoplay policy, which would silence both analysers.
    try {
      var Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw new Error('This browser has no AudioContext.');
      state.context = new Audio();
      if (state.context.state === 'suspended' && state.context.resume) {
        state.context.resume();
      }
    } catch (error) {
      fail('This browser could not open an audio context.', String((error && error.message) || error));
      return Promise.resolve(false);
    }

    return startSession(generation);
  }

  function startSession(generation) {
    setPhase('connecting', 'Asking for the microphone…', 'Your browser asks once.');
    return askMic(generation).then(function () {
      if (generation !== state.generation) throw { cancelled: true };
      setPhase('connecting', 'Minting a session…', 'The key stays on the server.');
      return fetchSession();
    }).then(function (data) {
      if (generation !== state.generation) throw { cancelled: true };
      state.session = data;
      state.sessionId = 's' + Date.now().toString(36) + '-' + (++sessionCounter);
      updateNote();
      if (data.model_note) appendSystemNote(data.model_note);
      return openAudio(generation).then(function () {
        if (generation !== state.generation) throw { cancelled: true };
        connect(data, generation);
        return true;
      });
    }).catch(function (error) {
      if (error && error.cancelled) return false;
      if (state.intentional || generation !== state.generation) return false;
      fail((error && error.message) || 'Voice could not start.',
           (error && error.payload && error.payload.model)
             ? 'Model ' + error.payload.model + '.' : '');
      return false;
    });
  }

  function stop() {
    state.intentional = true;
    state.generation += 1;
    teardownTransport();
    hideAll();
    setPhase('idle');
    var close = state.opts.onClose;
    state.opts = {};
    if (close) { try { close(); } catch (e) { /* a hook is not the product */ } }
    if (state.opener && state.opener.focus) {
      try { state.opener.focus(); } catch (e) { /* the opener went away */ }
    }
    state.opener = null;
  }

  function minimise() {
    if (!isLive() || !ui) return;
    ui.overlay.hidden = true;
    ui.pill.hidden = false;
    try { ui.pillOpen.focus(); } catch (e) { /* not focusable */ }
  }

  function restore() {
    if (!ui) return;
    ui.pill.hidden = true;
    ui.overlay.hidden = false;
    try { ui.stop.focus(); } catch (e) { /* not focusable */ }
  }

  function retry() {
    var options = state.opts;
    stop();
    return open(options);
  }

  function diagnostics() {
    return [
      'model: ' + (state.session ? state.session.model : 'unknown'),
      'api: ' + (state.session ? state.session.api_version : 'unknown'),
      'endpoint: ' + (state.session ? state.session.endpoint : 'unknown'),
      'phase: ' + state.phase,
      'settled: ' + state.settled,
      'close: ' + (state.closeInfo || 'none'),
      'error: ' + (state.lastError || 'none'),
      'turns: ' + state.turns.length
    ].join('\n');
  }

  function copyDiagnostics() {
    var text = diagnostics();
    function fallback() {
      var box = make('textarea', 'voice-copy-box');
      box.value = text;
      document.body.appendChild(box);
      box.select();
      try {
        var copied = document.execCommand('copy');
        appendSystemNote(copied ? 'Diagnostics copied.'
          : 'Could not copy. The diagnostics are: ' + text);
      } catch (e) {
        appendSystemNote('Could not copy. The diagnostics are: ' + text);
      }
      document.body.removeChild(box);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        appendSystemNote('Diagnostics copied.');
      }, fallback);
    } else {
      fallback();
    }
  }

  window.AscendVoice = {
    open: open,
    start: open,             // the old name, kept so no caller has to change twice
    stop: stop,
    minimise: minimise,
    restore: restore,
    running: isLive,
    phase: function () { return state.phase; }
  };
})();
