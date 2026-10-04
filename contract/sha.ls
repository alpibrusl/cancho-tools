edition 5;

module toolbox.sha;

// `toolbox.sha` -- SHA-256 and SHA-512, fed a chunk at a time.
//
// `docs/agent-toolbox.md` (lex-sys) §2.3 and L4: `std.crypto.sha256` copies
// its whole message, padded, into a 64 KiB arena, so any message longer
// than 65,527 bytes traps (`sha512`: 65,519). A precondition hash over a
// file of any size, which `write` and `hash` both need, cannot be built on
// that. This is the in-package port the design recommends (AGENTS.md §7:
// write it in your program first): the compression functions are copied
// unchanged from `std/crypto.ls`, and only the padding moves into a state
// that survives between calls. It is deleted when `std.crypto` grows an
// incremental interface.
//
// The state lives on the heap, not in a region, so nothing here is sized
// from data: a hasher is 8 words and one block, whatever it is fed.

pub res struct Hasher {
    // The eight running words.
    state: Box[[int]],
    // A partial block: `used` bytes of it are waiting for the rest.
    block: Box[[byte]],
    used: int,
    // Bytes fed so far, for the length field.
    total: int,
    // SHA-512 when true: 128-byte blocks, 80 rounds, 64-bit words.
    wide: bool,
}

static sha256_h0: [int] {
    let h = alloc_slice[static](8, 0);
    h[0] = 0x6a09e667;
    h[1] = 0xbb67ae85;
    h[2] = 0x3c6ef372;
    h[3] = 0xa54ff53a;
    h[4] = 0x510e527f;
    h[5] = 0x9b05688c;
    h[6] = 0x1f83d9ab;
    h[7] = 0x5be0cd19;
    return h;
}

static sha256_k: [int] {
    let k = alloc_slice[static](64, 0);
    k[0] = 0x428a2f98;
    k[1] = 0x71374491;
    k[2] = 0xb5c0fbcf;
    k[3] = 0xe9b5dba5;
    k[4] = 0x3956c25b;
    k[5] = 0x59f111f1;
    k[6] = 0x923f82a4;
    k[7] = 0xab1c5ed5;
    k[8] = 0xd807aa98;
    k[9] = 0x12835b01;
    k[10] = 0x243185be;
    k[11] = 0x550c7dc3;
    k[12] = 0x72be5d74;
    k[13] = 0x80deb1fe;
    k[14] = 0x9bdc06a7;
    k[15] = 0xc19bf174;
    k[16] = 0xe49b69c1;
    k[17] = 0xefbe4786;
    k[18] = 0x0fc19dc6;
    k[19] = 0x240ca1cc;
    k[20] = 0x2de92c6f;
    k[21] = 0x4a7484aa;
    k[22] = 0x5cb0a9dc;
    k[23] = 0x76f988da;
    k[24] = 0x983e5152;
    k[25] = 0xa831c66d;
    k[26] = 0xb00327c8;
    k[27] = 0xbf597fc7;
    k[28] = 0xc6e00bf3;
    k[29] = 0xd5a79147;
    k[30] = 0x06ca6351;
    k[31] = 0x14292967;
    k[32] = 0x27b70a85;
    k[33] = 0x2e1b2138;
    k[34] = 0x4d2c6dfc;
    k[35] = 0x53380d13;
    k[36] = 0x650a7354;
    k[37] = 0x766a0abb;
    k[38] = 0x81c2c92e;
    k[39] = 0x92722c85;
    k[40] = 0xa2bfe8a1;
    k[41] = 0xa81a664b;
    k[42] = 0xc24b8b70;
    k[43] = 0xc76c51a3;
    k[44] = 0xd192e819;
    k[45] = 0xd6990624;
    k[46] = 0xf40e3585;
    k[47] = 0x106aa070;
    k[48] = 0x19a4c116;
    k[49] = 0x1e376c08;
    k[50] = 0x2748774c;
    k[51] = 0x34b0bcb5;
    k[52] = 0x391c0cb3;
    k[53] = 0x4ed8aa4a;
    k[54] = 0x5b9cca4f;
    k[55] = 0x682e6ff3;
    k[56] = 0x748f82ee;
    k[57] = 0x78a5636f;
    k[58] = 0x84c87814;
    k[59] = 0x8cc70208;
    k[60] = 0x90befffa;
    k[61] = 0xa4506ceb;
    k[62] = 0xbef9a3f7;
    k[63] = 0xc67178f2;
    return k;
}

