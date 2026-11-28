/* Incremental SHA-256 also works on private HTTP origins without SubtleCrypto. */
const K = new Uint32Array([
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
]);
const rotate = (value, bits) => value >>> bits | value << (32 - bits);

export class Sha256 {
  constructor() {
    this.state = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
    this.block = new Uint8Array(64); this.words = new Uint32Array(64);
    this.length = 0; this.used = 0; this.finished = false;
  }
  compress() {
    const w = this.words, view = new DataView(this.block.buffer);
    for (let n = 0; n < 16; n++) w[n] = view.getUint32(n * 4);
    for (let n = 16; n < 64; n++) {
      const x = w[n-15], y = w[n-2];
      w[n] = w[n-16] + (rotate(x,7) ^ rotate(x,18) ^ x >>> 3) + w[n-7] +
        (rotate(y,17) ^ rotate(y,19) ^ y >>> 10);
    }
    let [a,b,c,d,e,f,g,h] = this.state;
    for (let n = 0; n < 64; n++) {
      const t = h + (rotate(e,6) ^ rotate(e,11) ^ rotate(e,25)) + ((e & f) ^ (~e & g)) + K[n] + w[n];
      const s = (rotate(a,2) ^ rotate(a,13) ^ rotate(a,22)) + ((a & b) ^ (a & c) ^ (b & c));
      h=g; g=f; f=e; e=(d+t)>>>0; d=c; c=b; b=a; a=(t+s)>>>0;
    }
    for (const [n, value] of [a,b,c,d,e,f,g,h].entries()) this.state[n] += value;
  }
  update(bytes) {
    if (this.finished || !(bytes instanceof Uint8Array)) throw new Error('Invalid SHA-256 input state');
    this.length += bytes.length;
    if (!Number.isSafeInteger(this.length)) throw new Error('SHA-256 input exceeds its byte budget');
    let offset = 0;
    while (offset < bytes.length) {
      const size = Math.min(64 - this.used, bytes.length - offset);
      this.block.set(bytes.subarray(offset, offset + size), this.used);
      this.used += size; offset += size;
      if (this.used === 64) { this.compress(); this.used = 0; }
    }
    return this;
  }
  hex() {
    if (this.finished) throw new Error('SHA-256 digest already finalized');
    const bytes = this.length;
    this.update(new Uint8Array([128]));
    if (this.used > 56) {
      this.block.fill(0, this.used); this.compress(); this.used = 0;
    }
    this.block.fill(0, this.used, 56);
    const view = new DataView(this.block.buffer);
    view.setUint32(56, Math.floor(bytes / 0x20000000));
    view.setUint32(60, (bytes * 8) >>> 0);
    this.compress(); this.finished = true;
    return [...this.state].map(word => word.toString(16).padStart(8, '0')).join('');
  }
}
export const sha256 = bytes => new Sha256().update(bytes).hex();
