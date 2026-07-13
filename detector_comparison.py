#!/usr/bin/env python3
"""
detector_comparison.py -- build the qualitative detector-comparison grid.

ONE input image is cropped to three visibility levels (full body / lower body /
feet only) so the ONLY variable is how much of the body is visible. Each crop is
run through three detectors and the results are tiled into a labelled grid:

                 MediaPipe Pose | RTMPose WholeBody |  Ours (foot-only)
   Full body          ...               ...                 ...
   Lower body         ...               ...                 ...
   Feet only          ...               ...                 ...

This makes the paper's point visible: general estimators degrade as the body
leaves frame, while the foot-only detector keeps producing the needed landmarks.

Dependencies (install what you have/need; missing ones degrade gracefully):
    pip install ultralytics            # your detector (required)
    pip install mediapipe              # MediaPipe Pose
    pip install rtmlib onnxruntime     # RTMPose WholeBody (lightweight, no mmcv)
    pip install pillow                 # grid compositing

Usage:
    python detector_comparison.py --image person.jpg --model ./train8/weights/best.pt
    python detector_comparison.py --image person.jpg --model best.pt \
        --hip-frac 0.45 --feet-frac 0.80 --device cuda --out detector_comparison_grid
"""

import argparse, os
import numpy as np
import cv2


