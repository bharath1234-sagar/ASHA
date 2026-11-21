// Runs on the audio thread. Converts the microphone stream (any sample rate, e.g. 48000/44100)
// to mono PCM16 @ 16 kHz in ~100 ms chunks. Resampling = box-filter average over each output
// sample's input window (a simple low-pass), so 48k->16k does not alias badly.
class PCM16Resampler extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.target = o.targetRate || 16000;
    this.chunk = Math.round(this.target * (o.chunkMs || 100) / 1000);   // 1600 samples = 3200 bytes
    this.ratio = sampleRate / this.target;                              // `sampleRate` is the AudioContext rate
    this.pos = 0;
    this.tail = new Float32Array(0);
    this.out = new Int16Array(this.chunk);
    this.n = 0;
    this.sumsq = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    let ch = input[0];
    if (input.length > 1) {                       // downmix to mono
      const mono = new Float32Array(ch.length);
      for (let c = 0; c < input.length; c++) for (let i = 0; i < ch.length; i++) mono[i] += input[c][i] / input.length;
      ch = mono;
    }
    const buf = new Float32Array(this.tail.length + ch.length);
    buf.set(this.tail); buf.set(ch, this.tail.length);
    let pos = this.pos;
    while (pos + this.ratio <= buf.length) {
      const end = pos + this.ratio;
      let acc = 0;
      for (let i = Math.floor(pos); i < end; i++) {
        acc += buf[i] * (Math.min(end, i + 1) - Math.max(pos, i));
      }
      let s = Math.max(-1, Math.min(1, acc / this.ratio));
      this.sumsq += s * s;
      this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.chunk) {
        const rms = Math.sqrt(this.sumsq / this.chunk);
        this.port.postMessage({ pcm: this.out.buffer, rms }, [this.out.buffer]);
        this.out = new Int16Array(this.chunk); this.n = 0; this.sumsq = 0;
      }
      pos = end;
    }
    const keep = Math.floor(pos);
    this.tail = buf.slice(keep);
    this.pos = pos - keep;
    return true;
  }
}
registerProcessor('pcm16-resampler', PCM16Resampler);
