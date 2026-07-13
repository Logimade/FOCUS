# """
# foot_gestures.py  -- clean geometric classifier.
# 
# One interpretable measure per gesture, single threshold each, fixed-window
# classification. No adaptive thresholds, no pulse state, no magic.
# 
# Per foot, per frame (from smoothed keypoints):
#     ankle A, toe_center Tc = mean(big_toe, small_toe)
#     foot vector  v = Tc - A
#     angle        th = atan2(-v.y, v.x)         # degrees; image y is down
#     length       L  = |v|
#     centroid     C  = mean(big, small, heel, ankle)
# 
# A neutral pose is calibrated once (median over the first ~1 s, or press 'c').
# Then, over a fixed 30-frame window:
# 
#     SWIPE : |th - th0| peak  > swipe_deg          (the foot rotated)
#     MOVE  : |C - C0|  peak   > move_frac * L0  AND keypoints moved rigidly
#             (all displaced by ~the same vector -> a translation, not a rotation)
#     TAP   : (1 - L/L0) peak  > tap_frac           (the foot shortened: toe lift)
# 
# Checked move -> swipe -> tap -> idle. Direction comes from the sign of the
# rotation (swipe) or the dominant axis of the centroid shift (move).
# """
# 
# import numpy as np
# from collections import deque
# 
# LEFT_NAMES = dict(big="left_big_toe", small="left_small_toe",
#                   heel="left_heel", ankle="left_ankle")
# RIGHT_NAMES = dict(big="right_big_toe", small="right_small_toe",
#                    heel="right_heel", ankle="right_ankle")
# 
# DEFAULT_CFG = dict(
#     swipe_deg=22.0,    # foot rotates more than this (deg) from rest -> swipe
#     move_frac=0.40,    # base (heel+ankle) translates more than this (in foot
#                        # lengths) with little rotation -> move
#     tap_frac=0.07,     # foot shortens by more than this fraction -> tap
#     release_frac=0.5,  # re-arm once every measure falls below this fraction of
#                        # its trigger -- i.e. the foot has returned toward rest.
#                        # A gesture fires ONCE on crossing, then waits for this.
#     flip_swipe=False,  # flip if left/right swipe come out reversed for your camera
#     flip_move_x=False,
#     flip_move_y=False,
# )
# 
# 
# def _pt(kpts, name, min_conf):
#     if not kpts or name not in kpts:
#         return None
#     p = kpts[name]
#     if p.get("conf", 0.0) < min_conf:
#         return None
#     return np.array([p["x"], p["y"]], dtype=np.float64)
# 
# 
# class FootGestureRecognizer:
#     """One per foot. step(kpts, frame_no) every frame; returns a gesture label
#     on the frame a window classifies to something, else None."""
# 
#     def __init__(self, names, fps=30.0, min_conf=0.25,
#                  window=30, stride=15, cfg=DEFAULT_CFG, **_ignore):
#         self.names = names
#         self.min_conf = min_conf
#         self.cfg = cfg
#         self.window = window
#         self.stride = stride
#         self.buf = deque(maxlen=window)
#         self._since = 0
#         self.calib = deque(maxlen=int(fps * 2))
#         self.neutral = None
#         self.last = None          # last window's measures (for the HUD)
#         self.armed = True         # edge trigger: fire once, re-arm at neutral
# 
#     # ---- per-frame geometry ------------------------------------------------
#     def _frame(self, kpts):
#         big = _pt(kpts, self.names["big"], self.min_conf)
#         small = _pt(kpts, self.names["small"], self.min_conf)
#         heel = _pt(kpts, self.names["heel"], self.min_conf)
#         ankle = _pt(kpts, self.names["ankle"], self.min_conf)
#         if ankle is None or heel is None:
#             return None
#         toes = [p for p in (big, small) if p is not None]
#         if not toes:
#             return None
#         tc = np.mean(toes, axis=0)
#         base = np.mean([heel, ankle], axis=0)   # pivot: stays put in a swipe,
#                                                 # translates in a move
#         v = tc - ankle
#         ang = float(np.degrees(np.arctan2(-v[1], v[0])))
#         L = float(np.linalg.norm(v))
#         kp = dict(big=big, small=small, heel=heel, ankle=ankle)
#         allp = [p for p in kp.values() if p is not None]
#         C = np.mean(allp, axis=0)
#         return dict(ang=ang, L=max(L, 1e-6), C=C, base=base, kp=kp)
# 
#     def calibrate(self, frames):
#         kp0 = {}
#         for nm in ("big", "small", "heel", "ankle"):
#             pts = [f["kp"][nm] for f in frames if f["kp"][nm] is not None]
#             if pts:
#                 kp0[nm] = np.median(pts, axis=0)
#         self.neutral = dict(
#             ang=float(np.median([f["ang"] for f in frames])),
#             L=float(np.median([f["L"] for f in frames])),
#             C=np.median([f["C"] for f in frames], axis=0),
#             base=np.median([f["base"] for f in frames], axis=0),
#             kp=kp0)
# 
#     def reset(self):
#         self.neutral = None
#         self.calib.clear()
#         self.buf.clear()
#         self._since = 0
#         self.armed = True
# 
#     # ---- per-frame entry point --------------------------------------------
#     def step(self, kpts, frame_no):
#         f = self._frame(kpts)
#         if self.neutral is None:               # bootstrap neutral from first ~1 s
#             if f is not None:
#                 self.calib.append(f)
#                 if len(self.calib) >= self.window:
#                     self.calibrate(list(self.calib))
#             return None
# 
#         self.buf.append(f)
#         self._since += 1
#         if len(self.buf) < self.window or self._since < self.stride:
#             return None
#         self._since = 0
# 
#         label = self._classify()
# 
#         # edge trigger: re-arm only when the foot has returned toward rest
#         # (every measure below its release threshold), then fire ONCE on the
#         # next threshold crossing. This is why a HELD gesture shows numbers over
#         # threshold yet fires only once -- it must return to neutral to re-fire.
#         s = self.last
#         if s is not None:
#             rel = self.cfg["release_frac"]
#             if (abs(s["swipe"]) < self.cfg["swipe_deg"] * rel
#                     and s["move"] < self.cfg["move_frac"] * rel
#                     and s["tap"] < self.cfg["tap_frac"] * rel):
#                 self.armed = True
# 
#         if label is not None and self.armed:
#             self.armed = False
#             return label
#         return None
# 
#     # ---- window classification --------------------------------------------
#     def _classify(self):
#         frames = [f for f in self.buf if f is not None]
#         if len(frames) < self.window * 0.5:
#             self.last = None
#             return None
#         n, c = self.neutral, self.cfg
#         L0 = max(n["L"], 1e-6)
# 
#         ang = np.array([f["ang"] for f in frames])
#         L = np.array([f["L"] for f in frames])
#         B = np.array([f["base"] for f in frames])
# 
#         # SWIPE measure: peak rotation of the foot vector from neutral (+-180)
#         dth = ((ang - n["ang"] + 180.0) % 360.0) - 180.0
#         i_sw = int(np.argmax(np.abs(dth)))
#         swipe = float(dth[i_sw])
# 
#         # MOVE measure: peak BASE (pivot) translation, in foot lengths. The base
#         # stays put when the foot pivots (swipe) and translates when the whole
#         # foot slides (move) -- this is the clean swipe/move separator.
#         Bd = (B - n["base"]) / L0
#         bmag = np.linalg.norm(Bd, axis=1)
#         i_mv = int(np.argmax(bmag))
#         move = float(bmag[i_mv]); move_vec = Bd[i_mv]
# 
#         # TAP measure: peak shortening fraction
#         tap = float((1.0 - L / L0).max())
# 
#         # Decision: priority order with the physical move/swipe guard. We do NOT
#         # pick by measure/threshold "score": the three are in different units
#         # (deg, foot-lengths, fraction) and are not comparable as probabilities.
#         # tap is the weakest/noisiest on a front camera and would steal windows.
#         label = None
#         if move > c["move_frac"] and abs(swipe) < c["swipe_deg"]:
#             label = self._move_label(move_vec)
#         elif abs(swipe) > c["swipe_deg"]:
#             d = -swipe if c["flip_swipe"] else swipe
#             label = "swipe_left" if d > 0 else "swipe_right"
#         elif tap > c["tap_frac"]:
#             label = "tap"
# 
#         self.last = dict(swipe=swipe, move=move, tap=tap, move_vec=move_vec,
#                          best=label)
#         return label
# 
#     def _move_label(self, vec):
#         dx, dy = float(vec[0]), float(vec[1])
#         if self.cfg["flip_move_x"]:
#             dx = -dx
#         if self.cfg["flip_move_y"]:
#             dy = -dy
#         if abs(dx) >= abs(dy):
#             return "move_right" if dx > 0 else "move_left"
#         # image y grows downward: dy<0 = up/away = forward, dy>0 = down/near = back
#         return "move_back" if dy > 0 else "move_forward"

