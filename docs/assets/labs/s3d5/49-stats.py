#!/usr/bin/env python3
import sys
from collections import defaultdict


def quantile(xs, q):
    if not xs:
        return float("nan")
    pos = (len(xs) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


groups = defaultdict(list)
for line in sys.stdin:
    parts = line.split()
    if len(parts) == 2:
        try:
            groups[parts[0]].append(float(parts[1]))
        except ValueError:
            pass

print(f"{'label':<22}{'n':>4}{'min':>9}{'p25':>9}{'median':>9}{'p75':>9}{'max':>9}{'IQR':>9}")
for label, xs in groups.items():
    xs.sort()
    p25, p50, p75 = quantile(xs, 0.25), quantile(xs, 0.5), quantile(xs, 0.75)
    print(f"{label:<22}{len(xs):>4}{xs[0]:>9.1f}{p25:>9.1f}{p50:>9.1f}{p75:>9.1f}{xs[-1]:>9.1f}{p75 - p25:>9.1f}")
