# jitter_eval_from_cache.py
#
# Computes raw vs One-Euro-filtered jitter metrics using the keypoint cache
# created by evaluate.py.
#
# It matches the current gestures.py logic:
#   swipe signal = horizontal toe-center motion relative to neutral, normalized by L0
#   tap signal   = vertical toe-center motion relative to neutral, normalized by L0
#
# Usage:
#   python jitter_eval_from_cache.py \
#       --clips ./synthetic/tap ./synthetic/swipe_left ./synthetic/swipe_right ./synthetic/idle \
#       --cache ./kpcache \
#       --out ./jitter_outputs \
#       --plot-label tap
#
# Outputs:
#   jitter_outputs/jitter_per_clip.csv
#   jitter_outputs/jitter_summary.csv
#   jitter_outputs/filter_comparison.png

import argparse
import glob
import json
import os
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from euro_smoothing import KeypointSmoother
from gestures import DEFAULT_CFG, LEFT_NAMES, RIGHT_NAMES


# ---------------------------------------------------------------------
# Same naming convention as evaluate.py
# ---------------------------------------------------------------------
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


# ---------------------------------------------------------------------
# Geometry matching current gestures.py
# ---------------------------------------------------------------------
def _pt(kpts, name, min_conf):
    if not kpts or name not in kpts:
        return None
    p = kpts[name]
    if p.get("conf", 0.0) < min_conf:
        return None
    return np.array([p["x"], p["y"]], dtype=np.float64)


def foot_frame(kpts, names, min_conf):
    big = _pt(kpts, names["big"], min_conf)
    small = _pt(kpts, names["small"], min_conf)
    heel = _pt(kpts, names["heel"], min_conf)
    ankle = _pt(kpts, names["ankle"], min_conf)

    if ankle is None or heel is None:
        return None

    toes = [p for p in (big, small) if p is not None]
    if not toes:
        return None

    toe_center = np.mean(toes, axis=0)
    v = toe_center - ankle
    L = max(float(np.linalg.norm(v)), 1e-6)
    base = np.mean([heel, ankle], axis=0)

    return {
        "v": v,
        "L": L,
        "toe_center": toe_center,
        "ankle": ankle,
        "heel": heel,
        "base": base,
    }


def build_feature_series(frames, names, fps, min_conf, use_filter, max_hold):
    if use_filter:
        smoother = KeypointSmoother(
            fps=fps,
            min_conf=min_conf,
            max_hold=max_hold,
        )
    else:
        smoother = None

    dt = 1.0 / fps
    feats = []

    for raw in frames:
        if use_filter:
            kpts = smoother.update(raw, dt=dt)
        else:
            kpts = raw

        feats.append(foot_frame(kpts, names, min_conf))

    return feats


def first_valid_neutral(feats, neutral_frames):
    valid = [f for f in feats[:neutral_frames] if f is not None]

    if len(valid) < 5:
        return None

    return {
        "v0": np.median([f["v"] for f in valid], axis=0),
        "L0": max(float(np.median([f["L"] for f in valid])), 1e-6),
        "toe0": np.median([f["toe_center"] for f in valid], axis=0),
        "ankle0": np.median([f["ankle"] for f in valid], axis=0),
        "base0": np.median([f["base"] for f in valid], axis=0),
    }


def to_signal_dataframe(feats, neutral, fps):
    rows = []

    for i, f in enumerate(feats):
        row = {
            "frame": i,
            "time": i / fps,
            "valid": f is not None,
            "swipe_signal": np.nan,
            "tap_signal": np.nan,
            "toe_disp_px": np.nan,
            "ankle_disp_px": np.nan,
            "base_disp_norm": np.nan,
        }

        if f is not None and neutral is not None:
            L0 = neutral["L0"]
            dV = (f["v"] - neutral["v0"]) / L0

            # Current gestures.py logic:
            # horizontal component -> swipe
            # vertical component -> tap
            row["swipe_signal"] = float(dV[0])
            row["tap_signal"] = float(dV[1])

            row["toe_disp_px"] = float(np.linalg.norm(f["toe_center"] - neutral["toe0"]))
            row["ankle_disp_px"] = float(np.linalg.norm(f["ankle"] - neutral["ankle0"]))
            row["base_disp_norm"] = float(np.linalg.norm((f["base"] - neutral["base0"]) / L0))

        rows.append(row)

    return pd.DataFrame(rows)


def rms(values):
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 5:
        return np.nan
    return float(np.sqrt(np.mean(arr ** 2)))


def std(values):
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 5:
        return np.nan
    return float(np.std(arr))


def compute_neutral_jitter(df, neutral_frames):
    n = df.iloc[:neutral_frames].copy()

    return {
        "valid_neutral_frames": int(n["valid"].sum()),
        "swipe_signal_jitter": std(n["swipe_signal"].values),
        "tap_signal_jitter": std(n["tap_signal"].values),
        "toe_center_jitter_px": rms(n["toe_disp_px"].values),
        "ankle_jitter_px": rms(n["ankle_disp_px"].values),
        "base_jitter_norm": std(n["base_disp_norm"].values),
    }


