# FOCUS

Foot Gesture Recognition is a computer vision project focused on recognizing **dynamic foot gestures** using camera-based sensing. 
The goal is to move beyond simple keypoint detection and address **temporal motion understanding**, enabling reliable gesture recognition for **HCI, XR, accessibility, and smart environments**.

---

## ✨ Motivation

While several frameworks can detect **foot keypoints**, very few address the **gesture recognition problem itself**, which involves:
- Temporal modeling of motion
- Robustness to viewpoint and scale changes
- Handling partial occlusions
- Distinguishing similar movements with different intent

This repository explores **gesture-level understanding**, not just pose estimation.

---

## 🎯 Objectives

- Detect and track foot keypoints using RGB or RGB-D cameras 
- Model **temporal foot motion** for gesture recognition 
- Support **dynamic gestures** (e.g. swipe, tap, hold, rotate) 
- Be adaptable to different sensors and environments 

---

## 🧠 Core Idea

The pipeline is structured as:

## 🔄 Processing Pipeline

```mermaid
flowchart TD
    A["Input (RGB or RGB-D)"] --> B["Foot Keypoint Detection"]
    B --> C["Temporal Feature Extraction"]
    C --> D["Gesture Recognition Network"]
    D --> E["Gesture Label / Event"]
    
```md

## 📷 Sensors

The system is designed to work with:
- **RGB cameras**
- **RGB-D cameras** (preferred for depth-aware motion understanding)

Depth information helps improve:
- Scale consistency
- Z-axis motion
- Occlusion handling

## 🧩 Architecture (Planned)

### Keypoint Detection
- MediaPipe or custom CNN
- Optional depth refinement

### Temporal Modeling
- LSTM / GRU
- Temporal CNN
- Transformer-based sequence models

### Gesture Classification
- Sequence-to-label classification
- Online (real-time) and offline modes
