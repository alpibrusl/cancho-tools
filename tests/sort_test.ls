edition 5;

import std.buffer;
import std.map;
import std.test;
import toolbox.sort;

// A comparator over a map the sort never sees the type of: counts, larger first.
fn more_frequent[&m](m: &m map.Map[int], a: int, b: int) -> [] bool {
    return map.value_at(m, a) > map.value_at(m, b);
}

// A comparator over a table: the modulus is in its first slot, so a parameter travels with the keys.
fn by_remainder[&t](table: &t [int], a: int, b: int) -> [] bool {
    return a % table[0] < b % table[0];
}

// The numbers 0..n scrambled by a multiplier that is coprime with n.
fn scrambled[&k](keys: &!k [int], n: int, multiplier: int, modulus: int) -> [] int {
    var i = 0;
    while i < n {
        keys[i] = (i * multiplier + 7) % modulus;
        i = i + 1;
    }
    return 0;
}

// What the six keys 3 1 2 1 3 2 sort to, in the order of `order`.
fn six[&h](heap: &!h Heap, descending: bool, n: int) -> [heap] int {
    let keys = box_slice(heap, 6, 0);
    let order = box_slice(heap, 6, 0);
    let spare = box_slice(heap, 6, 0);
    var got = 0;
    borrow mut keys as &!kw in {
        borrow mut order as &!ow in {
            borrow mut spare as &!sw in {
                let k = contents(kw);
                k[0] = 3;
                k[1] = 1;
                k[2] = 2;
                k[3] = 1;
                k[4] = 3;
                k[5] = 2;
                let o = contents(ow);
                sort.identity(o, 6);
                sort.by_keys(k, o, contents(sw), n, descending);
                var i = 0;
                while i < 6 {
                    got = got * 10 + o[i];
                    i = i + 1;
                }
            }
        }
    }
    unbox_slice(heap, keys);
    unbox_slice(heap, order);
    unbox_slice(heap, spare);
    return got;
}

pub fn test_keys_ascending_is_stable[&h](heap: &!h Heap) -> [heap] int {
    // 1 3 2 5 0 4: the equal keys keep their order (1 before 3, 2 before 5, 0 before 4).
    test.assert_eq(six(heap, false, 6), 132504);
    return 0;
}

pub fn test_keys_descending_is_stable[&h](heap: &!h Heap) -> [heap] int {
    // 0 4 2 5 1 3: larger first, equal keys still in their order.
    test.assert_eq(six(heap, true, 6), 42513);
    return 0;
}

pub fn test_only_the_first_n_are_sorted[&h](heap: &!h Heap) -> [heap] int {
    test.assert_eq(six(heap, false, 3), 120345);
    test.assert_eq(six(heap, false, 0), 12345);
    test.assert_eq(six(heap, false, 1), 12345);
    test.assert_eq(six(heap, false, 0 - 4), 12345);
    return 0;
}

pub fn test_n_past_the_arrays_is_clamped[&h](heap: &!h Heap) -> [heap] int {
    test.assert_eq(six(heap, false, 600), 132504);
    return 0;
}

pub fn test_a_comparator_over_a_map[&h](heap: &!h Heap) -> [heap] int {
    var m = map.empty(heap, 16, 0, 5);
    m = map.put(heap, m, "a", 2);
    m = map.put(heap, m, "b", 9);
    m = map.put(heap, m, "c", 2);
    m = map.put(heap, m, "d", 5);
    let order = box_slice(heap, 4, 0);
    let spare = box_slice(heap, 4, 0);
    var got = 0;
    borrow mut order as &!ow in {
        borrow mut spare as &!sw in {
            let o = contents(ow);
            sort.identity(o, 4);
            borrow m as &mr in {
                sort.by_map(mr, o, contents(sw), 4, more_frequent);
            }
            var i = 0;
            while i < 4 {
                got = got * 10 + o[i];
                i = i + 1;
            }
        }
    }
    // b(9) d(5) then a, c (2 and 2, in the order they were put).
    test.assert_eq(got, 1302);
    unbox_slice(heap, order);
    unbox_slice(heap, spare);
    map.drop(heap, m);
    return 0;
}