static sha512_h0: [int] {
    let h = alloc_slice[static](8, 0);
    h[0] = 0x6a09e667 << 32 | 0xf3bcc908;
    h[1] = 0xbb67ae85 << 32 | 0x84caa73b;
    h[2] = 0x3c6ef372 << 32 | 0xfe94f82b;
    h[3] = 0xa54ff53a << 32 | 0x5f1d36f1;
    h[4] = 0x510e527f << 32 | 0xade682d1;
    h[5] = 0x9b05688c << 32 | 0x2b3e6c1f;
    h[6] = 0x1f83d9ab << 32 | 0xfb41bd6b;
    h[7] = 0x5be0cd19 << 32 | 0x137e2179;
    return h;
}

static sha512_k: [int] {
    let k = alloc_slice[static](80, 0);
    k[0] = 0x428a2f98 << 32 | 0xd728ae22;
    k[1] = 0x71374491 << 32 | 0x23ef65cd;
    k[2] = 0xb5c0fbcf << 32 | 0xec4d3b2f;
    k[3] = 0xe9b5dba5 << 32 | 0x8189dbbc;
    k[4] = 0x3956c25b << 32 | 0xf348b538;
    k[5] = 0x59f111f1 << 32 | 0xb605d019;
    k[6] = 0x923f82a4 << 32 | 0xaf194f9b;
    k[7] = 0xab1c5ed5 << 32 | 0xda6d8118;
    k[8] = 0xd807aa98 << 32 | 0xa3030242;
    k[9] = 0x12835b01 << 32 | 0x45706fbe;
    k[10] = 0x243185be << 32 | 0x4ee4b28c;
    k[11] = 0x550c7dc3 << 32 | 0xd5ffb4e2;
    k[12] = 0x72be5d74 << 32 | 0xf27b896f;
    k[13] = 0x80deb1fe << 32 | 0x3b1696b1;
    k[14] = 0x9bdc06a7 << 32 | 0x25c71235;
    k[15] = 0xc19bf174 << 32 | 0xcf692694;
    k[16] = 0xe49b69c1 << 32 | 0x9ef14ad2;
    k[17] = 0xefbe4786 << 32 | 0x384f25e3;
    k[18] = 0x0fc19dc6 << 32 | 0x8b8cd5b5;
    k[19] = 0x240ca1cc << 32 | 0x77ac9c65;
    k[20] = 0x2de92c6f << 32 | 0x592b0275;
    k[21] = 0x4a7484aa << 32 | 0x6ea6e483;
    k[22] = 0x5cb0a9dc << 32 | 0xbd41fbd4;
    k[23] = 0x76f988da << 32 | 0x831153b5;
    k[24] = 0x983e5152 << 32 | 0xee66dfab;
    k[25] = 0xa831c66d << 32 | 0x2db43210;
    k[26] = 0xb00327c8 << 32 | 0x98fb213f;
    k[27] = 0xbf597fc7 << 32 | 0xbeef0ee4;
    k[28] = 0xc6e00bf3 << 32 | 0x3da88fc2;
    k[29] = 0xd5a79147 << 32 | 0x930aa725;
    k[30] = 0x06ca6351 << 32 | 0xe003826f;
    k[31] = 0x14292967 << 32 | 0x0a0e6e70;
    k[32] = 0x27b70a85 << 32 | 0x46d22ffc;
    k[33] = 0x2e1b2138 << 32 | 0x5c26c926;
    k[34] = 0x4d2c6dfc << 32 | 0x5ac42aed;
    k[35] = 0x53380d13 << 32 | 0x9d95b3df;
    k[36] = 0x650a7354 << 32 | 0x8baf63de;
    k[37] = 0x766a0abb << 32 | 0x3c77b2a8;
    k[38] = 0x81c2c92e << 32 | 0x47edaee6;
    k[39] = 0x92722c85 << 32 | 0x1482353b;
    k[40] = 0xa2bfe8a1 << 32 | 0x4cf10364;
    k[41] = 0xa81a664b << 32 | 0xbc423001;
    k[42] = 0xc24b8b70 << 32 | 0xd0f89791;
    k[43] = 0xc76c51a3 << 32 | 0x0654be30;
    k[44] = 0xd192e819 << 32 | 0xd6ef5218;
    k[45] = 0xd6990624 << 32 | 0x5565a910;
    k[46] = 0xf40e3585 << 32 | 0x5771202a;
    k[47] = 0x106aa070 << 32 | 0x32bbd1b8;
    k[48] = 0x19a4c116 << 32 | 0xb8d2d0c8;
    k[49] = 0x1e376c08 << 32 | 0x5141ab53;
    k[50] = 0x2748774c << 32 | 0xdf8eeb99;
    k[51] = 0x34b0bcb5 << 32 | 0xe19b48a8;
    k[52] = 0x391c0cb3 << 32 | 0xc5c95a63;
    k[53] = 0x4ed8aa4a << 32 | 0xe3418acb;
    k[54] = 0x5b9cca4f << 32 | 0x7763e373;
    k[55] = 0x682e6ff3 << 32 | 0xd6b2b8a3;
    k[56] = 0x748f82ee << 32 | 0x5defb2fc;
    k[57] = 0x78a5636f << 32 | 0x43172f60;
    k[58] = 0x84c87814 << 32 | 0xa1f0ab72;
    k[59] = 0x8cc70208 << 32 | 0x1a6439ec;
    k[60] = 0x90befffa << 32 | 0x23631e28;
    k[61] = 0xa4506ceb << 32 | 0xde82bde9;
    k[62] = 0xbef9a3f7 << 32 | 0xb2c67915;
    k[63] = 0xc67178f2 << 32 | 0xe372532b;
    k[64] = 0xca273ece << 32 | 0xea26619c;
    k[65] = 0xd186b8c7 << 32 | 0x21c0c207;
    k[66] = 0xeada7dd6 << 32 | 0xcde0eb1e;
    k[67] = 0xf57d4f7f << 32 | 0xee6ed178;
    k[68] = 0x06f067aa << 32 | 0x72176fba;
    k[69] = 0x0a637dc5 << 32 | 0xa2c898a6;
    k[70] = 0x113f9804 << 32 | 0xbef90dae;
    k[71] = 0x1b710b35 << 32 | 0x131c471b;
    k[72] = 0x28db77f5 << 32 | 0x23047d84;
    k[73] = 0x32caab7b << 32 | 0x40c72493;
    k[74] = 0x3c9ebe0a << 32 | 0x15c9bebc;
    k[75] = 0x431d67c4 << 32 | 0x9c100d4c;
    k[76] = 0x4cc5d4be << 32 | 0xcb3e42b6;
    k[77] = 0x597f299c << 32 | 0xfc657e2a;
    k[78] = 0x5fcb6fab << 32 | 0x3ad6faec;
    k[79] = 0x6c44198c << 32 | 0x4a475817;
    return k;
}

