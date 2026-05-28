# Agent Avatars

Two parallel sets representing the centralized MAS specialist roles:

- **`*.svg` (primary, used by the room UI)** — procedurally generated
  badge-style avatars from `scripts/regen_avatars_svg.py`. Each avatar
  is a deterministic, network-free, version-controlled SVG built around
  a shared visual contract: deep navy radial background, warm amber rim
  light, role-tinted accent ring, and a role-specific glyph stroke.
  Rebuild any time with::

      PYTHONPATH=src .venv/bin/python scripts/regen_avatars_svg.py

- **`*.webp` (legacy)** — earlier raster image-gen artifacts kept for
  reference and as a fallback if the SVG path is ever inappropriate.
  The room UI no longer imports these.

| Role ID | Display name | Avatar file (primary) | Accent color |
|---------|--------------|-----------------------|--------------|
| `host` / `supervisor` | 主持人 | `supervisor.svg` | `#ffd166` (amber) |
| `implementation_specialist` | 系统架构师 | `systems-architect.svg` | `#58e3c9` (teal) |
| `product_specialist` | 产品策略师 | `product-strategist.svg` | `#ffb86b` (orange) |
| `risk_specialist` | 风险控制师 | `risk-controller.svg` | `#ff7a8c` (pink) |
| `operations_specialist` / `synthesis` | 决策记录员 | `decision-scribe.svg` | `#b794ff` (violet) |

The per-role accent colors match `--role-*` tokens in
`frontend/src/styles/global.css`, so the avatar accent reads in the same
visual language as the role-tinted transcript card borders in the room.

## Regenerating with image-gen

When the `claude.ai image-gen` MCP server is connected, the avatars can
be regenerated with the prompts below. Keep aspect ratio square (1:1),
512×512 minimum, transparent background optional. Convert PNG → WebP
losslessly (e.g., `cwebp -lossless input.png -o output.webp`).

### Known transport-layer blocker (as of 2026-05-28)

A regeneration attempt was made via the `claude.ai image-gen` MCP server.
Every response — from `generate_image` (openai + hunyuan providers tested),
`reload_config`, and any other tool that returns content — fails the
client-side MCP validator with:

```
content[0].annotations: Invalid input: expected object, received null
```

The image-gen MCP server emits `annotations: null` in its response
envelope but the Claude Code MCP transport requires `annotations` to be
an object (or omitted entirely). This is a server-side schema-version
mismatch; nothing the client can work around. `ListMcpResourcesTool`
(which doesn't go through the same content shape) does work.

Until the server is patched (drop the `annotations` field when there is
nothing to annotate, OR send `{}`), use the prompts below to regenerate
out-of-band with any image generator and drop the resulting WebP files
into this directory under the exact filenames documented in the table.

### Shared visual contract (apply to every prompt)

> Cinematic, soft warm rim light, deep navy + amber palette, 3/4 portrait
> framing, minimal background, sharp focus on the subject, painterly
> digital illustration. Square 1:1, 512×512. Calm, professional, gender-
> neutral, varied ethnicities across the set. No text, no logos.

### Per-role prompts

- `supervisor.webp` — Calm, attentive conductor in a softly lit
  decision room. Holding a slim notebook. Wears a structured navy
  jacket. Subtle gesture of orchestration with one hand. Conveys
  authority without dominance.

- `systems-architect.webp` — Pragmatic engineer with focused, analytical
  expression. Light blue collared shirt. Subtle background hints at
  schematics or sequence diagrams (out of focus). Conveys feasibility
  thinking, not bravado.

- `product-strategist.webp` — Warm, empathetic communicator with open
  posture and a faint smile. Holds a notebook angled toward the
  viewer. Subtle background of soft post-it shapes. Conveys curiosity
  about user value.

- `risk-controller.webp` — Vigilant analyst with steady eye contact and
  composed posture. Wears a dark charcoal sweater over a collared shirt.
  Subtle background hints at warning indicators (out of focus, not
  alarming). Conveys disciplined skepticism, not anxiety.

- `decision-scribe.webp` — Composed operator wearing minimal headset,
  hands resting near a glowing surface (event log abstraction). Conveys
  steady documentation focus and synthesis. Soft violet rim light to
  differentiate from the other four.

## File-system convention

`frontend/src/routes/room.tsx` `roleAvatarMap` imports each file by name.
If you add a new role to `_ROLE_BLUEPRINTS` in
`src/decision_room/orchestration/pre_room_planning.py`, also add an
avatar here and register it in `roleAvatarMap`. Without registration the
UI falls back to `roleLabelMap` initials.
