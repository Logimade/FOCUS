# """
# evaluate.py  --  score the rule-based recognizer on the synthetic clips.
#
# Pipeline mirrors live_demo.py:  YOLO -> One-Euro smoother -> FootGestureRecognizer
# The clip's TRUE label is its filename (e.g. swipe_left_0003.mp4 -> swipe_left);
# the PREDICTED label is the gesture the recognizer fires.
#
# Two stages, decoupled by a keypoint cache so you only run YOLO once:
#   1. extract: YOLO over every clip -> raw keypoints per frame -> cache/<clip>.json
#   2. score  : cache -> smoother + recognizers -> predicted label -> metrics
#
# Usage:
#   python evaluate.py --clips ./synth --model best.pt --cache ./kpcache   # extract + score
#   python evaluate.py --clips ./synth --cache ./kpcache                    # re-score only
#
# Metrics: confusion matrix + per-class precision/recall/F1 + MACRO-F1 (headline),
# plus detection rate and spurious-fire count. Macro-F1 is the number to watch --
# it won't let an easy class hide a failing one (expect move_forward/back to be
# the weak pair on a front camera).
# """
#
# import argparse, os, glob, re, json
# import numpy as np
#
# # Import the SAME recognizer module live_demo.py uses, so the eval scores the
# # classifier you actually run -- not a stale copy under a different name.
# try:
#     from gestures import (FootGestureRecognizer, LEFT_NAMES, RIGHT_NAMES,
#                           DEFAULT_CFG)
#     _REC_MODULE = "gestures"
# except ImportError:
#     from foot_gestures import (FootGestureRecognizer, LEFT_NAMES, RIGHT_NAMES,
#                               DEFAULT_CFG)
#     _REC_MODULE = "foot_gestures"
#
# LABELS = ["tap", "swipe_left", "swipe_right", "move_left", "move_right",
#           "move_forward", "move_backward"]
# # recognizer emits move_back; canonicalize to the synthetic label name
# NORMALIZE = {"move_back": "move_backward"}
#
#
# def canon(lbl):
#     return NORMALIZE.get(lbl, lbl)
#
#
# def label_from_name(path):
#     stem = os.path.splitext(os.path.basename(path))[0]
#     return re.sub(r"_\d+$", "", stem)            # strip trailing _0001
#
#
# def clip_key(path):
#     """Unique cache key across dirs: <parent_dir>__<filename>."""
#     parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
#     stem = os.path.splitext(os.path.basename(path))[0]
#     return parent + "__" + stem
#
#
# # --------------------------- stage 1: extract -------------------------------
# def extract_to_cache(clips, model_path, cache_dir, min_conf):
#     import cv2
#     from ultralytics import YOLO
#     from live_demo import extract_keypoints
#     os.makedirs(cache_dir, exist_ok=True)
#     model = YOLO(model_path)
#     for path in clips:
#         out = os.path.join(cache_dir, clip_key(path) + ".json")
#         if os.path.exists(out):
#             continue
#         cap = cv2.VideoCapture(path)
#         fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
#         frames = []
#         while True:
#             ok, frame = cap.read()
#             if not ok:
#                 break
#             res = model(frame, verbose=False)[0]
#             frames.append(extract_keypoints(res))     # {name:{x,y,conf}} or None
#         cap.release()
#         with open(out, "w") as f:
#             json.dump({"fps": fps, "frames": frames}, f)
#         print("cached", os.path.basename(out), f"({len(frames)} frames)")
#
#
# # --------------------------- stage 2: score ---------------------------------
# def predict_clip(raw_frames, fps, min_conf, cfg):
#     from euro_smoothing import KeypointSmoother
#     sm = KeypointSmoother(fps=fps, min_conf=min_conf, max_hold=3)
#     left = FootGestureRecognizer(LEFT_NAMES, fps=fps, min_conf=min_conf, cfg=cfg)
#     right = FootGestureRecognizer(RIGHT_NAMES, fps=fps, min_conf=min_conf, cfg=cfg)
#     dt = 1.0 / fps
#     fired = []
#     for i, raw in enumerate(raw_frames):
#         kpts = sm.update(raw, dt=dt)
#         for ev in (left.step(kpts, i), right.step(kpts, i)):
#             if ev:
#                 fired.append(canon(ev))
#     return fired
#
#
# def score(clips, cache_dir, min_conf, cfg):
#     y_true, y_pred, spurious, missed = [], [], 0, 0
#     for path in clips:
#         cache = os.path.join(cache_dir, clip_key(path) + ".json")
#         if not os.path.exists(cache):
#             print("no cache for", os.path.basename(path), "-- skipping")
#             continue
#         with open(cache) as f:
#             d = json.load(f)
#         fired = predict_clip(d["frames"], d.get("fps", 30.0), min_conf, cfg)
#         true = label_from_name(path)
#         if true == "idle":
#             true = "none"                           # idle clips must NOT fire
#         pred = fired[0] if fired else "none"        # first fire = the gesture
#         if true != "none" and not fired:
#             missed += 1
#         elif true != "none" and len(set(fired)) > 1:
#             spurious += 1
#         y_true.append(true); y_pred.append(pred)
#     return y_true, y_pred, spurious, missed
#
#
# # --------------------------- reporting --------------------------------------
# def report(y_true, y_pred, spurious, missed):
#     from sklearn.metrics import (confusion_matrix, classification_report,
#                                  f1_score)
#     cols = LABELS + (["none"] if "none" in y_pred else [])
#     cm = confusion_matrix(y_true, y_pred, labels=cols)
#
#     print("\n=== confusion matrix (rows=true, cols=pred) ===")
#     head = "".join(f"{c[:9]:>10}" for c in cols)
#     print(f"{'':>14}{head}")
#     for i, t in enumerate(cols):
#         if t not in LABELS:
#             continue
#         row = "".join(f"{cm[i][j]:>10}" for j in range(len(cols)))
#         print(f"{t:>14}{row}")
#
#     print("\n=== per-class precision / recall / F1 ===")
#     print(classification_report(y_true, y_pred, labels=LABELS, zero_division=0, digits=3))
#
#     macro = f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)
#     idle_total = sum(1 for t in y_true if t == "none")
#     idle_fired = sum(1 for t, p in zip(y_true, y_pred) if t == "none" and p != "none")
#     gest = len(y_true) - idle_total
#     print(f"MACRO-F1 (headline): {macro:.3f}")
#     if gest:
#         print(f"detection rate     : {(gest - missed) / gest:.3f}  "
#               f"({gest - missed}/{gest} gesture clips fired)")
#     print(f"missed (no fire)   : {missed}")
#     print(f"spurious (>1 label): {spurious}")
#     if idle_total:
#         print(f"FALSE-FIRE rate    : {idle_fired / idle_total:.3f}  "
#               f"({idle_fired}/{idle_total} idle clips fired -- lower is better)")
#     return macro
#
#
# def main():
#     ap = argparse.ArgumentParser()
#     ap.add_argument("--clips", required=True, nargs="+", help="one or more folders of .mp4 clips")
#     ap.add_argument("--cache", default="./kpcache")
#     ap.add_argument("--model", default="", help="YOLO .pt (needed if cache empty)")
#     ap.add_argument("--min-conf", type=float, default=0.25)
#     ap.add_argument("--flip-swipe", action="store_true", help="swap swipe left/right")
#     ap.add_argument("--flip-move-x", action="store_true", help="swap move left/right")
#     ap.add_argument("--flip-move-y", action="store_true", help="swap move fwd/back")
#     args = ap.parse_args()
#
#     clips = []
#     for d in args.clips:
#         clips += sorted(glob.glob(os.path.join(d, "*.mp4")))
#     if not clips:
#         raise SystemExit("no .mp4 found in: " + ", ".join(args.clips))
#     print(f"{len(clips)} clips across {len(args.clips)} dir(s)")
#     print(f"recognizer module: {_REC_MODULE}")
#
#     cfg = dict(DEFAULT_CFG, flip_swipe=args.flip_swipe,
#                flip_move_x=args.flip_move_x, flip_move_y=args.flip_move_y)
#
#     if args.model:
#         extract_to_cache(clips, args.model, args.cache, args.min_conf)
#     y_true, y_pred, spurious, missed = score(clips, args.cache, args.min_conf, cfg)
#     if not y_true:
#         raise SystemExit("nothing scored -- run once with --model to build the cache")
#     report(y_true, y_pred, spurious, missed)
#
#
# if __name__ == "__main__":
#     main()

