"""
Local image generation using FLUX.1-schnell on the GPU.
4-step model — ~30 seconds per image on RTX 3050.
Model weights ~24 GB but CPU offload keeps VRAM under 8 GB.
Weights cached in ~/.cache/huggingface after first download.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .registry import Tool, RiskLevel

MODEL_ID = "stabilityai/sdxl-turbo"

_pipe = None


def _load_pipeline():
    global _pipe
    if _pipe is not None:
        return _pipe

    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except ImportError as exc:
        raise RuntimeError(
            "Image generation packages not found.\n"
            "Run:  pip install diffusers accelerate transformers"
        ) from exc

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU not available. Image generation requires an NVIDIA GPU.")

    print("[image] Loading SDXL-Turbo — first run downloads ~7 GB …")
    _pipe = AutoPipelineForText2Image.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
        variant="fp16",
    )
    # SDXL-Turbo fits in 8 GB VRAM — no offload needed, keeps generation fast
    _pipe = _pipe.to("cuda")
    print("[image] Model ready")
    return _pipe


def _generate_image(
    project_root: str,
    prompt: str,
    output_path: str | None = None,
    width: int = 512,
    height: int = 512,
    num_inference_steps: int = 4,    # SDXL-Turbo works well at 1-4 steps
    guidance_scale: float = 0.0,     # must be 0.0 for SDXL-Turbo
    num_images: int = 1,
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate a still image from a text prompt using FLUX.1-schnell on the GPU."""
    try:
        import torch
    except ImportError as exc:
        return {"error": str(exc)}

    # ── Resolve output path ────────────────────────────────────────────────
    out_dir = Path(project_root) / "outputs" / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts   = int(time.time())
    safe = "".join(
        c if c.isalnum() or c in "-_ " else "_" for c in prompt[:50]
    ).strip().replace(" ", "_")

    if output_path is None:
        output_path = str(out_dir / f"{ts}_{safe}.png")
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
        generator = torch.Generator(device="cpu").manual_seed(seed)

    t0 = time.time()
    print(f"[image] prompt : {prompt[:80]}")
    print(f"[image] size   : {width}x{height}  steps: {num_inference_steps}")

    result = pipe(
        prompt=prompt,
        width=width,
        height=height,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        num_images_per_prompt=num_images,
        generator=generator,
    )

    saved_paths = []
    for i, image in enumerate(result.images):
        if i == 0:
            path = output_path
        else:
            p = Path(output_path)
            path = str(p.parent / f"{p.stem}_{i}{p.suffix}")
        image.save(path)
        saved_paths.append(path)

    elapsed = round(time.time() - t0, 1)
    print(f"[image] saved → {saved_paths[0]}  ({elapsed}s)")

    return {
        "success":      True,
        "output_paths": saved_paths,
        "output_path":  saved_paths[0],
        "width":        width,
        "height":       height,
        "elapsed_s":    elapsed,
        "prompt":       prompt,
    }


generate_image_tool = Tool(
    name="generate_image",
    description=(
        "Generate a high-quality still image from a text prompt using SDXL-Turbo "
        "running locally on the GPU (RTX 3050, no API cost). "
        "Takes ~10 seconds. Outputs a PNG file. Default size 512×512. "
        "Use for: concept art, UI mockups, product visuals, backgrounds, logos, "
        "illustrations, marketing images, or any creative still image content."
    ),
    risk_level=RiskLevel.SAFE,
    fn=_generate_image,
    required_permission="generate_image",
)
