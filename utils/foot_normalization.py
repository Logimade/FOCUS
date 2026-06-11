import math
from typing import Dict, Optional, Tuple


def euclidean_distance(p1: dict, p2: dict) -> float:
    return math.sqrt(
        (p1["x"] - p2["x"]) ** 2 +
        (p1["y"] - p2["y"]) ** 2
    )


def midpoint(p1: dict, p2: dict) -> dict:
    return {
        "x": (p1["x"] + p2["x"]) / 2.0,
        "y": (p1["y"] + p2["y"]) / 2.0,
    }


def has_required_keypoints(keypoints, required, min_conf=0.3):
    if keypoints is None:
        return False

    for name in required:
        if name not in keypoints:
            return False

        point = keypoints[name]

        if point is None:
            return False

        if "x" not in point or "y" not in point:
            return False

        if point.get("conf", 1.0) < min_conf:
            return False

        # Ultralytics sometimes returns (0, 0) for missing keypoints
        if point["x"] <= 0 and point["y"] <= 0:
            return False

    return True


def add_virtual_toe_centers(keypoints):
    """
    For the 8-keypoint feet model, create:
        left_toe_center
        right_toe_center

    From:
        left_big_toe + left_small_toe
        right_big_toe + right_small_toe
    """

    if keypoints is None:
        return None

    required = [
        "left_big_toe",
        "left_small_toe",
        "right_big_toe",
        "right_small_toe",
    ]

    for name in required:
        if name not in keypoints:
            return None

    keypoints = dict(keypoints)

    keypoints["left_toe_center"] = {
        "x": (keypoints["left_big_toe"]["x"] + keypoints["left_small_toe"]["x"]) / 2.0,
        "y": (keypoints["left_big_toe"]["y"] + keypoints["left_small_toe"]["y"]) / 2.0,
        "conf": min(
            keypoints["left_big_toe"].get("conf", 1.0),
            keypoints["left_small_toe"].get("conf", 1.0),
        ),
    }

    keypoints["right_toe_center"] = {
        "x": (keypoints["right_big_toe"]["x"] + keypoints["right_small_toe"]["x"]) / 2.0,
        "y": (keypoints["right_big_toe"]["y"] + keypoints["right_small_toe"]["y"]) / 2.0,
        "conf": min(
            keypoints["right_big_toe"].get("conf", 1.0),
            keypoints["right_small_toe"].get("conf", 1.0),
        ),
    }

    return keypoints


def normalize_foot_keypoints_8kpt(
    keypoints,
    previous_scale=None,
    smoothing_alpha=0.8,
    min_conf=0.3,
    min_scale=1e-6,
):
    """
    Normalization for your 8-keypoint YOLO foot pose model.

    Keypoints:
        left_big_toe
        left_small_toe
        left_heel
        left_ankle
        right_big_toe
        right_small_toe
        right_heel
        right_ankle

    Uses:
        anchor = midpoint(left_ankle, right_ankle)
        scale  = average heel-to-toe-center length
    """

    if keypoints is None:
        return None, previous_scale, {
            "valid": False,
            "reason": "no_keypoints",
        }

    keypoints = add_virtual_toe_centers(keypoints)

    if keypoints is None:
        return None, previous_scale, {
            "valid": False,
            "reason": "missing_toe_keypoints",
        }

    required_keypoints = [
        "left_ankle",
        "right_ankle",
        "left_heel",
        "right_heel",
        "left_toe_center",
        "right_toe_center",
    ]

    if not has_required_keypoints(keypoints, required_keypoints, min_conf=min_conf):
        return None, previous_scale, {
            "valid": False,
            "reason": "missing_or_low_conf_keypoints",
        }

    left_ankle = keypoints["left_ankle"]
    right_ankle = keypoints["right_ankle"]

    left_heel = keypoints["left_heel"]
    right_heel = keypoints["right_heel"]

    left_toe_center = keypoints["left_toe_center"]
    right_toe_center = keypoints["right_toe_center"]

    anchor = midpoint(left_ankle, right_ankle)

    left_foot_length = euclidean_distance(left_heel, left_toe_center)
    right_foot_length = euclidean_distance(right_heel, right_toe_center)

    current_scale = (left_foot_length + right_foot_length) / 2.0

    if current_scale < min_scale:
        return None, previous_scale, {
            "valid": False,
            "reason": "invalid_current_scale",
            "current_scale": current_scale,
        }

    if previous_scale is not None:
        scale = smoothing_alpha * previous_scale + (1.0 - smoothing_alpha) * current_scale
    else:
        scale = current_scale

    if scale < min_scale:
        return None, previous_scale, {
            "valid": False,
            "reason": "invalid_smoothed_scale",
            "scale": scale,
        }

    normalized = {}

    for name, point in keypoints.items():
        normalized[name] = {
            "x": (point["x"] - anchor["x"]) / scale,
            "y": (point["y"] - anchor["y"]) / scale,
            "conf": point.get("conf", 1.0),
        }

    debug_info = {
        "valid": True,
        "anchor_x": anchor["x"],
        "anchor_y": anchor["y"],
        "left_foot_length": left_foot_length,
        "right_foot_length": right_foot_length,
        "current_scale": current_scale,
        "scale": scale,
    }

    return normalized, scale, debug_info