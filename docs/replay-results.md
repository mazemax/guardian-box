# GuardianBox Replay Test Results

This document records the results of running the pose detection and full pipeline on the sample clips.

## Test Matrix

| Clip | Duration | Scenario | Expected Result | Pose Trigger? | VLM Verdict | Final Outcome |
|------|----------|----------|-----------------|---------------|-------------|---------------|
| clip1.mp4 | 44s | Slow collapse (chest pain) | Alert | TBD | TBD | TBD |
| clip2.mp4 | 13s | Fast fall (walker) | Alert | TBD | TBD | TBD |
| clip3.mp4 | 10s | Prayer (sujud) | Stay quiet | TBD | TBD | TBD |
| clip4.mp4 | 7s | Slow collapse (dizzy) | Alert | TBD | TBD | TBD |
| clip5.mp4 | 18s | Normal walking | Stay quiet | TBD | TBD | TBD |

## Clip Descriptions

### clip1.mp4 — Slow Collapse (Chest Pain) — EXPECT ALERT

- **Location:** Kitchen
- **Duration:** ~44 seconds
- **Camera:** Mid-torso height, partial body visible
- **Occlusion:** Large opaque Adobe Stock watermark over torso; legs mostly out of frame or hidden by cabinets

**Timeline:**
- 0:00–0:20: Older man stands at counter, clutching chest as if in cardiac event
- 0:21–0:36: Slowly slides down the cabinets (controlled descent)
- 0:37–end: Sits on floor against cabinets, does not get up

**Expected behavior:**
- No fast_fall trigger (slow descent)
- Slow-onset trigger (low posture + stillness) should fire
- VLM should classify as `fall` or `lying_on_floor_unclear_cause`
- Check-in should occur
- On silence → D8 alert

**Known challenges:**
- Heavy watermark occludes torso keypoints
- Legs often out of frame
- May need lower confidence threshold or upper-body-only features

---

### clip2.mp4 — Fast Fall (Walker) — EXPECT ALERT

- **Location:** Bright living room
- **Duration:** ~13 seconds
- **Camera:** Wide shot, full body visible
- **Occlusion:** Faint Shutterstock watermark; walker partially occludes during fall

**Timeline:**
- 0:00–0:02: Elderly woman with walker, walking
- 0:03: Loses balance
- 0:04–0:06: Drops to knees, collapses onto right side
- 0:07–end: Lies still on floor

**Expected behavior:**
- fast_fall trigger (rapid descent + low posture + stillness)
- VLM should classify as `fall`
- Check-in should occur
- On silence → D8 alert

**Known challenges:**
- Walker creates partial occlusion during the fall moment
- Should still detect full body pose before and after fall

---

### clip3.mp4 — Prayer (Sujud) — EXPECT STAY QUIET

- **Location:** Prayer room with red mats
- **Duration:** ~10 seconds
- **Camera:** Wide shot from behind-left, full body visible