"""
evaluate.py  --  score the rule-based recognizer on the synthetic clips.

Pipeline mirrors live_demo.py:  YOLO -> One-Euro smoother -> FootGestureRecognizer
The clip's TRUE label is its filename (e.g. swipe_left_0003.mp4 -> swipe_left);
the PREDICTED label is the gesture the recognizer fires.

Two stages, decoupled by a keypoint cache so you only run YOLO once:
  1. extract: YOLO over every clip -> raw keypoints per frame -> cache/<clip>.json
  2. score  : cache -> smoother + recognizers -> predicted label -> metrics

Usage:
  python evaluate.py --clips ./synth --model best.pt --cache ./kpcache   # extract + score
  python evaluate.py --clips ./synth --cache ./kpcache                    # re-score only

Metrics: confusion matrix + per-class precision/recall/F1 + MACRO-F1 (headline),
plus detection rate and spurious-fire count. Macro-F1 is the number to watch --
it won't let an easy class hide a failing one (expect move_forward/back to be
the weak pair on a front camera).
"""

import argparse, os, glob, re, json
import numpy as np

# Import the SAME recognizer module live_demo.py uses, so the eval scores the
# classifier you actually run -- not a stale copy under a different name.
try:
    from gestures import (FootGestureRecognizer, LEFT_NAMES, RIGHT_NAMES,
                          DEFAULT_CFG)
    _REC_MODULE = "gestures"
