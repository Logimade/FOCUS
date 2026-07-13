# runtime_eval.py
#
# Measures runtime of the complete pipeline:
#   video decode -> YOLO -> extract keypoints -> One Euro smoothing -> recognizer
#
# It reports synthetic and real datasets separately if you run it separately.
#
# Usage:
#   python runtime_eval.py --model best.pt --source ./synthetic_clips --out runtime_synth.csv
#   python runtime_eval.py --model best.pt --source ./clips_real --out runtime_real.csv

import argparse
import glob
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from ultralytics import YOLO

from demo import extract_keypoints
from euro_smoothing import KeypointSmoother
from gestures import FootGestureRecognizer, LEFT_NAMES, RIGHT_NAMES, DEFAULT_CFG


def list_videos(source):
    source = Path(source)

    if source.is_file():
        return [source]

    exts = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

    return sorted(
        p for p in source.rglob("*")
        if p.suffix.lower() in exts
    )


def percentile(values, q):
    if len(values) == 0:
        return np.nan
    return float(np.percentile(values, q))


def process_video(video_path, model, args):
    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    input_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    smoother = KeypointSmoother(
        fps=input_fps,
        min_conf=args.min_conf,
        max_hold=3,
    )

    cfg = dict(DEFAULT_CFG)

    if args.no_move:
        cfg["move_frac"] = 1e9

    left = FootGestureRecognizer(
        LEFT_NAMES,
        fps=input_fps,
        min_conf=args.min_conf,
        cfg=cfg,
    )

    right = FootGestureRecognizer(
        RIGHT_NAMES,
        fps=input_fps,
        min_conf=args.min_conf,
        cfg=cfg,
    )

    detector_times = []
    keypoint_times = []
    smoother_times = []
    recognizer_times = []
    full_frame_times = []

    n_frames = 0
    n_events = 0

    # warm-up, useful for CUDA
    ok, warm_frame = cap.read()
    if ok:
        _ = model(
            warm_frame,
            verbose=False,
            device=args.device,
            imgsz=args.imgsz,
        )[0]
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    total_start = time.perf_counter()

    while True:
        ok, frame = cap.read()

        if not ok:
            break

        if args.resize_width > 0:
            h, w = frame.shape[:2]
            new_w = args.resize_width
            new_h = int(round(h * new_w / w))
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

        frame_start = time.perf_counter()

        t0 = time.perf_counter()
        result = model(
            frame,
            verbose=False,
            device=args.device,
            imgsz=args.imgsz,
        )[0]
        t1 = time.perf_counter()

        raw = extract_keypoints(result)
        t2 = time.perf_counter()

        dt = 1.0 / input_fps
        kpts = smoother.update(raw, dt=dt)
        t3 = time.perf_counter()

        ev_l = left.step(kpts, n_frames)
        ev_r = right.step(kpts, n_frames)

        if ev_l:
            n_events += 1
        if ev_r:
            n_events += 1

        t4 = time.perf_counter()

        detector_times.append((t1 - t0) * 1000.0)
        keypoint_times.append((t2 - t1) * 1000.0)
        smoother_times.append((t3 - t2) * 1000.0)
        recognizer_times.append((t4 - t3) * 1000.0)
        full_frame_times.append((t4 - frame_start) * 1000.0)

        n_frames += 1

    total_end = time.perf_counter()
    cap.release()

    elapsed = total_end - total_start
    measured_fps = n_frames / elapsed if elapsed > 0 else np.nan

    full_ms = np.array(full_frame_times, dtype=np.float64)

    return {
        "video": str(video_path),
        "frames": n_frames,
        "source_fps": input_fps,
        "width": width,
        "height": height,
        "resize_width": args.resize_width,
        "imgsz": args.imgsz,
        "events": n_events,

        "detector_ms_mean": float(np.mean(detector_times)),
        "keypoint_extract_ms_mean": float(np.mean(keypoint_times)),
        "smoothing_ms_mean": float(np.mean(smoother_times)),
        "recognizer_ms_mean": float(np.mean(recognizer_times)),
        "postprocess_ms_mean": float(np.mean(keypoint_times) + np.mean(smoother_times) + np.mean(recognizer_times)),

        "full_pipeline_ms_mean": float(np.mean(full_frame_times)),
        "full_pipeline_ms_p50": percentile(full_ms, 50),
        "full_pipeline_ms_p95": percentile(full_ms, 95),
        "measured_fps": measured_fps,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", default="runtime_results.csv")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--min-conf", type=float, default=0.25)
    parser.add_argument("--resize-width", type=int, default=0,
                        help="Optional resize before inference. 0 keeps original resolution.")
    parser.add_argument("--no-move", action="store_true",
                        help="Disable move detection for tap/swipe/none runtime evaluation.")
    args = parser.parse_args()

    videos = list_videos(args.source)

    if not videos:
        raise RuntimeError(f"No videos found in {args.source}")

    model = YOLO(args.model)

    rows = []

    for i, video in enumerate(videos, start=1):
        print(f"[{i}/{len(videos)}] {video}")
        row = process_video(video, model, args)
        rows.append(row)

        print(
            f"  {row['width']}x{row['height']} | "
            f"{row['full_pipeline_ms_mean']:.2f} ms/frame | "
            f"{row['measured_fps']:.2f} FPS"
        )

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)

    summary = df.mean(numeric_only=True)

    print("\n=== Runtime summary ===")
    print(f"Videos: {len(df)}")
    print(f"Mean resolution: {summary['width']:.0f} x {summary['height']:.0f}")
    print(f"Detector: {summary['detector_ms_mean']:.2f} ms/frame")
    print(f"Post-processing: {summary['postprocess_ms_mean']:.3f} ms/frame")
    print(f"Full pipeline: {summary['full_pipeline_ms_mean']:.2f} ms/frame")
    print(f"P95 full pipeline: {summary['full_pipeline_ms_p95']:.2f} ms/frame")
    print(f"Measured FPS: {summary['measured_fps']:.2f}")

    print("\nLaTeX row:")
    print(
        f"Dataset & {summary['width']:.0f}$\\times${summary['height']:.0f} & "
        f"{summary['detector_ms_mean']:.2f} & "
        f"{summary['postprocess_ms_mean']:.3f} & "
        f"{summary['full_pipeline_ms_mean']:.2f} & "
        f"{summary['measured_fps']:.2f} \\\\"
    )


if __name__ == "__main__":
    main()
