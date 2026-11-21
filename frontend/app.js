'use strict';
// Voice Field Register frontend. No secrets live here; the browser only talks to our own backend.
const $ = id => document.getElementById(id);
const UI = {
  hi: { tap: 'बोलने के लिए दबाएँ', stop: 'रोकने के लिए दबाएँ', listening: 'सुन रहे हैं…', thinking: 'प्रोसेस हो रहा है…',
        ready: 'तैयार। माइक दबाएँ।', yes: 'हाँ', no: 'नहीं', missing: 'बाकी', none: 'कोई नहीं', notset: 'तय नहीं', saved: 'रिकॉर्ड सेव हो गया' },
  en: { tap: 'Tap to speak', stop: 'Tap to stop', listening: 'Listening…', thinking: 'Working…',
        ready: 'Ready. Tap the microphone.', yes: 'Yes', no: 'No', missing: 'Missing', none: 'none', notset: 'Not set', saved: 'Record saved' },
};
const S = {
  ws: null, sid: localStorage.getItem('vfr_sid'), lang: localStorage.getItem('vfr_lang') || 'hi',
  ready: false, rec: false, mode: null, retry: 0, ctx: null, workletOK: false, stream: null, node: null, src: null,
  playT: 0, sources: [], loud: 0, lastUpdate: null,
};
const t = () => UI[S.lang] || UI.en;

function status(msg) { $('status').textContent = msg; }
function showError(msg, retry) { $('errorText').textContent = msg; $('retryBtn').hidden = !retry; $('errorBar').hidden = false; }
function clearError() { $('errorBar').hidden = true; }
function setConn(kind, text) { const c = $('conn'); c.className = 'pill ' + kind; c.textContent = text; }

// ---------------- WebSocket ----------------
function connect() {
  setConn('wait', navigator.onLine ? 'Connecting…' : 'Offline');
  const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
  const ws = new WebSocket(url);
  S.ws = ws;
  ws.onopen = () => {
    S.retry = 0;
    // Only a session id is kept in the browser (no patient data), so a dropped connection can resume safely.
    if (S.sid) send({ type: 'resume', session_id: S.sid, language: S.lang });
    else send({ type: 'start', language: S.lang });
  };
  ws.onmessage = ev => { let m; try { m = JSON.parse(ev.data); } catch { return; } onMessage(m); };
  ws.onclose = () => {
    S.ready = false; $('mic').disabled = true; stopPlayback();
    if (S.rec) stopRecording(false);
    const wait = Math.min(10000, 1000 * 2 ** S.retry++);
    setConn('off', 'Reconnecting…');
    status('Connection lost. Retrying in ' + Math.round(wait / 1000) + ' s…');
    setTimeout(connect, wait);
  };
  ws.onerror = () => { try { ws.close(); } catch { /* already closing */ } };
}
function send(obj) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(obj)); }

function onMessage(m) {
  switch (m.type) {
    case 'ready':
      S.sid = m.session_id; localStorage.setItem('vfr_sid', S.sid); S.ready = true; S.mode = m.mode;
      setConn('on', 'Connected'); $('mic').disabled = false; clearError();
      $('mockBanner').hidden = !(m.stt === 'mock');
      status(m.resumed ? 'Reconnected. ' + t().ready : t().ready);
      break;
    case 'transcript':
      $('transcript').textContent = m.text; break;
    case 'update':
      renderUpdate(m); break;
    case 'audio':
      playChunk(m); break;
    case 'error':
      showError(m.message, m.retryable);
      status(t().ready);
      break;
  }
}

// ---------------- Rendering ----------------
function setField(id, text, missing) { const el = $(id); el.textContent = text; el.className = missing ? 'empty' : ''; }
function renderUpdate(m) {
  if (m.interrupted) return;
  S.lastUpdate = m;
  if (m.reply) { $('reply').textContent = m.reply; $('replyCard').hidden = false; }
  const f = m.form || {};
  setField('f_ben', f.beneficiary_id ? `${f.beneficiary_name} (${f.beneficiary_id})` : t().missing, !f.beneficiary_id);
  setField('f_vt', f.visit_label || t().missing, !f.visit_label);
  setField('f_con', f.concerns == null ? t().missing : (f.concern_labels.length ? f.concern_labels.join(', ') : t().none), f.concerns == null);
  setField('f_fu', f.follow_up_days ? f.follow_up_days + ' d' : t().notset, false);
  const cands = m.candidates || [];
  $('candidates').hidden = cands.length === 0;
  $('candList').innerHTML = '';
  cands.forEach(c => { const li = document.createElement('li'); li.textContent = `${c.id} · ${c.village}`; $('candList').appendChild(li); });
  const signs = (m.danger && m.danger.signs) || [];
  $('danger').hidden = signs.length === 0;
  if (signs.length) {
    $('dangerRef').textContent = m.danger.referral;
    $('dangerList').innerHTML = '';
    signs.forEach(s => { const li = document.createElement('li'); li.textContent = s.label; $('dangerList').appendChild(li); });
    $('dangerNote').textContent = signs.some(s => !s.verified) ? 'Rule not yet verified against an official guideline (demo).' : '';
  }
  $('confirmRow').hidden = m.state !== 'await_confirm';
  $('yesBtn').textContent = t().yes; $('noBtn').textContent = t().no;
  $('savedMsg').hidden = m.state !== 'saved';
  if (m.state === 'saved') $('savedMsg').textContent = t().saved + ' (#' + m.saved_record_id + ')';
  const fu = m.followups || [];
  $('fuCard').hidden = fu.length === 0;
  $('fuList').innerHTML = '';
  fu.forEach(x => { const li = document.createElement('li'); li.textContent = `${x.name || ''} ${x.beneficiary_id} – ${x.due_date}`; $('fuList').appendChild(li); });
}