# ----------------------------- detectors ------------------------------------
def run_yolo(img, model_path, device="cpu", _cache={}):
    try:
        if "m" not in _cache:
            from ultralytics import YOLO
            _cache["m"] = YOLO(model_path)
        res = _cache["m"](img, device=device, verbose=False)[0]
        if res.boxes is not None and len(res.boxes) > 0:
            idx = int(res.boxes.conf.argmax())       # highest-confidence box only
            res = res[idx:idx + 1]
        w = img.shape[1]                             # width is constant across crops
        kr, lw = max(8, w // 60), max(3, w // 340)
        try:
            return res.plot(boxes=True, kpt_radius=kr, line_width=lw), True
        except TypeError:
            return res.plot(boxes=True), True       # older ultralytics
    except Exception as e:
        return _stamp(img, f"YOLO error: {e}"), False


_POSE_CONN = [(0,1),(1,2),(2,3),(3,7),(0,4),(4,5),(5,6),(6,8),(9,10),
              (11,12),(11,13),(13,15),(15,17),(15,19),(15,21),(17,19),
              (12,14),(14,16),(16,18),(16,20),(16,22),(18,20),
              (11,23),(12,24),(23,24),(23,25),(24,26),(25,27),(26,28),
              (27,29),(28,30),(29,31),(30,32),(27,31),(28,32)]


def _draw_mp(img, poses):
    h, w = img.shape[:2]
    r = max(6, w // 80)                 # scale to width (constant across crops)
    lw = max(3, w // 300)
    for lms in poses:
        pts = [(int(l.x * w), int(l.y * h)) for l in lms]
        for a, b in _POSE_CONN:
            if a < len(pts) and b < len(pts):
                cv2.line(img, pts[a], pts[b], (0, 255, 255), lw, cv2.LINE_AA)   # yellow
        for (x, y) in pts:
            cv2.circle(img, (x, y), r, (0, 0, 255), -1, cv2.LINE_AA)            # red fill
            cv2.circle(img, (x, y), r, (255, 255, 255), max(1, lw // 2), cv2.LINE_AA)  # white ring


def run_mediapipe(img, model_path="pose_landmarker.task", use_gpu=False, _cache={}):
    """MediaPipe Tasks API. Requests the GPU delegate when use_gpu=True, falling
    back to CPU (with a notice) if the GPU delegate is unavailable -- common on
    headless Linux. _cache['dev'] records the device actually used."""
    try:
        import mediapipe as mp
        if "lm" not in _cache:
            if not os.path.exists(model_path):
                return _stamp(img, f"MediaPipe model not found: {model_path}"), False
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision
            Deleg = mp_python.BaseOptions.Delegate

            def make(delegate):
                opts = vision.PoseLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=model_path,
                                                       delegate=delegate),
                    running_mode=vision.RunningMode.IMAGE,
                    num_poses=5, min_pose_detection_confidence=0.3)
                return vision.PoseLandmarker.create_from_options(opts)

            try:
                _cache["lm"] = make(Deleg.GPU if use_gpu else Deleg.CPU)
                _cache["dev"] = "GPU" if use_gpu else "CPU"
            except Exception as e:
                if use_gpu:
                    print(f"[MediaPipe] GPU delegate unavailable ({e}); using CPU")
                    _cache["lm"] = make(Deleg.CPU); _cache["dev"] = "CPU"
                else:
                    raise
            _cache["mp"] = mp
        mp = _cache["mp"]
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = _cache["lm"].detect(mp_image)
        if not res.pose_landmarks:
            return _stamp(img, "no pose detected"), False
        out = img.copy()
        _draw_mp(out, res.pose_landmarks)
        return out, True
    except ImportError:
        return _stamp(img, "MediaPipe not installed"), False
    except Exception as e:
        return _stamp(img, f"MediaPipe error: {e}"), False


def run_rtmpose(img, device, _cache={}):
    try:
        from rtmlib import Wholebody, draw_skeleton
        if "w" not in _cache:
            _cache["w"] = Wholebody(mode="balanced", backend="onnxruntime",
                                    device=device)
            _cache["draw"] = draw_skeleton
        kpts, scores = _cache["w"](img)
        if kpts is None or len(kpts) == 0:
            return _stamp(img, "no pose detected"), False
        w = img.shape[1]
        r, lw = max(5, w // 100), max(3, w // 360)
        try:
            out = _cache["draw"](img.copy(), kpts, scores, kpt_thr=0.3,
                                 radius=r, line_width=lw)
        except TypeError:
            out = _cache["draw"](img.copy(), kpts, scores, kpt_thr=0.3)
        return out, True
    except ImportError:
        return _stamp(img, "rtmlib/RTMPose not installed"), False
    except Exception as e:
        return _stamp(img, f"RTMPose error: {e}"), False


def _stamp(img, text):
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 34), (40, 40, 40), -1)
    cv2.putText(out, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 255), 1, cv2.LINE_AA)
    return out


# ----------------------------- compositing ----------------------------------
def fit_w(img_bgr, w):
    """Resize a BGR image to width w, preserving aspect ratio (no padding)."""
    ih, iw = img_bgr.shape[:2]
    nh = max(1, round(ih * w / iw))
    return cv2.resize(img_bgr, (w, nh), interpolation=cv2.INTER_AREA)


def build_grid(cells, col_titles, row_titles, cell_w=430):
    """cells[row][col] = BGR image -> labelled grid (PIL Image).
    Each ROW takes the natural height of its crop at width cell_w, so short
    crops (lower body / feet only) get short cells instead of empty space."""
    from PIL import Image, ImageDraw, ImageFont
    pad, lmar, tmar = 10, 150, 46
    nrow, ncol = len(cells), len(cells[0])

    resized = [[fit_w(c, cell_w) for c in row] for row in cells]
    row_h = [max(im.shape[0] for im in row) for im, row in zip(resized, resized)]

    W = lmar + ncol * cell_w + (ncol + 1) * pad
    H = tmar + sum(row_h) + (nrow + 1) * pad
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)

    def font(sz, bold=False):
        for p in ([os.path.expanduser("~/.fonts/InstrumentSans-Bold.ttf")] if bold
                  else [os.path.expanduser("~/.fonts/InstrumentSans-Regular.ttf")]) + \
                 ["/usr/share/fonts/truetype/dejavu/DejaVuSans"
                  + ("-Bold" if bold else "") + ".ttf"]:
            if os.path.exists(p):
                return ImageFont.truetype(p, sz)
        return ImageFont.load_default()

    for c, title in enumerate(col_titles):
        x = lmar + pad + c * (cell_w + pad) + cell_w // 2
        w = draw.textlength(title, font=font(20, True))
        draw.text((x - w / 2, 12), title, fill="#1E2630", font=font(20, True))

    y = tmar + pad
    for r in range(nrow):
        rh = row_h[r]
        draw.text((12, y + rh // 2 - 12), row_titles[r], fill="#1E2630", font=font(18, True))
        for c in range(ncol):
            x = lmar + pad + c * (cell_w + pad)
            tile = resized[r][c]
            oy = y + (rh - tile.shape[0]) // 2          # center if a cell is shorter
            canvas.paste(Image.fromarray(cv2.cvtColor(tile, cv2.COLOR_BGR2RGB)), (x, oy))
            draw.rectangle([x, y, x + cell_w, y + rh], outline="#C9CED4", width=1)
        y += rh + pad
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="one full-body image")
    ap.add_argument("--model", required=True, help="your YOLO foot .pt")
    ap.add_argument("--hip-frac", type=float, default=0.45,
                    help="top of the 'lower body' crop, as a fraction of height")
    ap.add_argument("--feet-frac", type=float, default=0.80,
                    help="top of the 'feet only' crop, as a fraction of height")
    ap.add_argument("--device", default="cpu", help="cpu or cuda (RTMPose)")
    ap.add_argument("--mp-model", default="pose_landmarker.task",
                    help="MediaPipe Tasks pose model (.task)")
    ap.add_argument("--time-runs", type=int, default=0,
                    help="if >0, benchmark each detector N times on the full image")
    ap.add_argument("--out", default="detector_comparison_grid")
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit("could not read image: " + args.image)
    H = img.shape[0]
    crops = [
        ("Full body",  img),
        ("Lower body", img[int(args.hip_frac * H):]),
        ("Feet only",  img[int(args.feet_frac * H):]),
    ]

    use_gpu = args.device.startswith("cuda")
    cells, row_titles = [], []
    for name, crop in crops:
        mp_img, _ = run_mediapipe(crop, args.mp_model, use_gpu)
        rt_img, _ = run_rtmpose(crop, args.device)
        yo_img, _ = run_yolo(crop, args.model, args.device)
        cells.append([mp_img, rt_img, yo_img])
        row_titles.append(name)

    if args.time_runs > 0:
        import time
        full = crops[0][1]
        ug = args.device.startswith("cuda")
        bench = [("MediaPipe Pose",     lambda im: run_mediapipe(im, args.mp_model, ug)),
                 ("RTMPose WholeBody",  lambda im: run_rtmpose(im, args.device)),
                 ("Ours (foot-only)",   lambda im: run_yolo(im, args.model, args.device))]
        print("\n=== throughput on the full-body image "
              "(inference + overlay; backends/devices differ) ===")
        print(f"{'detector':22s}{'ms/frame':>12s}{'fps':>9s}")
        for name, fn in bench:
            fn(full)                                  # warmup / lazy-load model
            t0 = time.perf_counter()
            for _ in range(args.time_runs):
                fn(full)
            ms = (time.perf_counter() - t0) / args.time_runs * 1000.0
            print(f"{name:22s}{ms:12.1f}{1000.0/ms:9.1f}")
        # report the device each detector ACTUALLY used
        try:
            import onnxruntime as _ort
            ort_gpu = "CUDAExecutionProvider" in _ort.get_available_providers()
        except Exception:
            ort_gpu = False
        mp_dev = run_mediapipe.__defaults__[2].get("dev", "CPU")   # set in run_mediapipe
        rt_dev = "GPU" if (args.device.startswith("cuda") and ort_gpu) else "CPU"
        print(f"requested device='{args.device}'  |  ACTUAL device used:")
        print(f"  MediaPipe : {mp_dev}")
        print(f"  RTMPose   : {rt_dev}"
              f"  (onnxruntime-gpu {'present' if ort_gpu else 'absent -> CPU'})")
        print(f"  Ours      : {args.device} (PyTorch)")
        if not (mp_dev == "GPU" and rt_dev == "GPU" and args.device.startswith("cuda")):
            print("  -> NOT all on GPU. For a fair table, either install onnxruntime-gpu "
                  "/ fix the MediaPipe GPU delegate, or run ALL on --device cpu.")

    grid = build_grid(cells,
                      ["MediaPipe Pose", "RTMPose WholeBody", "Ours (foot-only)"],
                      row_titles)
    grid.save(args.out + ".png")
    print("wrote", args.out + ".png")
    try:
        grid.save(args.out + ".pdf")
        print("wrote", args.out + ".pdf")
    except Exception as e:
        print(f"[pdf skipped] {e}\n  PNG is the deliverable (the paper uses .png). "
              f"For a PDF too, either reinstall Pillow with JPEG support\n"
              f"  (pip install --force-reinstall pillow), or convert the PNG: "
              f"img2pdf {args.out}.png -o {args.out}.pdf")


if __name__ == "__main__":
    main()