def improvement_percent(raw, filt):
    if not np.isfinite(raw) or raw <= 0 or not np.isfinite(filt):
        return np.nan
    return 100.0 * (raw - filt) / raw


# ---------------------------------------------------------------------
# Clip processing
# ---------------------------------------------------------------------
def process_cached_clip(cache_path, fps_default, min_conf, max_hold, neutral_sec):
    with open(cache_path, "r") as f:
        d = json.load(f)

    fps = float(d.get("fps", fps_default) or fps_default)
    frames = d["frames"]
    neutral_frames = int(round(neutral_sec * fps))

    results = {}

    for side, names in [("left", LEFT_NAMES), ("right", RIGHT_NAMES)]:
        raw_feats = build_feature_series(
            frames=frames,
            names=names,
            fps=fps,
            min_conf=min_conf,
            use_filter=False,
            max_hold=max_hold,
        )

        filt_feats = build_feature_series(
            frames=frames,
            names=names,
            fps=fps,
            min_conf=min_conf,
            use_filter=True,
            max_hold=max_hold,
        )

        raw_neutral = first_valid_neutral(raw_feats, neutral_frames)
        filt_neutral = first_valid_neutral(filt_feats, neutral_frames)

        if raw_neutral is None or filt_neutral is None:
            continue

        raw_df = to_signal_dataframe(raw_feats, raw_neutral, fps)
        filt_df = to_signal_dataframe(filt_feats, filt_neutral, fps)

        raw_j = compute_neutral_jitter(raw_df, neutral_frames)
        filt_j = compute_neutral_jitter(filt_df, neutral_frames)

        results[side] = {
            "fps": fps,
            "neutral_frames": neutral_frames,
            "raw_df": raw_df,
            "filtered_df": filt_df,
            "raw_jitter": raw_j,
            "filtered_jitter": filt_j,
        }

    return results


def choose_best_side(processed):
    # Pick the side with more valid neutral frames after filtering.
    best_side = None
    best_count = -1

    for side, r in processed.items():
        count = r["filtered_jitter"]["valid_neutral_frames"]
        if count > best_count:
            best_count = count
            best_side = side

    return best_side