"""
foot_gestures.py  -- clean geometric classifier.

One interpretable measure per gesture, single threshold each, fixed-window
classification. No adaptive thresholds, no pulse state, no magic.

Per foot, per frame (from smoothed keypoints):
    ankle A, toe_center Tc = mean(big_toe, small_toe)
    foot vector  v = Tc - A
    angle        th = atan2(-v.y, v.x)         # degrees; image y is down
    length       L  = |v|
    centroid     C  = mean(big, small, heel, ankle)

A neutral pose is calibrated once (median over the first ~1 s, or press 'c').
Then, over a fixed 30-frame window:

    SWIPE : |th - th0| peak  > swipe_deg          (the foot rotated)
    MOVE  : |C - C0|  peak   > move_frac * L0  AND keypoints moved rigidly
            (all displaced by ~the same vector -> a translation, not a rotation)
    TAP   : (1 - L/L0) peak  > tap_frac           (the foot shortened: toe lift)

Checked move -> swipe -> tap -> idle. Direction comes from the sign of the
rotation (swipe) or the dominant axis of the centroid shift (move).
"""

import numpy as np
from collections import deque

LEFT_NAMES = dict(big="left_big_toe", small="left_small_toe",
                  heel="left_heel", ankle="left_ankle")
RIGHT_NAMES = dict(big="right_big_toe", small="right_small_toe",
                   heel="right_heel", ankle="right_ankle")