fn mask32(x: int) -> [] int {
    return x & 0xffffffff;
}

fn rotr32(x: int, n: int) -> [] int {
    return mask32(x >> n | x << 32 - n);
}

fn not32(x: int) -> [] int {
    return 0xffffffff - x;
}

fn compress[&st, &b](state: &!st [int], block: &b [byte]) -> [] int {
    region a {
        let w = alloc_slice[a](64, 0);

        var t = 0;
        while t < 16 {
            let i = t * 4;
            w[t] = int_of(block[i]) << 24 | int_of(block[i + 1]) << 16 | int_of(block[i + 2]) << 8 | int_of(block[i + 3]);
            t = t + 1;
        }

        t = 16;
        while t < 64 {
            let s0 = rotr32(w[t - 15], 7) ^ rotr32(w[t - 15], 18) ^ w[t - 15] >> 3;
            let s1 = rotr32(w[t - 2], 17) ^ rotr32(w[t - 2], 19) ^ w[t - 2] >> 10;
            w[t] = mask32(w[t - 16] + s0 + w[t - 7] + s1);
            t = t + 1;
        }

        var wa = state[0];
        var wb = state[1];
        var wc = state[2];
        var wd = state[3];
        var we = state[4];
        var wf = state[5];
        var wg = state[6];
        var wh = state[7];

        t = 0;
        while t < 64 {
            let s1 = rotr32(we, 6) ^ rotr32(we, 11) ^ rotr32(we, 25);
            let ch = we & wf ^ not32(we) & wg;
            let temp1 = mask32(wh + s1 + ch + sha256_k[t] + w[t]);
            let s0 = rotr32(wa, 2) ^ rotr32(wa, 13) ^ rotr32(wa, 22);
            let maj = wa & wb ^ wa & wc ^ wb & wc;
            let temp2 = mask32(s0 + maj);

            wh = wg;
            wg = wf;
            wf = we;
            we = mask32(wd + temp1);
            wd = wc;
            wc = wb;
            wb = wa;
            wa = mask32(temp1 + temp2);

            t = t + 1;
        }

        state[0] = mask32(state[0] + wa);
        state[1] = mask32(state[1] + wb);
        state[2] = mask32(state[2] + wc);
        state[3] = mask32(state[3] + wd);
        state[4] = mask32(state[4] + we);
        state[5] = mask32(state[5] + wf);
        state[6] = mask32(state[6] + wg);
        state[7] = mask32(state[7] + wh);
    }
    return 0;
}

