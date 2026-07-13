# keypoint_availability_eval.py
#
# Computes how often the detector provides enough keypoints for the recognizer.
#
# A frame is considered usable when, for a given foot:
#   ankle exists with conf >= min_conf
#   heel exists with conf >= min_conf
#   at least one toe keypoint exists with conf >= min_conf
#
# This matches the geometric requirement used by gestures.py:
# ankle + heel + at least one toe are needed to compute the foot vector/base.
#
# Usage:
#   python keypoint_availability_eval.py \
#       --clips ./synthetic/tap ./synthetic/swipe_left ./synthetic/swipe_right ./synthetic/idle \
#       --cache ./kpcache \
#       --out ./availability_synthetic.csv
#
#   python keypoint_availability_eval.py \
#       --clips ./clips_real \
#       --cache ./real_kpcache \
#       --out ./availability_real.csv

import argparse
import glob
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from gestures import LEFT_NAMES, RIGHT_NAMES


def label_from_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"_\d+$", "", stem)


def clip_key(path):
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    stem = os.path.splitext(os.path.basename(path))[0]
    return parent + "__" + stem


def list_clips(folders):
    clips = []
    for folder in folders:
        clips += sorted(glob.glob(os.path.join(folder, "*.mp4")))
    return clips


def has_valid_point(kpts, name, min_conf):
    if not kpts or name not in kpts:
        return False

    p = kpts[name]

    if p is None:
        return False

    if p.get("x") is None or p.get("y") is None:
        return False

    return p.get("conf", 0.0) >= min_conf


def foot_is_usable(kpts, names, min_conf):
    ankle_ok = has_valid_point(kpts, names["ankle"], min_conf)
    heel_ok = has_valid_point(kpts, names["heel"], min_conf)

    big_ok = has_valid_point(kpts, names["big"], min_conf)
    small_ok = has_valid_point(kpts, names["small"], min_conf)

    toe_ok = big_ok or small_ok

    return ankle_ok and heel_ok and toe_ok


def dropout_lengths(usable):
    """
    Returns lengths of consecutive unusable-frame runs.
    Example:
        usable = [1, 1, 0, 0, 1, 0]
        dropouts = [2, 1]
    """
    lengths = []
    current = 0

    for u in usable:
        if u:
            if current > 0:
                lengths.append(current)
                current = 0
        else:
            current += 1

    if current > 0:
        lengths.append(current)

    return lengths


def summarize_foot(usable):
    usable = np.asarray(usable, dtype=bool)

    total_frames = len(usable)

    if total_frames == 0:
        return {
            "total_frames": 0,
            "usable_frames": 0,
            "usable_percent": np.nan,
            "mean_dropout_frames": np.nan,
            "max_dropout_frames": np.nan,
            "has_dropout": False,
            "num_dropout_runs": 0,
        }

    drops = dropout_lengths(usable)

    return {
        "total_frames": int(total_frames),
        "usable_frames": int(usable.sum()),
        "usable_percent": float(100.0 * usable.mean()),
        "mean_dropout_frames": float(np.mean(drops)) if drops else 0.0,
        "max_dropout_frames": int(np.max(drops)) if drops else 0,
        "has_dropout": bool(len(drops) > 0),
        "num_dropout_runs": int(len(drops)),
    }


def process_clip(cache_path, min_conf):
    with open(cache_path, "r") as f:
        d = json.load(f)

    frames = d["frames"]

    usable_left = []
    usable_right = []

    for kpts in frames:
        usable_left.append(foot_is_usable(kpts, LEFT_NAMES, min_conf))
        usable_right.append(foot_is_usable(kpts, RIGHT_NAMES, min_conf))

    left_summary = summarize_foot(usable_left)
    right_summary = summarize_foot(usable_right)

    # Best foot = the foot with the higher usable percentage.
    # This is useful for clips where only one foot is clearly visible.
    if left_summary["usable_percent"] >= right_summary["usable_percent"]:
        best_side = "left"
        best_summary = left_summary
    else:
        best_side = "right"
        best_summary = right_summary

    # Either foot = at least one foot is usable in the frame.
    either_usable = np.asarray(usable_left, dtype=bool) | np.asarray(usable_right, dtype=bool)
    either_summary = summarize_foot(either_usable)

    return {
        "left": left_summary,
        "right": right_summary,
        "best_side": best_side,
        "best": best_summary,
        "either": either_summary,
    }