**Timeline:**
- 0:00–0:01: Standing
- 0:01–0:03: Kneeling (ruku' to kneeling transition)
- 0:03–0:05: Sujud (prostration, forehead on floor)
- 0:05–0:10: Sitting on heels

**Expected behavior:**
- May trigger pose detection (rapid descent to sujud position)
- VLM should classify as `prayer_sujud` or `prayer_other_position`
- D1 decision: Stand down silently
- Timeline should show "Prayer detected, stand down"
- **No voice check-in should occur** (prayer must not be interrupted)

**Known challenges:**
- The descent to sujud is fairly quick, may look like fast_fall to pose
- Prayer-specific features (kneel-to-prostration signature, prayer mat detection) help distinguish
- Prayer cycles feature should show 1+ cycles

---

### clip4.mp4 — Slow Collapse (Dizzy) — EXPECT ALERT

- **Location:** Hallway
- **Duration:** ~7 seconds
- **Camera:** Side-on at waist/chest height, full body visible
- **Audio:** Contains off-camera "three, two, one" countdown and "oh" groan at 0:04 (ignore for decision)

**Timeline:**
- 0:00–0:02: Standing/preparing
- 0:02–0:03: Walking
- 0:03–0:04: Hand to head as if dizzy
- 0:04–0:06: Knees buckle, forward collapse
- 0:06–end: Lying prone, motionless

**Expected behavior:**
- Pose trigger (descent + floor-level posture + stillness)
- VLM should classify as `fall` or `lying_on_floor_unclear_cause`
- Check-in should occur
- On silence → D8 alert

**Notes:**
- The audio groan may be noted by STT but should not affect the decision
- This is a forward fall (prone position), different from clip2's sideways fall

---

### clip5.mp4 — Normal Walking — EXPECT STAY QUIET

- **Location:** Hallway (same as clip4)
- **Duration:** ~18 seconds
- **Camera:** Looking down corridor, full body visible

**Timeline:**
- 0:00–0:02: Standing
- 0:02–0:11: Walking away from camera (figure gets smaller with distance)
- 0:11–0:18: Walking back toward camera

**Expected behavior:**
- **No pose trigger** should fire
- Person remains upright throughout
- No VLM call needed
- No check-in, no alert

**Purpose:** False-positive control — verifies that normal walking does not trigger the system.

---

## Pose Feature Analysis

### Metrics Captured Per Frame

| Feature | Description | Trigger Threshold |
|---------|-------------|-------------------|
| torso_angle | Angle of torso from vertical (shoulder→hip) | >60° for low posture |
| head_height_ratio | Head height relative to standing height | <0.4 for floor level |
| hip_descent_ratio | Hip drop relative to standing height | >0.4 in <1s for fast fall |
| stillness_seconds | Duration of low motion energy | >3s (demo) / >10s (home) |
| motion_energy | Keypoint movement over 2s window | <0.1 for stillness |
| prayer_cycles | Low↔upright transitions in 60s | ≥2 suggests prayer |
| rapid_descent | Boolean: hip drop >40% in <1s | True for fast fall |

---

## Results by Clip

### clip1.mp4 Results

```
[To be filled after running pose analysis]

Timeline:
- 00:00.000 | torso: 15° | head: 0.85 | still: 0.0s | motion: 0.42
- ...
- 00:35.000 | TRIGGER: slow_low_posture | torso: 72° | still: 12s
- ...

Final: [PASS/FAIL] - [outcome]
```

### clip2.mp4 Results

```
[To be filled after running pose analysis]

Timeline:
- 00:00.000 | torso: 12° | head: 0.88 | still: 0.0s
- ...
- 00:05.500 | TRIGGER: fast_fall | descent: 0.52 in 0.8s | torso: 85°
- ...

Final: [PASS/FAIL] - [outcome]
```

### clip3.mp4 Results

```
[To be filled after running pose analysis]

Timeline:
- 00:00.000 | torso: 8° | head: 0.90 | prayer_cycles: 0
- ...
- 00:03.500 | torso: 88° (sujud) | prayer_cycles: 1
- ...
- VLM verdict: prayer_sujud (conf: 0.85)
- Decision: D1 - Stand down silently

Final: [PASS/FAIL] - [outcome]
```

### clip4.mp4 Results

```
[To be filled after running pose analysis]

Timeline:
- 00:00.000 | torso: 10° | walking
- ...
- 00:04.200 | TRIGGER: fast_fall | torso: 78° | descent detected
- ...

Final: [PASS/FAIL] - [outcome]
```

### clip5.mp4 Results

```
[To be filled after running pose analysis]

Timeline:
- 00:00.000 | torso: 12° | upright | walking
- ...
- 00:18.000 | No trigger fired

Final: [PASS/FAIL] - No trigger expected, no trigger fired
```

---

## Summary

| Clip | Expected | Actual | Status |
|------|----------|--------|--------|
| clip1 | Alert | TBD | ⏳ |
| clip2 | Alert | TBD | ⏳ |
| clip3 | Quiet (prayer) | TBD | ⏳ |
| clip4 | Alert | TBD | ⏳ |
| clip5 | Quiet (walk) | TBD | ⏳ |

**Pass rate:** TBD / 5

---

## Running the Replay Tests

### On the DGX Spark

```bash
cd ~/guardian-box

# Ensure clips are in place
ls samples/
# Should show: clip1.mp4 clip2.mp4 clip3.mp4 clip4.mp4 clip5.mp4

# Run pose analysis on a single clip
make pose-analyze CLIP=samples/clip1.mp4

# Run full pipeline replay on a clip
make replay CLIP=samples/clip2.mp4

# Run all clips
make replay-all
```

### Interpreting Results

- **PASS (Alert):** Clip triggered detection, went through check-in, and reached ALERTING/AWAITING_ACK state
- **PASS (Quiet):** Clip either didn't trigger, or triggered but VLM correctly classified as prayer/normal activity and stood down
- **FAIL (False Positive):** Clip triggered alert when it shouldn't have
- **FAIL (False Negative):** Clip didn't trigger alert when it should have

---

## Notes on Occlusion and Watermarks

The sample clips contain stock footage watermarks that partially occlude the subjects:

- **clip1:** Large Adobe Stock watermark over torso — significantly impacts keypoint detection
- **clip2:** Faint Shutterstock watermark — minimal impact
- **clip3:** No visible watermark
- **clip4:** No visible watermark
- **clip5:** No visible watermark

For production deployment, watermark-free footage is recommended. The system should still function with partial occlusion, but confidence scores may be lower.

---

## Changelog

- **2026-10-17:** Initial test matrix with 5 clips
- Results to be updated after running on Spark hardware
