# Agent Avatars

The room UI now uses Codex built-in `image_gen` raster avatars directly,
without the MCP image-gen server. The MCP path is still documented as a
known blocker because that server currently returns `annotations: null`
inside MCP content envelopes, which Claude Code rejects.

## Primary Assets

| Role ID | Display name | Avatar file (primary) | Accent color |
|---------|--------------|-----------------------|--------------|
| `host` / `supervisor` | 主持人 | `supervisor-codex.png` | `#ffd166` (amber) |
| `implementation_specialist` / `systems_architect` | 系统架构师 | `systems-architect-codex.png` | `#58e3c9` (teal) |
| `product_specialist` / `product_strategist` / `pro` | 产品策略师 / 正方顾问 | `product-strategist-codex.png` | `#ffb86b` (orange) |
| `risk_specialist` / `risk_controller` / `con` | 风险控制师 / 反方顾问 | `risk-controller-codex.png` | `#ff7a8c` (pink) |
| `operations_specialist` | 运营观察员 | `operations-specialist-codex.png` | `#88a5ff` (blue-violet) |
| `synthesis` / `decision_scribe` / `recorder` | 决策记录员 | `decision-scribe-codex.png` | `#b794ff` (violet) |

All primary files are 512x512 PNGs generated in this Codex session, then
downscaled with `ffmpeg` for frontend bundle size. The original generated
files remain under `$CODEX_HOME/generated_images/019e6f38-80e5-7cc2-9acb-02249b51f0b7/`.

The previous deterministic SVG files and earlier WebP files are kept as
legacy/reference assets. `scripts/regen_avatars_svg.py` can still rebuild
the SVG fallback set, but `frontend/src/routes/room.tsx` imports the
`*-codex.png` assets.

## Codex Image-Gen Prompt Contract

Use Codex built-in `image_gen`, not the MCP server. Keep aspect ratio
square (1:1), avoid any text/logos/watermarks, and keep the face readable
inside the UI's 46px circular crop.

### Shared visual contract

> Cinematic, soft warm rim light, deep navy + amber palette, 3/4 portrait
> framing, minimal background, sharp focus on the subject, painterly
> digital illustration. Square 1:1, 512×512. Calm, professional, varied
> personas across the set. No text, no logos.

### Per-role prompt summaries

- `supervisor-codex.png` — Calm, attentive conductor in a softly lit
  decision room. Holding a slim notebook. Wears a structured navy
  jacket. Subtle gesture of orchestration with one hand. Conveys
  authority without dominance.

- `systems-architect-codex.png` — Pragmatic engineer with focused, analytical
  expression. Light blue collared shirt. Subtle background hints at
  schematics or sequence diagrams (out of focus). Conveys feasibility
  thinking, not bravado.

- `product-strategist-codex.png` — Warm, empathetic communicator with open
  posture and a faint smile. Holds a notebook angled toward the
  viewer. Subtle background of soft post-it shapes. Conveys curiosity
  about user value.

- `risk-controller-codex.png` — Vigilant analyst with steady eye contact and
  composed posture. Wears a dark charcoal sweater over a collared shirt.
  Subtle background hints at warning indicators (out of focus, not
  alarming). Conveys disciplined skepticism, not anxiety.

- `operations-specialist-codex.png` — Calm operational lead with a compact
  tablet or runbook folder. Subtle background of workflow lanes and status
  dots. Conveys delivery-flow and readiness awareness.

- `decision-scribe-codex.png` — Composed operator wearing minimal headset,
  hands resting near a glowing surface (event log abstraction). Conveys
  steady documentation focus and synthesis. Soft violet rim light to
  differentiate from the other roles.

## Known MCP Transport Blocker

A regeneration attempt was made via the `claude.ai image-gen` MCP server
on 2026-05-28. Every response from `generate_image`, `reload_config`, and
`get_image_data` failed the client-side validator with:

```
content[0].annotations: Invalid input: expected object, received null
```

The deployed image-gen MCP server emits `annotations: null` in its
response envelope. Claude Code requires `annotations` to be an object or
omitted, so the MCP path remains blocked until the server strips `None`
fields or emits `{}`.

## File-system convention

`frontend/src/routes/room.tsx` `roleAvatarMap` imports each file by name.
If you add a new role to `_ROLE_BLUEPRINTS` in
`src/decision_room/orchestration/pre_room_planning.py`, also add an
avatar here and register it in `roleAvatarMap`. Without registration the
UI falls back to `roleLabelMap` initials.
