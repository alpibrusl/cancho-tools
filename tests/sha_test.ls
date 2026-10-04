edition 5;

// The incremental hasher against `std.crypto`, which is right below its
// 64 KiB ceiling: every length from 0 to 300 (each padding case, both
// block sizes, twice over) fed in chunks of 1, 7 and 64 bytes, compared
// digest for digest. Lengths past the ceiling are `hash`'s differential
// test against `sha256sum` (tests/conformance).

import std.crypto;
import std.test;
import toolbox.sha;

fn message[&m](m: &!m [byte]) -> [] int {
    var i = 0;
    while i < len(m) {
        m[i] = byte_of(i * 31 + 7 & 255);
        i = i + 1;
    }
    return 0;
}

fn incremental[&h, &m, &o](heap: &!h Heap, m: &m [byte], chunk: int, wide: bool, out: &!o [byte]) -> [heap] int {
    var s = sha.sha256(heap);
    if wide {
        sha.discard(heap, s);
        s = sha.sha512(heap);
    }
    var at = 0;
    while at < len(m) {
        var end = at + chunk;
        if end > len(m) {
            end = len(m);
        }
        s = sha.update(s, m[at..end]);
        at = end;
    }
    return sha.finish(heap, s, out);
}

fn agree[&h](heap: &!h Heap, wide: bool) -> [heap] int {
    var n = 0;
    while n <= 300 {
        region a {
            let m = alloc_slice[a](n, byte_of(0));
            message(m);
            let want = alloc_slice[a](64, byte_of(0));
            if wide {
                crypto.sha512(m, want);
            } else {
                crypto.sha256(m, want);
            }
            var c = 0;
            while c < 3 {
                var chunk = 1;
                if c == 1 {
                    chunk = 7;
                } else if c == 2 {
                    chunk = 64;
                }
                let got = alloc_slice[a](64, byte_of(0));
                let size = incremental(heap, m, chunk, wide, got);
                var i = 0;
                while i < size {
                    test.assert_eq(int_of(got[i]), int_of(want[i]));
                    i = i + 1;
                }
                c = c + 1;
            }
        }
        n = n + 1;
    }
    return 0;
}

pub fn test_sha256_agrees_with_std_in_any_chunking[&h](heap: &!h Heap) -> [heap] int {
    return agree(heap, false);
}

pub fn test_sha512_agrees_with_std_in_any_chunking[&h](heap: &!h Heap) -> [heap] int {
    return agree(heap, true);
}

pub fn test_sha256_of_abc_is_the_fips_vector[&h](heap: &!h Heap) -> [heap] int {
    var s = sha.sha256(heap);
    s = sha.update(s, "abc");
    region a {
        let d = alloc_slice[a](32, byte_of(0));
        let hex = alloc_slice[a](64, byte_of(0));
        sha.finish(heap, s, d);
        sha.hex_into(d, 32, hex);
        let want = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
        var i = 0;
        while i < 64 {
            test.assert_eq(int_of(hex[i]), int_of(want[i]));
            i = i + 1;
        }
    }
    return 0;
}

pub fn test_is_hex_wants_exact_lowercase() -> [] int {
    test.assert(sha.is_hex("00ff", 4));
    test.assert(!sha.is_hex("00FF", 4));
    test.assert(!sha.is_hex("00f", 4));
    test.assert(!sha.is_hex("00fg", 4));
    return 0;
}