except ImportError:
    from foot_gestures import (FootGestureRecognizer, LEFT_NAMES, RIGHT_NAMES,
                              DEFAULT_CFG)
    _REC_MODULE = "foot_gestures"

LABELS = ["tap", "swipe_left", "swipe_right", "move_left", "move_right",
          "move_forward", "move_backward"]
# recognizer emits move_back; canonicalize to the synthetic label name
NORMALIZE = {"move_back": "move_backward"}


def canon(lbl):
    return NORMALIZE.get(lbl, lbl)


def label_from_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"_\d+$", "", stem)            # strip trailing _0001


def clip_key(path):
    """Unique cache key across dirs: <parent_dir>__<filename>."""
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    stem = os.path.splitext(os.path.basename(path))[0]
    return parent + "__" + stem


# --------------------------- stage 1: extract -------------------------------
def extract_to_cache(clips, model_path, cache_dir, min_conf):
    import cv2
    from ultralytics import YOLO
    from live_demo import extract_keypoints
    os.makedirs(cache_dir, exist_ok=True)
    model = YOLO(model_path)
    for path in clips:
        out = os.path.join(cache_dir, clip_key(path) + ".json")
        if os.path.exists(out):
            continue
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            res = model(frame, verbose=False)[0]
            frames.append(extract_keypoints(res))     # {name:{x,y,conf}} or None
        cap.release()
        with open(out, "w") as f:
            json.dump({"fps": fps, "frames": frames}, f)
        print("cached", os.path.basename(out), f"({len(frames)} frames)")


# --------------------------- stage 2: score ---------------------------------
def predict_clip(raw_frames, fps, min_conf, cfg):
    from euro_smoothing import KeypointSmoother
    sm = KeypointSmoother(fps=fps, min_conf=min_conf, max_hold=3)
    left = FootGestureRecognizer(LEFT_NAMES, fps=fps, min_conf=min_conf, cfg=cfg)
    right = FootGestureRecognizer(RIGHT_NAMES, fps=fps, min_conf=min_conf, cfg=cfg)
    dt = 1.0 / fps
    fired = []
    for i, raw in enumerate(raw_frames):
        kpts = sm.update(raw, dt=dt)
        for ev in (left.step(kpts, i), right.step(kpts, i)):
            if ev:
                fired.append(canon(ev))
    return fired


