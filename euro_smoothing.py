"""
euro_smoothing.py

Confidence-gated One-Euro smoothing for YOLO pose keypoints.

Works directly on the keypoint dict format:
    {"left_big_toe": {"x": float, "y": float, "conf": float}, ...}

The One-Euro filter (Casiez, Roussel & Vogel, 2012) smooths hard when a
keypoint is slow (kills idle jitter) and backs off when it is fast (preserves
the tap/swipe peak with low lag) -- which is exactly what a gesture signal
living in fast transients needs.

Usage:
    from euro_smoothing import KeypointSmoother

    smoother = KeypointSmoother(fps=30.0, min_conf=0.3)
    while True:
        raw = extract_keypoints_from_yolo_result(...)   # may be None
        kpts = smoother.update(raw)                      # smoothed, same format
        # ... feed kpts to your existing draw / angle / feature code ...
"""

import math


class _LowPass:
    """Exponential low-pass with an externally supplied alpha (One-Euro core)."""

    def __init__(self):
        self.y = None   # last raw input
        self.s = None   # last smoothed output

    def __call__(self, value, alpha):
        s = value if self.s is None else alpha * value + (1.0 - alpha) * self.s
        self.y = value
        self.s = s
        return s

    def last_raw(self):
        return self.y

    def reset(self):
        self.y = None
        self.s = None


class OneEuroFilter:
    """
    One-Euro filter for a single scalar signal.

    Parameters
    ----------
    freq      : nominal sampling rate in Hz (use the video fps).
    mincutoff : baseline cutoff in Hz. LOWER -> smoother when still.
    beta      : speed coefficient. HIGHER -> less lag on fast motion.
    dcutoff   : cutoff for the internal derivative low-pass (leave at 1.0).
    """

    def __init__(self, freq, mincutoff=1.0, beta=0.02, dcutoff=1.0):
        self.freq = float(freq)
        self.mincutoff = float(mincutoff)
        self.beta = float(beta)
        self.dcutoff = float(dcutoff)
        self._x = _LowPass()
        self._dx = _LowPass()

    def _alpha(self, cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / self.freq
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x, dt=None):
        if dt is not None and dt > 0:
            self.freq = 1.0 / dt
        prev = self._x.last_raw()
        dx = 0.0 if prev is None else (x - prev) * self.freq
        edx = self._dx(dx, self._alpha(self.dcutoff))
        cutoff = self.mincutoff + self.beta * abs(edx)
        return self._x(x, self._alpha(cutoff))

    def reset(self):
        self._x.reset()
        self._dx.reset()


class _PointFilter:
    """
    Smooths (x, y) for ONE keypoint.

    Holds the last good smoothed value through brief low-confidence / missing
    frames (so a single bad YOLO detection doesn't drag the filter toward a
    spurious point), and drops out entirely after `max_hold` consecutive bad
    frames so a real occlusion is reported honestly as missing.
    """

    def __init__(self, freq, min_conf, max_hold, mincutoff, beta, dcutoff):
        self.min_conf = min_conf
        self.max_hold = max_hold
        self.fx = OneEuroFilter(freq, mincutoff, beta, dcutoff)
        self.fy = OneEuroFilter(freq, mincutoff, beta, dcutoff)
        self.last = None      # (sx, sy, conf) last good smoothed output
        self.hold_age = 0     # consecutive invalid frames

    def update(self, x, y, conf, dt=None):
        valid = (
            x is not None and y is not None
            and conf is not None and conf >= self.min_conf
        )

        if valid:
            sx = self.fx(float(x), dt)
            sy = self.fy(float(y), dt)
            self.last = (sx, sy, float(conf))
            self.hold_age = 0
            return sx, sy, float(conf), False

        # invalid measurement -> bridge briefly, then drop
        self.hold_age += 1
        if self.last is None or self.hold_age > self.max_hold:
            self.fx.reset()
            self.fy.reset()
            self.last = None
            return None

        sx, sy, c = self.last
        return sx, sy, c, True   # held = True


class KeypointSmoother:
    """
    Confidence-gated One-Euro smoother over a full keypoint dict.

    update(raw_keypoints) -> dict in the SAME format as the input, with one
    extra key per point: "held" (bool). Points that have been invalid for more
    than `max_hold` frames are omitted entirely, so your existing
    get_named_point(... min_conf) logic will treat them as missing.

    Parameters
    ----------
    fps       : video frame rate, drives the filter timing.
    min_conf  : keypoints below this confidence are treated as invalid.
    max_hold  : how many consecutive bad frames to bridge before dropping a
                point (8 @ 30fps ~= 0.27 s).
    mincutoff, beta, dcutoff : One-Euro knobs (see OneEuroFilter). The defaults
                are a reasonable start for foot keypoints at 30 fps.
    """

    def __init__(self, fps=30.0, min_conf=0.3, max_hold=8,
                 mincutoff=1.0, beta=0.02, dcutoff=1.0):
        self.freq = float(fps)
        self.min_conf = min_conf
        self.max_hold = max_hold
        self._mincutoff = mincutoff
        self._beta = beta
        self._dcutoff = dcutoff
        self._filters = {}

    def _get(self, name):
        if name not in self._filters:
            self._filters[name] = _PointFilter(
                self.freq, self.min_conf, self.max_hold,
                self._mincutoff, self._beta, self._dcutoff,
            )
        return self._filters[name]

    def update(self, raw_keypoints, dt=None):
        out = {}
        # include every name we've ever tracked so points can drop out cleanly
        names = set(self._filters)
        if raw_keypoints:
            names |= set(raw_keypoints)

        for name in names:
            kp = raw_keypoints.get(name) if raw_keypoints else None
            if kp is None:
                res = self._get(name).update(None, None, None, dt)
            else:
                res = self._get(name).update(
                    kp.get("x"), kp.get("y"), kp.get("conf"), dt
                )
            if res is not None:
                sx, sy, c, held = res
                out[name] = {"x": sx, "y": sy, "conf": c, "held": held}

        return out

    def reset(self):
        self._filters.clear()
