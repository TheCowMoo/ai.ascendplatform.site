/* The microphone half of the spoken assistant.
 *
 * The socket goes browser -> Google using a one-use token our server minted, because a WSGI
 * worker must not be asked to hold a WebSocket. That means the browser relays the model's
 * tool calls back to us: /api/voice/tool is what executes, and it runs the same manager job a
 * typed request would. The voice can talk and delegate; it cannot act.
 *
 * Audio is never recorded or stored anywhere: mic frames go to the session, and what we keep
 * is the transcript the console already shows.
 */
(function () {
  'use strict';

  var state = { ws: null, mic: null, player: null, running: false, context: null, calls: 0 };

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

  // One queue for the model's speech, so barge-in can silence it instantly.
  function makePlayer(context) {
    var queue = [];
    var playing = false;

    function next() {
      if (!queue.length) { playing = false; return; }
      playing = true;
      var shorts = queue.shift();
      var buffer = context.createBuffer(1, shorts.length, 24000);
      var channel = buffer.getChannelData(0);
      for (var i = 0; i < shorts.length; i++) channel[i] = shorts[i] / 32768;
      var source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      source.onended = next;
      source.start();
    }

    return {
      push: function (bytes) {
        // 24 kHz, 16-bit mono, little endian: what the Live API sends back.
        var view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        var count = Math.floor(bytes.length / 2);
        var shorts = new Int16Array(count);
        for (var i = 0; i < count; i++) shorts[i] = view.getInt16(i * 2, true);
        queue.push(shorts);
        if (!playing) next();
      },
      stop: function () { queue.length = 0; playing = false; }
    };
  }

  function downsample(input, from, to) {
    if (to === from) return input;
    var ratio = from / to;
    var length = Math.floor(input.length / ratio);
    var out = new Float32Array(length);
    for (var i = 0; i < length; i++) out[i] = input[Math.floor(i * ratio)];
    return out;
  }

  function startMic(context, onChunk) {
    // ScriptProcessor rather than AudioWorklet on purpose: a worklet needs its own module file
    // and a same-origin URL load. The sample plumbing here does not change when it is swapped.
    var source = context.createMediaStreamSource(state.mic);
    var node = context.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = function (event) {
      var samples = downsample(event.inputBuffer.getChannelData(0), context.sampleRate, 16000);
      var shorts = new Int16Array(samples.length);
      for (var i = 0; i < samples.length; i++) {
        var sample = Math.max(-1, Math.min(1, samples[i]));
        shorts[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      }
      onChunk(new Uint8Array(shorts.buffer));
    };
    source.connect(node);
    node.connect(context.destination);   // keeps the node alive; it outputs silence
    return { source: source, node: node };
  }

  function send(payload) {
    if (state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify(payload));
  }

  function stop() {
    state.running = false;
    if (state.ws) {
      try { state.ws.close(); } catch (e) { /* already closing */ }
      state.ws = null;
    }
    if (state.player) { state.player.stop(); state.player = null; }
    if (state.mic) {
      state.mic.getTracks().forEach(function (track) { track.stop(); });
      state.mic = null;
    }
    if (state.context) {
      try { state.context.close(); } catch (e) { /* already closed */ }
      state.context = null;
    }
  }

  // The one thing the voice may call: our own agents, through the route that runs jobs.
  function delegate(call, hooks) {
    var turnId = (call.id || 'turn') + ':' + state.calls;
    state.calls += 1;
    var instruction = (call.args && call.args.instruction) || '';
    (hooks.onDelegate || function () {})(instruction);
    fetch('/api/voice/tool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: call.name, args: call.args || {}, turn_id: turnId })
    }).then(function (response) {
      return response.json().then(function (data) { return data; });
    }).then(function (data) {
      var message = data.message || data.error || 'No answer from the team';
      if (data.job_id) (hooks.onJob || function () {})(data.job_id, message);
      send({ toolResponse: { functionResponses: [{
        id: call.id, name: call.name, response: { result: message } }] } });
    }).catch(function () {
      send({ toolResponse: { functionResponses: [{
        id: call.id, name: call.name,
        response: { result: 'The team could not be reached - say so, and suggest typing it.' }
      }] } });
    });
  }

  function start(hooks) {
    hooks = hooks || {};
    if (state.running) { stop(); return Promise.resolve(false); }
    var say = hooks.onState || function () {};
    var warn = hooks.onError || function () {};
    say('connecting');

    return fetch('/api/voice/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: hooks.page || '' })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || 'Voice is not available');
        return data;
      });
    }).then(function (data) {
      return navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
      }).then(function (stream) {
        state.mic = stream;
        return data;
      });
    }).then(function (data) {
      state.context = new (window.AudioContext || window.webkitAudioContext)();
      state.player = makePlayer(state.context);
      state.calls = 0;

      var ws = new WebSocket(data.endpoint + '?access_token=' + encodeURIComponent(data.token));
      state.ws = ws;
      ws.onopen = function () {
        send(data.setup);
        startMic(state.context, function (bytes) {
          if (state.running) {
            send({ realtimeInput: { mediaChunks: [{
              mimeType: 'audio/pcm;rate=16000', data: bytesToBase64(bytes) }] } });
          }
        });
        state.running = true;
        say('listening');
      };
      ws.onmessage = function (event) {
        var message;
        try { message = JSON.parse(event.data); } catch (e) { return; }
        if (message.setupComplete) { say('listening'); return; }
        var content = message.serverContent;
        if (content) {
          if (content.inputTranscription && content.inputTranscription.text) {
            (hooks.onHeard || function () {})(content.inputTranscription.text);
          }
          if (content.outputTranscription && content.outputTranscription.text) {
            (hooks.onSaid || function () {})(content.outputTranscription.text);
          }
          if (content.interrupted) {
            // Barge-in: the model stopped talking, so its queued audio stops with it.
            if (state.player) state.player.stop();
            say('listening');
          }
          var parts = content.modelTurn && content.modelTurn.parts;
          if (parts) {
            parts.forEach(function (part) {
              if (part.inlineData && part.inlineData.data) {
                say('speaking');
                state.player.push(base64ToBytes(part.inlineData.data));
              }
            });
          }
        }
        if (message.toolCall && message.toolCall.functionCalls) {
          say('thinking');
          message.toolCall.functionCalls.forEach(function (call) { delegate(call, hooks); });
        }
      };
      ws.onerror = function () { warn('The voice connection failed - typing still works.'); };
      ws.onclose = function () {
        state.running = false;
        say('idle');
        if (state.mic) {
          state.mic.getTracks().forEach(function (track) { track.stop(); });
          state.mic = null;
        }
      };
      return true;
    }).catch(function (error) {
      stop();
      say('idle');
      warn(error.message);
      return false;
    });
  }

  window.AscendVoice = {
    start: start,
    stop: stop,
    running: function () { return state.running; }
  };
})();