def score(clips, cache_dir, min_conf, cfg):
    y_true, y_pred, spurious, missed = [], [], 0, 0
    for path in clips:
        cache = os.path.join(cache_dir, clip_key(path) + ".json")
        if not os.path.exists(cache):
            print("no cache for", os.path.basename(path), "-- skipping")
            continue
        with open(cache) as f:
            d = json.load(f)
        fired = predict_clip(d["frames"], d.get("fps", 30.0), min_conf, cfg)
        true = label_from_name(path)
        if true == "idle":
            true = "none"                           # idle clips must NOT fire
        pred = fired[0] if fired else "none"        # first fire = the gesture
        if true != "none" and not fired:
            missed += 1
        elif true != "none" and len(set(fired)) > 1:
            spurious += 1
        y_true.append(true); y_pred.append(pred)
    return y_true, y_pred, spurious, missed


# --------------------------- reporting --------------------------------------
def report(y_true, y_pred, spurious, missed, labels):
    from sklearn.metrics import (confusion_matrix, classification_report,
                                 f1_score)
    cols = labels + (["none"] if "none" in y_pred or "none" in y_true else [])
    cm = confusion_matrix(y_true, y_pred, labels=cols)

    print("\n=== confusion matrix (rows=true, cols=pred) ===")
    head = "".join(f"{c[:9]:>10}" for c in cols)
    print(f"{'':>14}{head}")
    for i, t in enumerate(cols):
        if t not in labels:
            continue
        row = "".join(f"{cm[i][j]:>10}" for j in range(len(cols)))
        print(f"{t:>14}{row}")

    print("\n=== per-class precision / recall / F1 ===")
    print(classification_report(y_true, y_pred, labels=labels, zero_division=0, digits=3))

    macro = f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    idle_total = sum(1 for t in y_true if t == "none")
    idle_fired = sum(1 for t, p in zip(y_true, y_pred) if t == "none" and p != "none")
    gest = len(y_true) - idle_total
    print(f"MACRO-F1 (headline): {macro:.3f}")
    if gest:
        print(f"detection rate     : {(gest - missed) / gest:.3f}  "
              f"({gest - missed}/{gest} gesture clips fired)")
    print(f"missed (no fire)   : {missed}")
    print(f"spurious (>1 label): {spurious}")
    if idle_total:
        print(f"FALSE-FIRE rate    : {idle_fired / idle_total:.3f}  "
              f"({idle_fired}/{idle_total} idle clips fired -- lower is better)")
    return macro


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True, nargs="+", help="one or more folders of .mp4 clips")
    ap.add_argument("--cache", default="./kpcache")
    ap.add_argument("--model", default="", help="YOLO .pt (needed if cache empty)")
    ap.add_argument("--min-conf", type=float, default=0.25)
    ap.add_argument("--flip-swipe", action="store_true", help="swap swipe left/right")
    ap.add_argument("--flip-move-x", action="store_true", help="swap move left/right")
    ap.add_argument("--flip-move-y", action="store_true", help="swap move fwd/back")
    ap.add_argument("--no-move", action="store_true",
                    help="evaluate only tap/swipe/none; drop move clips + disable move detection")
    args = ap.parse_args()

    clips = []
    for d in args.clips:
        clips += sorted(glob.glob(os.path.join(d, "*.mp4")))
    if not clips:
        raise SystemExit("no .mp4 found in: " + ", ".join(args.clips))
    print(f"{len(clips)} clips across {len(args.clips)} dir(s)")
    print(f"recognizer module: {_REC_MODULE}")

    cfg = dict(DEFAULT_CFG, flip_swipe=args.flip_swipe,
               flip_move_x=args.flip_move_x, flip_move_y=args.flip_move_y)

    labels = LABELS
    if args.no_move:
        labels = ["tap", "swipe_left", "swipe_right"]
        clips = [c for c in clips if not label_from_name(c).startswith("move")]
        cfg["move_frac"] = 1e9            # disable move detection entirely
        print(f"no-move mode: {len(clips)} tap/swipe/idle clips, move disabled")

    if args.model:
        extract_to_cache(clips, args.model, args.cache, args.min_conf)
    y_true, y_pred, spurious, missed = score(clips, args.cache, args.min_conf, cfg)
    if not y_true:
        raise SystemExit("nothing scored -- run once with --model to build the cache")
    report(y_true, y_pred, spurious, missed, labels)


if __name__ == "__main__":
    main()