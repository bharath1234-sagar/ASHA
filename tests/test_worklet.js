// Run: node tests/test_worklet.js   (needs Node 16+; checks the AudioWorklet resampler outside a browser)
const fs = require('fs');
const path = require('path');
let Proc;
function load(rate) {
  global.sampleRate = rate;
  global.AudioWorkletProcessor = class { constructor() { this.port = { postMessage: (m) => this.posted.push(m) }; this.posted = []; } };
  global.registerProcessor = (_n, cls) => { Proc = cls; };
  eval(fs.readFileSync(path.join(__dirname, '..', 'frontend', 'audio-worklet.js'), 'utf8'));
}
let failed = 0;
function check(name, ok, info = '') { console.log((ok ? 'PASS ' : 'FAIL ') + name + ' ' + info); if (!ok) failed++; }

for (const rate of [48000, 44100, 16000]) {
  load(rate);
  const p = new Proc({ processorOptions: { targetRate: 16000, chunkMs: 100 } });
  p.posted = [];
  const seconds = 1, total = rate * seconds, f = 440;
  for (let off = 0; off < total; off += 128) {
    const block = new Float32Array(128);
    for (let i = 0; i < 128; i++) block[i] = 0.5 * Math.sin(2 * Math.PI * f * (off + i) / rate);
    p.process([[block]]);
  }
  const chunks = p.posted;
  check(`${rate} Hz: ~10 chunks/s`, chunks.length >= 9 && chunks.length <= 10, `(got ${chunks.length})`);
  check(`${rate} Hz: chunk is 1600 samples = 3200 bytes PCM16`, chunks.every(c => c.pcm.byteLength === 3200));
  const all = chunks.flatMap(c => Array.from(new Int16Array(c.pcm)));
  const peak = Math.max(...all.map(Math.abs)) / 32768;
  let zc = 0; for (let i = 1; i < all.length; i++) if ((all[i - 1] < 0) !== (all[i] < 0)) zc++;
  const hz = zc / 2 / (all.length / 16000);
  check(`${rate} Hz: 440 Hz tone preserved after resample`, Math.abs(hz - 440) < 15, `(measured ${hz.toFixed(0)} Hz)`);
  check(`${rate} Hz: amplitude sane (0.4-0.55 of full scale)`, peak > 0.4 && peak < 0.55, `(peak ${peak.toFixed(2)})`);
}
process.exit(failed ? 1 : 0);