fn low_mask64(k: int) -> [] int {
    if k == 63 {
        return 0x7fffffffffffffff;
    }
    return (1 << k) - 1;
}

fn lshr64(x: int, n: int) -> [] int {
    return x >> n & low_mask64(64 - n);
}

fn rotr64(x: int, n: int) -> [] int {
    return lshr64(x, n) | x << 64 - n;
}

fn compress512[&st, &b](state: &!st [int], block: &b [byte]) -> [] int {
    region a {
        let w = alloc_slice[a](80, 0);

        var t = 0;
        while t < 16 {
            let i = t * 8;
            w[t] = int_of(block[i]) << 56 | int_of(block[i + 1]) << 48 | int_of(block[i + 2]) << 40 | int_of(block[i + 3]) << 32 | int_of(block[i + 4]) << 24 | int_of(block[i + 5]) << 16 | int_of(block[i + 6]) << 8 | int_of(block[i + 7]);
            t = t + 1;
        }

        t = 16;
        while t < 80 {
            let s0 = rotr64(w[t - 15], 1) ^ rotr64(w[t - 15], 8) ^ lshr64(w[t - 15], 7);
            let s1 = rotr64(w[t - 2], 19) ^ rotr64(w[t - 2], 61) ^ lshr64(w[t - 2], 6);
            w[t] = wrapping_add(wrapping_add(w[t - 16], s0), wrapping_add(w[t - 7], s1));
            t = t + 1;
        }

        var wa = state[0];
        var wb = state[1];
        var wc = state[2];
        var wd = state[3];
        var we = state[4];
        var wf = state[5];
        var wg = state[6];
        var wh = state[7];

        t = 0;
        while t < 80 {
            let s1 = rotr64(we, 14) ^ rotr64(we, 18) ^ rotr64(we, 41);
            let ch = we & wf ^ ~we & wg;
            let temp1 = wrapping_add(wrapping_add(wrapping_add(wh, s1), ch), wrapping_add(sha512_k[t], w[t]));
            let s0 = rotr64(wa, 28) ^ rotr64(wa, 34) ^ rotr64(wa, 39);
            let maj = wa & wb ^ wa & wc ^ wb & wc;
            let temp2 = wrapping_add(s0, maj);

            wh = wg;
            wg = wf;
            wf = we;
            we = wrapping_add(wd, temp1);
            wd = wc;
            wc = wb;
            wb = wa;
            wa = wrapping_add(temp1, temp2);

            t = t + 1;
        }

        state[0] = wrapping_add(state[0], wa);
        state[1] = wrapping_add(state[1], wb);
        state[2] = wrapping_add(state[2], wc);
        state[3] = wrapping_add(state[3], wd);
        state[4] = wrapping_add(state[4], we);
        state[5] = wrapping_add(state[5], wf);
        state[6] = wrapping_add(state[6], wg);
        state[7] = wrapping_add(state[7], wh);
    }
    return 0;
}

fn start[&h](heap: &!h Heap, wide: bool) -> [heap] Hasher {
    let state = box_slice(heap, 8, 0);
    var size = 64;
    if wide {
        size = 128;
    }
    borrow mut state as &!sw in {
        let s = contents(sw);
        var i = 0;
        while i < 8 {
            if wide {
                s[i] = sha512_h0[i];
            } else {
                s[i] = sha256_h0[i];
            }
            i = i + 1;
        }
    }
    return Hasher { state: state, block: box_slice(heap, size, byte_of(0)), used: 0, total: 0, wide: wide };
}

pub fn sha256[&h](heap: &!h Heap) -> [heap] Hasher {
    return start(heap, false);
}

pub fn sha512[&h](heap: &!h Heap) -> [heap] Hasher {
    return start(heap, true);
}

// The digest's length in bytes: 32 or 64.
pub fn digest_size[&s](hasher: &s Hasher) -> [] int {
    if hasher.wide {
        return 64;
    }
    return 32;
}

