"""Bounding boxes, KiCad placement transforms and spatial clustering (mm, KiCad frame, y down)."""

from __future__ import annotations

import math

# A box is [x0, y0, x1, y1]; the JSON uses [x, y, w, h] (see `to_xywh`).


def rotate(x: float, y: float, deg: float) -> tuple[float, float]:
    """Rotate a point by `deg` counter-clockwise *as seen on screen* (KiCad's convention, y down)."""
    if not deg:
        return x, y
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


def box_of(points) -> list[float] | None:
    xs, ys = [], []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def union(*boxes) -> list[float] | None:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def grow(b, d: float):
    return None if b is None else [b[0] - d, b[1] - d, b[2] + d, b[3] + d]


def overlaps(a, b, gap: float = 0.0) -> bool:
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def to_xywh(b, pad: float = 0.0):
    if b is None:
        return None
    x0, y0, x1, y1 = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def arc_points(start, mid, end, n: int = 16):
    """Points along a 3-point arc (for bboxes)."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, mid, end]
    ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / d
    uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(p[1] - uy, p[0] - ux) for p in (start, mid, end))

    def ccw(a, b):
        return (b - a) % (2 * math.pi)

    span = ccw(a1, a3)
    if ccw(a1, a2) > span:  # mid not on the ccw path: go the other way
        span -= 2 * math.pi
    return [(ux + r * math.cos(a1 + span * i / n), uy + r * math.sin(a1 + span * i / n)) for i in range(n + 1)]


def circle_box(cx, cy, r):
    return [cx - r, cy - r, cx + r, cy + r]


def cluster(items, gap: float = 1.0):
    """Group (box, payload) items whose boxes are within `gap` of each other.
    Returns [(union_box, [payload, ...])]. Simple O(n log n)-ish sweep with merging."""
    groups: list[list] = []  # [box, payloads]
    for box, payload in sorted(items, key=lambda t: (t[0][0], t[0][1])):
        hit = None
        for g in groups:
            if overlaps(g[0], box, gap):
                if hit is None:
                    g[0] = union(g[0], box)
                    g[1].append(payload)
                    hit = g
                else:  # box bridges two groups: merge
                    hit[0] = union(hit[0], g[0])
                    hit[1].extend(g[1])
                    g[1] = None
        groups = [g for g in groups if g[1] is not None]
        if hit is None:
            groups.append([list(box), [payload]])
    return [(g[0], g[1]) for g in groups]
