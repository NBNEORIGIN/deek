"""
Local video generation using CogVideoX-2b on the local GPU.
Requires: torch (CUDA), diffusers, accelerate, imageio[ffmpeg]
Model weights (~9 GB) are downloaded to ~/.cache/huggingface on first use.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .registry import Tool, RiskLevel

# ── Config ────────────────────────────────────────────────────────────────────

MODEL_ID       = "THUDM/CogVideoX-2b"
DEFAULT_STEPS  = 25     # 20–50 range; 25 is a good quality/speed balance
DEFAULT_FPS    = 8      # CogVideoX native output frame rate
DEFAULT_FRAMES = 49     # ~6 s @ 8 fps  (must be 4k+1: 17, 33, 49 …)

# ── Lazy pipeline loader ──────────────────────────────────────────────────────

_pipe = None


def _load_pipeline():
    global _pipe
    if _pipe is not None:
        return _pipe

    try:
        import torch
        from diffusers import CogVideoXPipeline
    except ImportError as exc:
        raise RuntimeError(
            "Video generation packages not found.\n"
            "Run:  pip install diffusers accelerate imageio[ffmpeg] transformers"
        ) from exc

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA GPU not available. Video generation requires an NVIDIA GPU."
        )

    print("[video] Loading CogVideoX-2b — first run downloads ~9 GB …")
    _pipe = CogVideoXPipeline.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
    )
    # CPU offload keeps peak VRAM under 8 GB on the RTX 3050
    _pipe.enable_model_cpu_offload()
    # VAE tiling avoids OOM when decoding longer clips
    _pipe.vae.enable_tiling()
    print("[video] Model ready")
    return _pipe


# ── Generation ────────────────────────────────────────────────────────────────

def _generate_video(
    project_root: str,
    prompt: str,
    output_path: str | None = None,
    negative_prompt: str = "blurry, low quality, distorted, watermark, text",
    num_frames: int = DEFAULT_FRAMES,
    num_inference_steps: int = DEFAULT_STEPS,
    guidance_scale: float = 6.0,
    fps: int = DEFAULT_FPS,
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate an MP4 video from a text prompt on the local GPU."""
    try:
        import torch
        from diffusers.utils import export_to_video
    except ImportError as exc:
        return {"error": str(exc)}

    # ── Resolve output path ────────────────────────────────────────────────
    if output_path is None:
        out_dir = Path(project_root) / "outputs" / "video"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts   = int(time.time())
        safe = "".join(
            c if c.isalnum() or c in "-_ " else "_" for c in prompt[:40]
        ).strip().replace(" ", "_")
        output_path = str(out_dir / f"{ts}_{safe}.mp4")
    else:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # ── Load model ─────────────────────────────────────────────────────────
    try:
        pipe = _load_pipeline()
    except RuntimeError as exc:
        return {"error": str(exc)}

    # ── Run inference ──────────────────────────────────────────────────────
    generator = None
    if seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(seed)

    t0 = time.time()
    print(f"[video] prompt   : {prompt[:80]}")
    print(f"[video] frames   : {num_frames}  steps: {num_inference_steps}"
          f"  guidance: {guidance_scale}")

    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        num_frames=num_frames,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        generator=generator,
    )

    export_to_video(result.frames[0], output_path, fps=fps)

    elapsed = round(time.time() - t0, 1)
    print(f"[video] saved → {output_path}  ({elapsed}s)")

    return {
        "success":     True,
        "output_path": output_path,
        "duration_s":  round(num_frames / fps, 1),
        "frames":      num_frames,
        "fps":         fps,
        "elapsed_s":   elapsed,
        "prompt":      prompt,
    }


# ── Tool object ───────────────────────────────────────────────────────────────

generate_video_tool = Tool(
    name="generate_video",
    description=(
        "Generate a short video clip from a text prompt using CogVideoX-2b "
        "running locally on the GPU (RTX 3050, no API cost). "
        "Returns the path to a saved MP4 file. "
        "Takes 3–8 minutes. "
        "Use for: demo clips, concept visualisations, marketing assets, "
        "animating UI mockups, or any creative video content."
    ),
    risk_level=RiskLevel.SAFE,
    fn=_generate_video,
    required_permission="generate_video",
)
