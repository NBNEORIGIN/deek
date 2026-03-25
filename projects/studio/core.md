# CLAW Studio — Core Context
# Version: 1.0

## What this is
CLAW Studio is a local AI media generation workspace.
It uses CogVideoX-2b running on the RTX 3050 GPU to generate
video clips from text prompts — no API, no cost per generation.

## Tools available
- `generate_image` — SDXL-Turbo, ~10–15 seconds, PNG output, 512×512 default
- `generate_video` — CogVideoX-2b, ~8 min per 6s clip, MP4 output

## Hardware
- GPU: NVIDIA GeForce RTX 3050 (8 GB VRAM)
- Model: CogVideoX-2b (fp16 + CPU offload to stay under 8 GB)
- First run downloads model weights (~9 GB, cached in ~/.cache/huggingface)

## Output
All generated videos are saved to:
  D:/claw/projects/studio/outputs/video/<timestamp>_<slug>.mp4

## Generation parameters
| Parameter       | Default | Range   | Effect                          |
|-----------------|---------|---------|-------------------------------|
| num_frames      | 49      | 17–49   | 17=2s, 33=4s, 49=6s            |
| num_steps       | 25      | 20–50   | Higher = better quality, slower |
| guidance_scale  | 6.0     | 4–10    | Higher = closer to prompt      |
| fps             | 8       | 8       | Fixed at 8 fps for CogVideoX   |
| seed            | random  | any int | Set for reproducible results   |

## Prompt writing tips
- Be specific: describe lighting, camera angle, motion, style
- Include: subject, action, environment, mood, colour palette
- Avoid: text overlays, faces (unreliable), complex multi-scene narration

## Good prompt examples
- "A golden retriever running through a field of sunflowers, slow motion,
   warm afternoon light, cinematic depth of field"
- "Aerial drone shot of a coastline at sunset, waves crashing on rocks,
   dramatic orange sky, 4K cinematic"
- "A cup of coffee being poured in slow motion, steam rising,
   dark background, studio lighting, macro lens"
- "Futuristic city skyline at night, neon lights reflecting on wet streets,
   cyberpunk aesthetic, rain falling"

## Negative prompt defaults
"blurry, low quality, distorted, watermark, text, ugly, artifacts"

## Image generation (FLUX.1-schnell)
First run downloads ~24 GB of weights (cached after that).
Output saved to: D:/claw/projects/studio/outputs/images/

| Steps | Time (RTX 3050) | Quality       |
|-------|-----------------|---------------|
| 4     | ~30 seconds     | Good (default)|
| 8     | ~60 seconds     | Better detail |

Supported resolutions (multiples of 64): 512×512, 768×768, 1024×1024,
1024×576 (16:9), 576×1024 (9:16 portrait), 1280×720 (HD landscape)

## Video generation time estimates (RTX 3050)
- 17 frames (2s), 25 steps  →  ~3 minutes
- 33 frames (4s), 25 steps  →  ~5 minutes
- 49 frames (6s), 25 steps  →  ~8 minutes
- 49 frames (6s), 40 steps  →  ~12 minutes