pub fn test_a_comparator_over_a_table[&h](heap: &!h Heap) -> [heap] int {
    let table = box_slice(heap, 1, 3);
    let order = box_slice(heap, 7, 0);
    let spare = box_slice(heap, 7, 0);
    var got = 0;
    borrow mut order as &!ow in {
        borrow mut spare as &!sw in {
            let o = contents(ow);
            sort.identity(o, 7);
            borrow table as &tr in {
                sort.by(contents(tr), o, contents(sw), 7, by_remainder);
            }
            var i = 0;
            while i < 7 {
                got = got * 10 + o[i];
                i = i + 1;
            }
        }
    }
    // Remainder 0: 0 3 6; remainder 1: 1 4; remainder 2: 2 5.
    test.assert_eq(got, 361425);
    unbox_slice(heap, table);
    unbox_slice(heap, order);
    unbox_slice(heap, spare);
    return 0;
}

// A thousand scrambled keys: a permutation, sorted, and stable.
pub fn test_a_large_sort_is_a_stable_permutation[&h](heap: &!h Heap) -> [heap] int {
    let n = 1000;
    let keys = box_slice(heap, n, 0);
    let order = box_slice(heap, n, 0);
    let spare = box_slice(heap, n, 0);
    var sorted = true;
    var stable = true;
    var sum = 0;
    borrow mut keys as &!kw in {
        borrow mut order as &!ow in {
            borrow mut spare as &!sw in {
                let k = contents(kw);
                scrambled(k, n, 37, 50);
                let o = contents(ow);
                sort.identity(o, n);
                sort.by_keys(k, o, contents(sw), n, false);
                var i = 0;
                while i < n {
                    sum = sum + o[i];
                    if i > 0 {
                        if k[o[i - 1]] > k[o[i]] {
                            sorted = false;
                        }
                        if k[o[i - 1]] == k[o[i]] && o[i - 1] > o[i] {
                            stable = false;
                        }
                    }
                    i = i + 1;
                }
            }
        }
    }
    test.assert(sorted);
    test.assert(stable);
    test.assert_eq(sum, 499500);
    unbox_slice(heap, keys);
    unbox_slice(heap, order);
    unbox_slice(heap, spare);
    return 0;
}

// An index that is not in `keys` is key 0: no trap.
pub fn test_an_index_outside_the_keys_is_key_zero[&h](heap: &!h Heap) -> [heap] int {
    let keys = box_slice(heap, 2, 5);
    let order = box_slice(heap, 3, 0);
    let spare = box_slice(heap, 3, 0);
    var got = 0;
    borrow mut keys as &!kw in {
        borrow mut order as &!ow in {
            borrow mut spare as &!sw in {
                let o = contents(ow);
                o[0] = 1;
                o[1] = 99;
                o[2] = 0 - 3;
                sort.by_keys(contents(kw), o, contents(sw), 3, false);
                got = o[0] * 10000 + o[1] * 10 + o[2];
            }
        }
    }
    // 99 and -3 are key 0, so first, in their order; 1 has key 5: 990000 - 30 + 1.
    test.assert_eq(got, 989971);
    unbox_slice(heap, keys);
    unbox_slice(heap, order);
    unbox_slice(heap, spare);
    return 0;
}

// `identity` writes the first n and nothing after.
pub fn test_identity_writes_only_the_first_n[&h](heap: &!h Heap) -> [heap] int {
    let order = box_slice(heap, 6, 9);
    borrow mut order as &!ow in {
        let o = contents(ow);
        sort.identity(o, 3);
        test.assert_eq(o[0] + o[1] + o[2], 3);
        test.assert_eq(o[3], 9);
        sort.identity(o, 100);
        test.assert_eq(o[5], 5);
    }
    unbox_slice(heap, order);
    return 0;
}

// `n` past the shorter of the two arrays sorts what fits in both, whichever is shorter.
pub fn test_n_is_clamped_to_the_shorter_array[&h](heap: &!h Heap) -> [heap] int {
    let keys = box_slice(heap, 20, 0);
    let long = box_slice(heap, 20, 0);
    let short = box_slice(heap, 4, 0);
    borrow mut keys as &!kw in {
        borrow mut long as &!lw in {
            borrow mut short as &!tw in {
                let k = contents(kw);
                scrambled(k, 20, 7, 5);
                let a = contents(lw);
                let b = contents(tw);
                sort.identity(a, 20);
                sort.by_keys(k, a, b, 1000, false);
                // Only the first 4 were sorted (stably, by their keys); the rest is as it was.
                var i = 0;
                while i < 20 {
                    if i >= 4 {
                        test.assert_eq(a[i], i);
                    }
                    if i > 0 && i < 4 {
                        test.assert(k[a[i - 1]] <= k[a[i]]);
                    }
                    i = i + 1;
                }
                sort.identity(b, 4);
                sort.identity(a, 20);
                sort.by_keys(k, b, a, 1000, false);
                i = 0;
                while i < 20 {
                    if i >= 4 {
                        test.assert_eq(a[i], i);
                    }
                    i = i + 1;
                }
            }
        }
    }
    unbox_slice(heap, keys);
    unbox_slice(heap, long);
    unbox_slice(heap, short);
    return 0;
}