fn fold[&st, &b](state: &!st [int], block: &b [byte], wide: bool) -> [] int {
    if wide {
        return compress512(state, block);
    }
    return compress(state, block);
}

// Feed `data`. Whole blocks are compressed straight from `data`; only a
// partial block at either end is copied.
pub fn update[&d](hasher: Hasher, data: &d [byte]) -> [] Hasher {
    let Hasher { state, block, used, total, wide } = hasher;
    var size = 64;
    if wide {
        size = 128;
    }
    var have = used;
    var at = 0;
    borrow mut state as &!sw in {
        borrow mut block as &!bw in {
            let s = contents(sw);
            let b = contents(bw);
            // Top up a partial block first.
            while have > 0 && at < len(data) {
                b[have] = data[at];
                have = have + 1;
                at = at + 1;
                if have == size {
                    fold(s, b, wide);
                    have = 0;
                }
            }
            // Whole blocks, in place.
            while len(data) - at >= size {
                fold(s, data[at..at + size], wide);
                at = at + size;
            }
            // What is left waits for the next call.
            while at < len(data) {
                b[have] = data[at];
                have = have + 1;
                at = at + 1;
            }
        }
    }
    return Hasher { state: state, block: block, used: have, total: total + len(data), wide: wide };
}

// Pad, fold the last block(s), write the digest into `digest` (at least
// `digest_size` bytes) and free the hasher. Answers the digest's length.
pub fn finish[&h, &o](heap: &!h Heap, hasher: Hasher, digest: &!o [byte]) -> [heap] int {
    let Hasher { state, block, used, total, wide } = hasher;
    var size = 64;
    var field = 8;
    var words = 8;
    var word = 4;
    if wide {
        size = 128;
        field = 16;
        word = 8;
    }
    borrow mut state as &!sw in {
        borrow mut block as &!bw in {
            let s = contents(sw);
            let b = contents(bw);
            var have = used;
            b[have] = byte_of(0x80);
            have = have + 1;
            // No room for the length field: zero the rest, fold, start a
            // fresh block.
            if have > size - field {
                while have < size {
                    b[have] = byte_of(0);
                    have = have + 1;
                }
                fold(s, b, wide);
                have = 0;
            }
            while have < size {
                b[have] = byte_of(0);
                have = have + 1;
            }
            // The bit length, big-endian, in the last eight bytes. SHA-512's
            // field is sixteen, and its top eight stay zero: `total * 8` is
            // a checked multiply, so a length that needed them traps here
            // rather than wrapping (`docs/sha512.md` §3, lex-sys).
            let bits = total * 8;
            var k = 0;
            while k < 8 {
                b[size - 8 + k] = byte_of(bits >> (7 - k) * 8 & 0xff);
                k = k + 1;
            }
            fold(s, b, wide);
            var i = 0;
            while i < words {
                var j = 0;
                while j < word {
                    digest[i * word + j] = byte_of(s[i] >> (word - 1 - j) * 8 & 0xff);
                    j = j + 1;
                }
                i = i + 1;
            }
        }
    }
    unbox_slice(heap, state);
    unbox_slice(heap, block);
    return words * word;
}

// End a hasher without a digest, on a path that gave up.
pub fn discard[&h](heap: &!h Heap, hasher: Hasher) -> [heap] int {
    let Hasher { state, block, used, total, wide } = hasher;
    unbox_slice(heap, state);
    unbox_slice(heap, block);
    return total;
}

fn hex_digit(v: int) -> [] int {
    let digits = "0123456789abcdef";
    return int_of(digits[v]);
}

// `digest[0..n]` as lowercase hex into `out` (at least `2 * n` bytes).
pub fn hex_into[&d, &o](digest: &d [byte], n: int, out: &!o [byte]) -> [] int {
    var i = 0;
    while i < n {
        let v = int_of(digest[i]);
        out[2 * i] = byte_of(hex_digit(v >> 4));
        out[2 * i + 1] = byte_of(hex_digit(v & 15));
        i = i + 1;
    }
    return 2 * n;
}

// Whether `text` is exactly `digits` lowercase hex digits. Uppercase is
// refused rather than folded: a digest is compared byte for byte, and one
// spelling is what keeps two equal digests equal.
pub fn is_hex[&t](text: &t [byte], digits: int) -> [] bool {
    if len(text) != digits {
        return false;
    }
    var i = 0;
    while i < len(text) {
        let c = int_of(text[i]);
        if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
            return false;
        }
        i = i + 1;
    }
    return true;
}
