"""
live_demo.py

Live (or file-playback) sanity check for the foot-gesture pipeline:
    YOLO pose -> KeypointSmoother (One-Euro) -> FootGestureRecognizer
with an on-screen HUD so you can SEE why gestures fire or not.

Run:
    # webcam
    python live_demo.py --model best.pt --source 0
    # one of your video files
    python live_demo.py --model best.pt --source normalized_debug_video.mp4

Keys:
    q / ESC : quit
    c       : reset calibration + filters (re-learn neutral now)
    m       : toggle MOVE detection (off by default -- unreliable on a
              moving camera, see note in chat)

Note on a moving camera: the translation/move channel is NOT trustworthy when
the camera pans, because the whole foot appears to move. Tap and swipe rely on
the articulation channel (toe relative to base), which is differential and
largely cancels camera translation -- those are the ones to trust here.
"""

import argparse
import time
import cv2
import numpy as np
from ultralytics import YOLO

from euro_smoothing import KeypointSmoother
from foot_gestures import FootGestureRecognizer, LEFT_NAMES, RIGHT_NAMES

KEYPOINT_NAMES = {
    0: "left_big_toe", 1: "left_small_toe", 2: "left_heel", 3: "left_ankle",
    4: "right_big_toe", 5: "right_small_toe", 6: "right_heel", 7: "right_ankle",
}


def extract_keypoints(result, instance_index=0):
    """YOLO pose result -> {name: {x, y, conf}} or None."""
    kp = getattr(result, "keypoints", None)
    if kp is None or kp.xy is None:
        return None
    xy = kp.xy.cpu().numpy()
    if len(xy) == 0 or instance_index >= len(xy):
        return None
    pts = xy[instance_index]
    conf = (kp.conf.cpu().numpy()[instance_index]
            if kp.conf is not None else np.ones(len(pts), np.float32))
    out = {}
    for idx, name in KEYPOINT_NAMES.items():
        if idx < len(pts):
            x, y = pts[idx]
            out[name] = {"x": float(x), "y": float(y),
                         "conf": float(conf[idx]) if idx < len(conf) else 1.0}
    return out


def draw_keypoints(frame, kpts, min_conf):
    if not kpts:
        return
    for name, p in kpts.items():
        if p["conf"] < min_conf:
            continue
        x, y = int(p["x"]), int(p["y"])
        # held (coasting) points drawn hollow so you can see dropout bridging
        if p.get("held"):
            cv2.circle(frame, (x, y), 5, (0, 165, 255), 1)
        else:
            cv2.circle(frame, (x, y), 4, (0, 255, 255), -1)


def hud(frame, rec, foot, x0, color):
    """Print the live summary numbers that drive the decision."""
    s = rec.last_summary
    cv2.putText(frame, foot, (x0, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    if not s:
        cv2.putText(frame, "no window", (x0, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
        return
    lines = [
        f"energy {s['energy']:.3f}  (floor {rec.cfg['floor']:.3f})",
        f"R      {s['R']:.2f}",
        f"ay_pk  {s['ay_pk']:+.3f}  ret={int(s['ay_ret'])}",
        f"ax_pk  {s['ax_pk']:+.3f}  ret={int(s['ax_ret'])}",
        f"t_net  {s['t_net']:.3f}",
    ]
    for i, ln in enumerate(lines):
        cv2.putText(frame, ln, (x0, 48 + 20 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--source", default="0", help="webcam index or video path")
    ap.add_argument("--min-conf", type=float, default=0.25)  # lower for poor quality
    ap.add_argument("--flip", action="store_true",
                    help="mirror webcam (note: swaps left/right swipe sense)")
    args = ap.parse_args()

    model = YOLO(args.model)
    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open source: {args.source}")

    fps_hint = cap.get(cv2.CAP_PROP_FPS) or 30.0
    smoother = KeypointSmoother(fps=fps_hint, min_conf=args.min_conf, max_hold=10)
    # move OFF by default: unreliable with a moving camera
    left = FootGestureRecognizer(LEFT_NAMES, fps=fps_hint,
                                 min_conf=args.min_conf, enable_move=False)
    right = FootGestureRecognizer(RIGHT_NAMES, fps=fps_hint,
                                  min_conf=args.min_conf, enable_move=False)

    latch = {"L": ("", 0), "R": ("", 0)}   # (label, frames_remaining)
    frame_no = 0
    t_prev = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if args.flip:
            frame = cv2.flip(frame, 1)

        now = time.perf_counter()
        dt = (now - t_prev) if t_prev is not None else None
        t_prev = now

        result = model(frame, verbose=False)[0]
        raw = extract_keypoints(result)
        kpts = smoother.update(raw, dt=dt)
        #
        ev_l = left.step(kpts, frame_no)
        ev_r = right.step(kpts, frame_no)
        if ev_l:
            latch["L"] = (ev_l, int(0.6 * (1.0 / dt if dt else fps_hint)))
            print(f"[{frame_no}] LEFT  -> {ev_l}")
        if ev_r:
            latch["R"] = (ev_r, int(0.6 * (1.0 / dt if dt else fps_hint)))
            print(f"[{frame_no}] RIGHT -> {ev_r}")

        draw_keypoints(frame, kpts, args.min_conf)
        hud(frame, left, "LEFT", 12, (0, 255, 0))
        hud(frame, right, "RIGHT", frame.shape[1] - 230, (0, 160, 255))

        # latched gesture banner
        for side, (lbl, rem) in latch.items():
            if rem > 0 and lbl:
                yb = 150 if side == "L" else 185
                col = (0, 255, 0) if side == "L" else (0, 160, 255)
                cv2.putText(frame, f"{side}: {lbl}", (12, yb),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)
                latch[side] = (lbl, rem - 1)

        live_fps = (1.0 / dt) if dt else 0.0
        cv2.putText(frame, f"{live_fps:4.1f} fps  [q]uit [c]alib [m]ove",
                    (12, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1)

        cv2.imshow("foot gestures (live check)", frame)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("c"):
            smoother.reset()
            left.proc.S = right.proc.S = None      # force re-bootstrap of neutral
            left.proc._calib.clear(); right.proc._calib.clear()
            print("calibration + filters reset")
        elif k == ord("m"):
            left.enable_move = not left.enable_move
            right.enable_move = left.enable_move
            print(f"move detection: {left.enable_move}")

        frame_no += 1

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
