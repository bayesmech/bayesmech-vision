# DA3 size and resolution scaling on MPI Sintel

Run date: 2026-07-20

## Protocol

- Hardware: NVIDIA GeForce RTX 3060 Laptop GPU (6 GB), PyTorch 2.3.1+cu121.
- Data: all 23 MPI Sintel training scenes, final render pass, with five uniformly spaced frames per scene (115 RGB/depth pairs total).
- Input: each five-frame scene is inferred jointly. The 504 setting produces 504 x 210 tensors for these images; the 252 setting produces 252 x 112 tensors.
- Checkpoints: official `depth-anything/DA3-SMALL`, `DA3-BASE`, and refreshed `DA3-LARGE-1.1` weights.
- Depth alignment: one median scale per scene, with no shift. Valid ground truth is in `(0, 80)` metres.
- Accuracy aggregation: metrics are computed over valid pixels for each scene, then summarized as the unweighted mean and sample standard deviation across 23 scenes.
- Latency: median end-to-end time per frame after model loading and one warm-up scene. It includes image decoding/preprocessing, GPU inference, synchronization, and conversion/postprocessing; model loading and downloading are excluded.
- GPU memory: mean per-scene peak allocated bytes, including loaded model parameters and inference allocations but excluding the CUDA context.

## Results

| Checkpoint | Loaded parameters | Process size | MAE, m (mean +/- SD) | RMSE, m (mean +/- SD) | AbsRel (mean +/- SD) | delta1 (mean +/- SD) | Median latency | Peak GPU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DA3-Small | 34.30M | 252 | 3.608 +/- 4.071 | 6.877 +/- 7.306 | 45.03% +/- 25.08 pp | 51.30% +/- 21.97 pp | 8.29 ms/frame | 0.405 GB |
| DA3-Small | 34.30M | 504 | 3.798 +/- 6.133 | 7.334 +/- 9.989 | 34.04% +/- 17.50 pp | 55.81% +/- 22.20 pp | 16.50 ms/frame | 0.588 GB |
| DA3-Base | 135.37M | 504 | 3.869 +/- 6.076 | 7.187 +/- 9.030 | 38.14% +/- 23.05 pp | 53.56% +/- 24.26 pp | 29.18 ms/frame | 1.343 GB |
| DA3-Large-1.1 | 410.94M | 504 | 2.967 +/- 4.416 | 6.422 +/- 9.296 | 31.23% +/- 23.45 pp | 62.90% +/- 22.82 pp | 69.81 ms/frame | 3.403 GB |

`delta1` is the percentage of predictions within a factor of 1.25 of ground truth. `pp` means percentage points. The square of each reported SD is the corresponding sample variance.

At fixed 504 resolution, Small is 4.23x faster than Large by median latency and uses 82.7% less peak allocated GPU memory. The cost is +2.81 percentage points AbsRel, -7.09 percentage points delta1, and +0.831 m MAE. Base is non-monotonic on this dynamic benchmark: it is slower and less accurate than Small despite having more parameters.

Halving Small's process size from 504 to 252 makes it 1.99x faster and lowers peak allocated GPU memory by 31.2%, but costs +10.99 percentage points AbsRel and -4.51 percentage points delta1. Its MAE happens to improve by 0.190 m because scene-averaged metric MAE and AbsRel weight distance regimes differently; the relative metrics show the resolution loss clearly.

## Reproduction

```bash
python analysis/da3_sintel_scaling.py \
  --model depth-anything/DA3-SMALL \
  --data /path/to/sintel-subset \
  --output /tmp/da3-small-sintel.json \
  --process-res 504
```