def aggregate(rows, mode):
    """
    mode:
      "best"   -> selected foot per clip
      "either" -> at least one foot usable
    """
    df = pd.DataFrame(rows)

    usable_col = f"{mode}_usable_percent"
    mean_col = f"{mode}_mean_dropout_frames"
    max_col = f"{mode}_max_dropout_frames"
    has_col = f"{mode}_has_dropout"

    return {
        "usable_frames_percent": float(df[usable_col].mean()),
        "mean_dropout_frames": float(df[mean_col].mean()),
        "max_dropout_frames": int(df[max_col].max()),
        "clips_with_dropout_percent": float(100.0 * df[has_col].mean()),
        "num_clips": int(len(df)),
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--clips", required=True, nargs="+", help="folders with .mp4 clips")
    parser.add_argument("--cache", required=True, help="keypoint cache generated by evaluate.py")
    parser.add_argument("--out", default="keypoint_availability.csv")
    parser.add_argument("--min-conf", type=float, default=0.25)
    parser.add_argument(
        "--mode",
        choices=["best", "either"],
        default="best",
        help=(
            "best = report the most visible foot per clip. "
            "either = report whether at least one foot is usable per frame."
        ),
    )

    args = parser.parse_args()

    clips = list_clips(args.clips)

    if not clips:
        raise SystemExit("No .mp4 clips found.")

    rows = []

    for clip_path in clips:
        cache_path = Path(args.cache) / f"{clip_key(clip_path)}.json"

        if not cache_path.exists():
            print(f"Missing cache for {clip_path}")
            continue

        result = process_clip(cache_path, args.min_conf)

        row = {
            "clip": str(clip_path),
            "label": label_from_name(clip_path),
            "best_side": result["best_side"],

            "left_total_frames": result["left"]["total_frames"],
            "left_usable_percent": result["left"]["usable_percent"],
            "left_mean_dropout_frames": result["left"]["mean_dropout_frames"],
            "left_max_dropout_frames": result["left"]["max_dropout_frames"],
            "left_has_dropout": result["left"]["has_dropout"],
            "left_num_dropout_runs": result["left"]["num_dropout_runs"],

            "right_total_frames": result["right"]["total_frames"],
            "right_usable_percent": result["right"]["usable_percent"],
            "right_mean_dropout_frames": result["right"]["mean_dropout_frames"],
            "right_max_dropout_frames": result["right"]["max_dropout_frames"],
            "right_has_dropout": result["right"]["has_dropout"],
            "right_num_dropout_runs": result["right"]["num_dropout_runs"],

            "best_total_frames": result["best"]["total_frames"],
            "best_usable_percent": result["best"]["usable_percent"],
            "best_mean_dropout_frames": result["best"]["mean_dropout_frames"],
            "best_max_dropout_frames": result["best"]["max_dropout_frames"],
            "best_has_dropout": result["best"]["has_dropout"],
            "best_num_dropout_runs": result["best"]["num_dropout_runs"],

            "either_total_frames": result["either"]["total_frames"],
            "either_usable_percent": result["either"]["usable_percent"],
            "either_mean_dropout_frames": result["either"]["mean_dropout_frames"],
            "either_max_dropout_frames": result["either"]["max_dropout_frames"],
            "either_has_dropout": result["either"]["has_dropout"],
            "either_num_dropout_runs": result["either"]["num_dropout_runs"],
        }

        rows.append(row)

    if not rows:
        raise SystemExit("No valid clips processed.")

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)

    summary = aggregate(rows, args.mode)

    print(f"Saved: {args.out}")

    print("\n=== Keypoint availability summary ===")
    print(f"Mode: {args.mode}")
    print(f"Clips: {summary['num_clips']}")
    print(f"Usable frames: {summary['usable_frames_percent']:.2f}%")
    print(f"Mean dropout: {summary['mean_dropout_frames']:.2f} frames")
    print(f"Max dropout: {summary['max_dropout_frames']} frames")
    print(f"Clips with dropout: {summary['clips_with_dropout_percent']:.2f}%")

    print("\nLaTeX row:")
    print(
        f"Dataset & "
        f"{summary['usable_frames_percent']:.2f} & "
        f"{summary['mean_dropout_frames']:.2f} & "
        f"{summary['max_dropout_frames']} & "
        f"{summary['clips_with_dropout_percent']:.2f} \\\\"
    )


if __name__ == "__main__":
    main()
