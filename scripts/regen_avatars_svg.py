"""Procedural SVG avatar generator for the five decision-room roles.

Used when the `claude.ai image-gen` MCP transport is unavailable (it has
a known `content[0].annotations: null` schema bug as of 2026-05-28 that
the client-side validator rejects). This generator is deterministic,
network-free, version-controllable, and produces real `.svg` files
under ``frontend/src/assets/agents/``.

Each avatar shares the visual contract documented in that directory's
README (deep navy → softer navy radial background, warm amber rim
light, painterly digital style anchored by a per-role accent color and
a per-role glyph) so the row of avatars reads as one designed set.

Run::

    PYTHONPATH=src .venv/bin/python scripts/regen_avatars_svg.py

then re-run the frontend production build to pick the new files up.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AvatarSpec:
    file_stem: str
    role_label: str
    initials: str
    accent: str  # hex like "#58e3c9"
    glyph_path: str  # SVG path data centered around (256, 256)
    glyph_stroke_width: float = 14.0


# Glyph paths are designed for a 96-unit-radius motif centered at (256, 256)
# inside a 512×512 viewBox. Each glyph evokes the role's primitive without
# being literal — keeps the badge set visually coherent while signaling intent.

GLYPH_CONDUCTOR = (
    # Five vertical lines of varying height = orchestration / waveform
    "M 196 220 L 196 292 "
    "M 226 196 L 226 316 "
    "M 256 178 L 256 334 "
    "M 286 196 L 286 316 "
    "M 316 220 L 316 292"
)

GLYPH_ARCHITECT = (
    # Layered isometric frame = system layers
    "M 192 220 L 256 188 L 320 220 L 256 252 Z "
    "M 192 260 L 256 228 L 320 260 L 256 292 Z "
    "M 192 300 L 256 268 L 320 300 L 256 332 Z"
)

GLYPH_PRODUCT = (
    # Concentric arcs opening upward = user-value lens
    "M 192 296 Q 256 232 320 296 "
    "M 208 308 Q 256 252 304 308 "
    "M 224 320 Q 256 280 288 320 "
    "M 256 304 L 256 332"
)

GLYPH_RISK = (
    # Shield outline + center break = guarding boundary
    "M 256 184 L 312 208 L 304 296 Q 296 320 256 332 Q 216 320 208 296 L 200 208 Z "
    "M 256 220 L 256 280"
)

GLYPH_SCRIBE = (
    # Stacked horizontal lines = ledger / event log entries
    "M 196 212 L 316 212 "
    "M 196 240 L 280 240 "
    "M 196 268 L 316 268 "
    "M 196 296 L 256 296 "
    "M 196 324 L 296 324"
)


AVATARS: tuple[AvatarSpec, ...] = (
    AvatarSpec(
        file_stem="supervisor",
        role_label="主持人",
        initials="主",
        accent="#ffd166",
        glyph_path=GLYPH_CONDUCTOR,
        glyph_stroke_width=14.0,
    ),
    AvatarSpec(
        file_stem="systems-architect",
        role_label="系统架构师",
        initials="架",
        accent="#58e3c9",
        glyph_path=GLYPH_ARCHITECT,
        glyph_stroke_width=4.0,
    ),
    AvatarSpec(
        file_stem="product-strategist",
        role_label="产品策略师",
        initials="品",
        accent="#ffb86b",
        glyph_path=GLYPH_PRODUCT,
        glyph_stroke_width=10.0,
    ),
    AvatarSpec(
        file_stem="risk-controller",
        role_label="风险控制师",
        initials="险",
        accent="#ff7a8c",
        glyph_path=GLYPH_RISK,
        glyph_stroke_width=4.0,
    ),
    AvatarSpec(
        file_stem="decision-scribe",
        role_label="决策记录员",
        initials="录",
        accent="#b794ff",
        glyph_path=GLYPH_SCRIBE,
        glyph_stroke_width=10.0,
    ),
)


def render_svg(spec: AvatarSpec) -> str:
    accent = spec.accent
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 512 512"
     width="512"
     height="512"
     role="img"
     aria-label="{spec.role_label}">
  <title>{spec.role_label}</title>
  <defs>
    <radialGradient id="bg-{spec.file_stem}" cx="50%" cy="42%" r="65%">
      <stop offset="0%" stop-color="#1a2236" />
      <stop offset="55%" stop-color="#0f1626" />
      <stop offset="100%" stop-color="#050810" />
    </radialGradient>
    <radialGradient id="rim-{spec.file_stem}" cx="22%" cy="18%" r="42%">
      <stop offset="0%" stop-color="#ffb86b" stop-opacity="0.42" />
      <stop offset="60%" stop-color="#ffb86b" stop-opacity="0.06" />
      <stop offset="100%" stop-color="#ffb86b" stop-opacity="0" />
    </radialGradient>
    <radialGradient id="accent-{spec.file_stem}" cx="50%" cy="58%" r="48%">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.22" />
      <stop offset="100%" stop-color="{accent}" stop-opacity="0" />
    </radialGradient>
    <filter id="soft-{spec.file_stem}" x="-25%" y="-25%" width="150%" height="150%">
      <feGaussianBlur stdDeviation="1.4" />
    </filter>
  </defs>

  <!-- background -->
  <rect width="512" height="512" rx="64" ry="64" fill="url(#bg-{spec.file_stem})" />
  <!-- warm amber rim light -->
  <rect width="512" height="512" rx="64" ry="64" fill="url(#rim-{spec.file_stem})" />
  <!-- role-tinted center wash -->
  <rect width="512" height="512" rx="64" ry="64" fill="url(#accent-{spec.file_stem})" />

  <!-- accent ring -->
  <circle cx="256" cy="256" r="170"
          fill="none"
          stroke="{accent}"
          stroke-opacity="0.85"
          stroke-width="4" />
  <circle cx="256" cy="256" r="170"
          fill="none"
          stroke="{accent}"
          stroke-opacity="0.18"
          stroke-width="14" />

  <!-- glyph -->
  <g filter="url(#soft-{spec.file_stem})"
     fill="none"
     stroke="{accent}"
     stroke-width="{spec.glyph_stroke_width}"
     stroke-linecap="round"
     stroke-linejoin="round"
     opacity="0.95">
    <path d="{spec.glyph_path}" />
  </g>

  <!-- initials (CJK so works as a fallback identifier even when rendered tiny) -->
  <text x="256" y="430"
        text-anchor="middle"
        font-family="'PingFang SC', 'Microsoft YaHei', 'Source Han Sans SC', 'Hiragino Sans GB', sans-serif"
        font-size="44"
        font-weight="600"
        fill="{accent}"
        opacity="0.92">{spec.initials}</text>
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "frontend/src/assets/agents",
        help="Where to write the generated .svg files.",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for spec in AVATARS:
        out_path = args.out_dir / f"{spec.file_stem}.svg"
        out_path.write_text(render_svg(spec), encoding="utf-8")
        print(f"wrote {out_path}  · accent={spec.accent}  · role={spec.role_label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
