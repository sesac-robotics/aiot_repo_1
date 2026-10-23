# ICCAS ST-GCN candidate model

## Imported artifact

```text
models/candidates/iccas_stgcn/best_stgcn.pth
SHA-256: 127f6b416a8a6aaff6cafd06a8b36929cd8af61a12ed324ad7ae3252a88ffb6b
Bytes: 12,546,007
```

## Upstream

```text
Repository: https://github.com/abbosaliboev/ICCAS_2026
Path: fall_iccas/checkpoints/best_stgcn.pth
License reported by GitHub API: MIT
Retrieved: 2026-10-19
```

## Input contract verified from checkpoint and upstream runtime

```text
Input: 30 frames × 17 COCO keypoints × 3 channels
Channels: x, y, confidence
Output: 2 classes (Fall / No-Fall)
Reference training FPS: 19.0
```

`two_stage_config.json` is the upstream companion configuration. Its current values are:

```json
{
  "stage1_threshold": 0.9,
  "rescue_threshold": 0.85,
  "vel_threshold": 0.0,
  "acc_threshold": 0.0,
  "fps": 19.0
}
```

## Integration status

This artifact is **stored only**. It is not connected to `run_fall_detection.py`, has not replaced the current 40×3 LSTM baseline, and has not been run on project videos.

Future integration must preserve per-`track_id` temporal buffers and evaluate the ST-GCN independently against the existing LSTM on the same Fall and Non-Fall videos.