// Every length from 1 to 40, so every shape of last run: sorted, stable, a permutation.
pub fn test_every_small_length_sorts_by_keys[&h](heap: &!h Heap) -> [heap] int {
    var n = 1;
    while n <= 40 {
        let keys = box_slice(heap, n, 0);
        let order = box_slice(heap, n, 0);
        let spare = box_slice(heap, n, 0);
        borrow mut keys as &!kw in {
            borrow mut order as &!ow in {
                borrow mut spare as &!sw in {
                    let k = contents(kw);
                    scrambled(k, n, 13, 4);
                    let o = contents(ow);
                    sort.identity(o, n);
                    sort.by_keys(k, o, contents(sw), n, n % 2 == 0);
                    var sum = 0;
                    var i = 0;
                    while i < n {
                        sum = sum + o[i];
                        if i > 0 {
                            if n % 2 == 0 {
                                test.assert(k[o[i - 1]] >= k[o[i]]);
                            } else {
                                test.assert(k[o[i - 1]] <= k[o[i]]);
                            }
                            if k[o[i - 1]] == k[o[i]] {
                                test.assert(o[i - 1] < o[i]);
                            }
                        }
                        i = i + 1;
                    }
                    test.assert_eq(sum, n * (n - 1) / 2);
                }
            }
        }
        unbox_slice(heap, keys);
        unbox_slice(heap, order);
        unbox_slice(heap, spare);
        n = n + 1;
    }
    return 0;
}

// The same over a map, for every length from 1 to 40 of a map of 40 entries (the first n entries are sorted).
pub fn test_every_small_length_sorts_by_map[&h](heap: &!h Heap) -> [heap] int {
    var m = map.empty(heap, 64, 0, 3);
    var e = 0;
    while e < 40 {
        var key = buffer.empty(heap, 8);
        key = buffer.push_nat(heap, key, e);
        borrow key as &kr in {
            m = map.put(heap, m, buffer.bytes(kr), (e * 13 + 7) % 4);
        }
        buffer.drop(heap, key);
        e = e + 1;
    }
    // `n` past the shorter of the two arrays, either one.
    let wide = box_slice(heap, 40, 0);
    let narrow = box_slice(heap, 5, 0);
    borrow mut wide as &!ww in {
        borrow mut narrow as &!nw in {
            let a = contents(ww);
            let b = contents(nw);
            sort.identity(a, 40);
            sort.identity(b, 5);
            borrow m as &mr in {
                sort.by_map(mr, a, b, 1000, more_frequent);
                var i = 5;
                while i < 40 {
                    test.assert_eq(a[i], i);
                    i = i + 1;
                }
                sort.identity(a, 40);
                sort.by_map(mr, b, a, 1000, more_frequent);
                test.assert_eq(a[5], 5);
                test.assert_eq(a[39], 39);
            }
        }
    }
    unbox_slice(heap, wide);
    unbox_slice(heap, narrow);
    var n = 1;
    while n <= 40 {
        let order = box_slice(heap, 40, 0);
        let spare = box_slice(heap, 40, 0);
        borrow mut order as &!ow in {
            borrow mut spare as &!sw in {
                let o = contents(ow);
                sort.identity(o, 40);
                borrow m as &mr in {
                    sort.by_map(mr, o, contents(sw), n, more_frequent);
                    var sum = 0;
                    var i = 0;
                    while i < 40 {
                        sum = sum + o[i];
                        if i > 0 && i < n {
                            let before_it = map.value_at(mr, o[i - 1]);
                            let it = map.value_at(mr, o[i]);
                            test.assert(before_it >= it);
                            if before_it == it {
                                test.assert(o[i - 1] < o[i]);
                            }
                        }
                        if i >= n {
                            test.assert_eq(o[i], i);
                        }
                        i = i + 1;
                    }
                    test.assert_eq(sum, 780);
                }
            }
        }
        unbox_slice(heap, order);
        unbox_slice(heap, spare);
        n = n + 1;
    }
    map.drop(heap, m);
    return 0;
}