# ---------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------
def make_filter_plot(raw_df, filt_df, out_path, cfg, xlim=None):
    t = raw_df["time"].values

    fig, axes = plt.subplots(2, 1, figsize=(9.2, 5.4), sharex=True)

    axes[0].plot(t, raw_df["swipe_signal"].values, label="Raw")
    axes[0].plot(t, filt_df["swipe_signal"].values, label="Filtered")
    axes[0].axhline(cfg["swipe_frac"], linestyle="--", linewidth=1)
    axes[0].axhline(-cfg["swipe_frac"], linestyle="--", linewidth=1)
    axes[0].set_ylabel("Swipe signal")
    axes[0].grid(True, linewidth=0.4, alpha=0.5)
    axes[0].legend(loc="upper right")

    axes[1].plot(t, raw_df["tap_signal"].values, label="Raw")
    axes[1].plot(t, filt_df["tap_signal"].values, label="Filtered")
    axes[1].axhline(cfg["tap_frac"], linestyle="--", linewidth=1)
    axes[1].axhline(-cfg["tap_frac"], linestyle="--", linewidth=1)
    axes[1].set_xlabel("Time")
    axes[1].set_xticks([])
    axes[1].set_ylabel("Tap signal")
    axes[1].grid(True, linewidth=0.4, alpha=0.5)
    axes[1].legend(loc="upper right")

    if xlim is not None:
        axes[0].set_xlim(xlim)
        axes[1].set_xlim(xlim)

    fig.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def print_latex_values(summary):
    s = summary.set_index("metric")["mean"]

    def g(name):
        return float(s.get(name, np.nan))

    print("\nLaTeX table rows:")
    print("----------------------------------------")
    print(
        f"Swipe signal & "
        f"{g('raw_swipe_signal_jitter'):.4f} & "
        f"{g('filtered_swipe_signal_jitter'):.4f} \\\\"
    )
    print(
        f"Tap signal & "
        f"{g('raw_tap_signal_jitter'):.4f} & "
        f"{g('filtered_tap_signal_jitter'):.4f} \\\\"
    )
    print(
        f"Toe center displacement, pixels & "
        f"{g('raw_toe_center_jitter_px'):.3f} & "
        f"{g('filtered_toe_center_jitter_px'):.3f} \\\\"
    )
    print(
        f"Ankle displacement, pixels & "
        f"{g('raw_ankle_jitter_px'):.3f} & "
        f"{g('filtered_ankle_jitter_px'):.3f} \\\\"
    )
    print("----------------------------------------")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clips", required=True, nargs="+", help="folders with .mp4 clips")
    parser.add_argument("--cache", required=True, help="keypoint cache generated by evaluate.py")
    parser.add_argument("--out", default="jitter_outputs")
    parser.add_argument("--min-conf", type=float, default=0.25)
    parser.add_argument("--max-hold", type=int, default=3)
    parser.add_argument("--neutral-sec", type=float, default=1.5)
    parser.add_argument("--fps-default", type=float, default=30.0)
    parser.add_argument("--plot-label", default="tap", help="label to use for the example plot")
    parser.add_argument("--plot-index", type=int, default=0, help="which clip of that label to plot")

    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    clips = list_clips(args.clips)
    if not clips:
        raise SystemExit("No .mp4 clips found.")

    rows = []
    plot_candidates = []

    for clip_path in clips:
        label = label_from_name(clip_path)
        cache_path = Path(args.cache) / f"{clip_key(clip_path)}.json"

        if not cache_path.exists():
            print(f"Missing cache for {clip_path}")
            continue

        processed = process_cached_clip(
            cache_path=cache_path,
            fps_default=args.fps_default,
            min_conf=args.min_conf,
            max_hold=args.max_hold,
            neutral_sec=args.neutral_sec,
        )

        side = choose_best_side(processed)
        if side is None:
            continue

        r = processed[side]
        raw = r["raw_jitter"]
        filt = r["filtered_jitter"]

        row = {
            "clip": str(clip_path),
            "label": label,
            "side": side,
            "fps": r["fps"],
            "neutral_frames": r["neutral_frames"],

            "raw_swipe_signal_jitter": raw["swipe_signal_jitter"],
            "filtered_swipe_signal_jitter": filt["swipe_signal_jitter"],
            "swipe_signal_improvement_percent": improvement_percent(
                raw["swipe_signal_jitter"],
                filt["swipe_signal_jitter"],
            ),

            "raw_tap_signal_jitter": raw["tap_signal_jitter"],
            "filtered_tap_signal_jitter": filt["tap_signal_jitter"],
            "tap_signal_improvement_percent": improvement_percent(
                raw["tap_signal_jitter"],
                filt["tap_signal_jitter"],
            ),

            "raw_toe_center_jitter_px": raw["toe_center_jitter_px"],
            "filtered_toe_center_jitter_px": filt["toe_center_jitter_px"],
            "toe_center_improvement_percent": improvement_percent(
                raw["toe_center_jitter_px"],
                filt["toe_center_jitter_px"],
            ),

            "raw_ankle_jitter_px": raw["ankle_jitter_px"],
            "filtered_ankle_jitter_px": filt["ankle_jitter_px"],
            "ankle_improvement_percent": improvement_percent(
                raw["ankle_jitter_px"],
                filt["ankle_jitter_px"],
            ),

            "raw_valid_neutral_frames": raw["valid_neutral_frames"],
            "filtered_valid_neutral_frames": filt["valid_neutral_frames"],
        }

        rows.append(row)

        if label == args.plot_label:
            plot_candidates.append((clip_path, side, r))

    if not rows:
        raise SystemExit("No valid clips were processed.")

    per_clip = pd.DataFrame(rows)

    metric_cols = [
        "raw_swipe_signal_jitter",
        "filtered_swipe_signal_jitter",
        "swipe_signal_improvement_percent",
        "raw_tap_signal_jitter",
        "filtered_tap_signal_jitter",
        "tap_signal_improvement_percent",
        "raw_toe_center_jitter_px",
        "filtered_toe_center_jitter_px",
        "toe_center_improvement_percent",
        "raw_ankle_jitter_px",
        "filtered_ankle_jitter_px",
        "ankle_improvement_percent",
    ]

    summary = (
        per_clip[metric_cols]
        .mean(numeric_only=True)
        .to_frame("mean")
        .reset_index()
        .rename(columns={"index": "metric"})
    )

    per_clip_path = out_dir / "jitter_per_clip.csv"
    summary_path = out_dir / "jitter_summary.csv"

    per_clip.to_csv(per_clip_path, index=False)
    summary.to_csv(summary_path, index=False)

    print(f"Saved {per_clip_path}")
    print(f"Saved {summary_path}")

    print("\nSummary:")
    print(summary.to_string(index=False))

    print_latex_values(summary)

    if plot_candidates:
        idx = min(args.plot_index, len(plot_candidates) - 1)
        clip_path, side, r = plot_candidates[idx]
        print(f"\nUsing plot candidate with label: {args.plot_label}")
    else:
        # Fallback: use the first valid processed clip.
        first_row = per_clip.iloc[0]
        clip_path = first_row["clip"]
        side = first_row["side"]

        cache_path = Path(args.cache) / f"{clip_key(clip_path)}.json"
        processed = process_cached_clip(
            cache_path=cache_path,
            fps_default=args.fps_default,
            min_conf=args.min_conf,
            max_hold=args.max_hold,
            neutral_sec=args.neutral_sec,
        )
        r = processed[side]

        print(f"\nNo plot candidate found for label: {args.plot_label}")
        print("Using first valid clip instead.")

    plot_path = out_dir / "filter_comparison.png"

    make_filter_plot(
        raw_df=r["raw_df"],
        filt_df=r["filtered_df"],
        out_path=plot_path,
        cfg=DEFAULT_CFG,
        xlim=(22, 36),
    )

    print(f"\nSaved plot: {plot_path}")
    print(f"Plot clip: {clip_path}")
    print(f"Plot foot side: {side}")


if __name__ == "__main__":
    main()
