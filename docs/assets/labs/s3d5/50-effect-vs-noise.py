#!/usr/bin/env python3
import random
import sys
from collections import defaultdict

random.seed(20260810)
A, B = sys.argv[1], sys.argv[2]

groups = defaultdict(list)
for line in sys.stdin:
    p = line.split()
    if len(p) == 2:
        try:
            groups[p[0]].append(float(p[1]))
        except ValueError:
            pass


def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


a, b = groups[A], groups[B]
obs = median(a) - median(b)
diffs = sorted(
    median([random.choice(a) for _ in a]) - median([random.choice(b) for _ in b])
    for _ in range(10000)
)
lo, hi = diffs[250], diffs[9750]
print(f"median({A}) = {median(a):.1f} ms   n={len(a)}")
print(f"median({B}) = {median(b):.1f} ms   n={len(b)}")
print(f"observed difference = {obs:+.1f} ms")
print(f"bootstrap 95% CI    = [{lo:+.1f}, {hi:+.1f}] ms  (10000 resamples)")
print("verdict: " + ("NOT MEASURABLE (CI covers 0)" if lo <= 0 <= hi else "measurable"))