// ---------------- Microphone ----------------
async function ensureCtx() {
  if (!S.ctx) S.ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (S.ctx.state === 'suspended') await S.ctx.resume();
  if (!S.workletOK) { await S.ctx.audioWorklet.addModule('audio-worklet.js'); S.workletOK = true; }
}
function b64(buf) {
  const u8 = new Uint8Array(buf); let s = '';
  for (let i = 0; i < u8.length; i += 0x2000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x2000));
  return btoa(s);
}
async function startRecording() {
  if (!S.ready) return;
  try {
    if (S.sources.length) { stopPlayback(); send({ type: 'interrupt' }); }
    await ensureCtx();
    S.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (e) {
    showError(e && e.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow it in the browser settings, or use "Type instead".'
      : 'Could not start the microphone. Use "Type instead" or try again.', false);
    return;
  }
  clearError();
  S.src = S.ctx.createMediaStreamSource(S.stream);
  S.node = new AudioWorkletNode(S.ctx, 'pcm16-resampler', { processorOptions: { targetRate: 16000, chunkMs: 100 } });
  S.node.port.onmessage = e => {
    const { pcm, rms } = e.data;
    $('mic').style.setProperty('--level', Math.min(1, rms * 6));
    if (S.sources.length) {                      // barge-in: user speaks over the reply
      S.loud = rms > 0.08 ? S.loud + 1 : 0;
      if (S.loud >= 3) { stopPlayback(); send({ type: 'interrupt' }); S.loud = 0; }
    }
    send({ type: 'audio', data: b64(pcm) });
  };
  S.src.connect(S.node);
  S.rec = true;
  $('mic').setAttribute('aria-pressed', 'true'); $('micLabel').textContent = t().stop; status(t().listening);
}
function stopRecording(finish = true) {
  S.rec = false;
  try { S.src && S.src.disconnect(); S.node && S.node.disconnect(); } catch { /* ignore */ }
  if (S.stream) S.stream.getTracks().forEach(tr => tr.stop());
  S.src = S.node = S.stream = null;
  $('mic').setAttribute('aria-pressed', 'false'); $('micLabel').textContent = t().tap; $('mic').style.setProperty('--level', 0);
  if (finish) { send({ type: 'end_utterance' }); status(t().thinking); }
}
$('mic').addEventListener('click', () => (S.rec ? stopRecording(true) : startRecording()));

// ---------------- Typed / button input (no STT) ----------------
function sendText(text) {
  if (!S.ready || !text) return;
  if (S.sources.length) { stopPlayback(); send({ type: 'interrupt' }); }
  send({ type: 'end_utterance', text }); status(t().thinking);
}
$('yesBtn').addEventListener('click', () => sendText(t().yes));
$('noBtn').addEventListener('click', () => sendText(t().no));
$('typedSend').addEventListener('click', () => { const v = $('typedText').value.trim(); $('typedText').value = ''; sendText(v); });
$('typedText').addEventListener('keydown', e => { if (e.key === 'Enter') $('typedSend').click(); });
$('retryBtn').addEventListener('click', () => { clearError(); if (!S.ready) connect(); });

// ---------------- Playback ----------------
async function playChunk(m) {
  if (m.final) { if (m.interrupted) stopPlayback(); return; }
  if (m.text && m.tts === 'mock' && 'speechSynthesis' in window) {      // demo voice in mock mode
    try { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(m.text); u.lang = m.lang === 'hi' ? 'hi-IN' : 'en-IN'; speechSynthesis.speak(u); } catch { /* optional */ }
  }
  try { await ensureCtx(); } catch { return; }
  const bytes = Uint8Array.from(atob(m.data), c => c.charCodeAt(0));
  const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1);
  const f32 = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;
  const buf = S.ctx.createBuffer(1, f32.length, m.sample_rate);
  buf.copyToChannel(f32, 0);
  const src = S.ctx.createBufferSource();
  src.buffer = buf; src.connect(S.ctx.destination);
  S.playT = Math.max(S.ctx.currentTime + 0.02, S.playT);
  src.start(S.playT); S.playT += buf.duration;
  S.sources.push(src);
  src.onended = () => { S.sources = S.sources.filter(x => x !== src); };
}
function stopPlayback() {
  S.sources.forEach(s => { try { s.stop(); } catch { /* already stopped */ } });
  S.sources = []; S.playT = 0;
  if ('speechSynthesis' in window) speechSynthesis.cancel();
}

// ---------------- Init ----------------
$('lang').value = S.lang;
document.documentElement.lang = S.lang;
$('lang').addEventListener('change', e => {
  S.lang = e.target.value; localStorage.setItem('vfr_lang', S.lang); document.documentElement.lang = S.lang;
  $('micLabel').textContent = t().tap;
  if (S.ready) send({ type: 'resume', session_id: S.sid, language: S.lang });
});
$('micLabel').textContent = t().tap;
window.addEventListener('online', () => { if (!S.ready && (!S.ws || S.ws.readyState > 1)) connect(); });
window.addEventListener('offline', () => setConn('off', 'Offline'));
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
connect();