DEFAULT_CFG = dict(
    swipe_frac=0.28,   # HORIZONTAL toe motion vs rest (foot lengths) -> swipe
    tap_frac=0.07,     # VERTICAL toe motion vs rest (foot lengths) -> tap
                       # (up OR down; the toe tipping is what counts, not length)
    move_frac=0.40,    # base (heel+ankle) translation (foot lengths) -> move
    release_frac=0.5,  # re-arm once every measure falls below this fraction of
                       # its trigger -- i.e. the foot has returned toward rest.
    flip_swipe=False,  # flip if left/right swipe come out reversed for your camera
    flip_move_x=False,
    flip_move_y=False,
)


def _pt(kpts, name, min_conf):
    if not kpts or name not in kpts:
        return None
    p = kpts[name]
    if p.get("conf", 0.0) < min_conf:
        return None
    return np.array([p["x"], p["y"]], dtype=np.float64)


class FootGestureRecognizer:
    """One per foot. step(kpts, frame_no) every frame; returns a gesture label
    on the frame a window classifies to something, else None."""

    def __init__(self, names, fps=30.0, min_conf=0.25,
                 window=30, stride=15, cfg=DEFAULT_CFG, **_ignore):
        self.names = names
        self.min_conf = min_conf
        self.cfg = cfg
        self.window = window
        self.stride = stride
        self.buf = deque(maxlen=window)
        self._since = 0
        self.calib = deque(maxlen=int(fps * 2))
        self.neutral = None
        self.last = None          # last window's measures (for the HUD)
        self.armed = True         # edge trigger: fire once, re-arm at neutral

    # ---- per-frame geometry ------------------------------------------------
    def _frame(self, kpts):
        big = _pt(kpts, self.names["big"], self.min_conf)
        small = _pt(kpts, self.names["small"], self.min_conf)
        heel = _pt(kpts, self.names["heel"], self.min_conf)
        ankle = _pt(kpts, self.names["ankle"], self.min_conf)
        if ankle is None or heel is None:
            return None
        toes = [p for p in (big, small) if p is not None]
        if not toes:
            return None
        tc = np.mean(toes, axis=0)
        v = tc - ankle                          # ankle -> toe-center vector
        base = np.mean([heel, ankle], axis=0)   # pivot: stays in swipe/tap,
                                                # translates in a move
        return dict(v=v, L=max(float(np.linalg.norm(v)), 1e-6), base=base)

    def calibrate(self, frames):
        self.neutral = dict(
            v0=np.median([f["v"] for f in frames], axis=0),
            L=float(np.median([f["L"] for f in frames])),
            base=np.median([f["base"] for f in frames], axis=0))

    def reset(self):
        self.neutral = None
        self.calib.clear()
        self.buf.clear()
        self._since = 0
        self.armed = True

    # ---- per-frame entry point --------------------------------------------
    def step(self, kpts, frame_no):
        f = self._frame(kpts)
        if self.neutral is None:               # bootstrap neutral from first ~1 s
            if f is not None:
                self.calib.append(f)
                if len(self.calib) >= self.window:
                    self.calibrate(list(self.calib))
            return None

        self.buf.append(f)
        self._since += 1
        if len(self.buf) < self.window or self._since < self.stride:
            return None
        self._since = 0

        label = self._classify()

        # edge trigger: re-arm only when the foot has returned toward rest
        # (every measure below its release threshold), then fire ONCE on the
        # next threshold crossing. This is why a HELD gesture shows numbers over
        # threshold yet fires only once -- it must return to neutral to re-fire.
        s = self.last
        if s is not None:
            rel = self.cfg["release_frac"]
            if (abs(s["swipe"]) < self.cfg["swipe_frac"] * rel
                    and abs(s["tap"]) < self.cfg["tap_frac"] * rel
                    and s["move"] < self.cfg["move_frac"] * rel):
                self.armed = True

        if label is not None and self.armed:
            self.armed = False
            return label
        return None

    # ---- window classification --------------------------------------------
    def _classify(self):
        frames = [f for f in self.buf if f is not None]
        if len(frames) < self.window * 0.5:
            self.last = None
            return None
        n, c = self.neutral, self.cfg
        L0 = max(n["L"], 1e-6)

        V = np.array([f["v"] for f in frames])      # foot vector each frame
        B = np.array([f["base"] for f in frames])

        # Toe motion RELATIVE to the ankle, vs rest, split by axis:
        #   dx (horizontal) = toe sweeps sideways -> SWIPE
        #   dy (vertical)   = toe tips up/down     -> TAP
        # The single 'angle' could not separate these (a tap and a swipe both
        # rotate the foot vector); the axis split can.
        dV = (V - n["v0"]) / L0
        dx, dy = dV[:, 0], dV[:, 1]
        sx = float(dx[np.argmax(np.abs(dx))]); hx = abs(sx)    # swipe signal
        sy = float(dy[np.argmax(np.abs(dy))]); vy = abs(sy)    # tap signal

        # MOVE: whole-foot (base) translation
        Bd = (B - n["base"]) / L0
        bmag = np.linalg.norm(Bd, axis=1)
        i_mv = int(np.argmax(bmag))
        move = float(bmag[i_mv]); move_vec = Bd[i_mv]

        # Decision: the toe moving sideways is a swipe, the toe tipping up/down
        # is a tap (the dominant axis decides between them); a foot that didn't
        # articulate but translated is a move.
        label = None
        if hx > c["swipe_frac"] and hx >= vy:
            d = -sx if c["flip_swipe"] else sx
            label = "swipe_right" if d > 0 else "swipe_left"
        elif vy > c["tap_frac"] and vy > hx:
            label = "tap"
        elif move > c["move_frac"]:
            label = self._move_label(move_vec)

        self.last = dict(swipe=sx, tap=sy, move=move, move_vec=move_vec,
                         best=label)
        return label

    def _move_label(self, vec):
        dx, dy = float(vec[0]), float(vec[1])
        if self.cfg["flip_move_x"]:
            dx = -dx
        if self.cfg["flip_move_y"]:
            dy = -dy
        if abs(dx) >= abs(dy):
            return "move_right" if dx > 0 else "move_left"
        # image y grows downward: dy<0 = up/away = forward, dy>0 = down/near = back
        return "move_back" if dy > 0 else "move_forward"