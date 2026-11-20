# Coco's sprite format

Each cat keeps her art in `cats/<name>/sprites/`:

```
cats/coco/sprites/
  manifest.yaml     <- which animations exist, frame counts, speeds, anchor points
  sit.png           <- one horizontal strip per animation
  walk.png
  ...
```

## The rules

1. **One animation = one horizontal PNG strip.** Frames sit side by side, left to right,
   all the same size. A 6-frame animation with 256×256 frames is a 1536×256 PNG.
2. **RGBA with a transparent background.** Only her body should be opaque: transparent
   pixels let mouse clicks through to the app behind her. Pixels that are at least about 50 % opaque catch
   clicks.
3. **Art faces LEFT.** Coco mirrors frames in code when she walks right, so you never draw
   (or render) a right-facing version.
4. **Same scale and same camera for every animation.** The cat must be the same size in
   `sit.png` and `walk.png`, or she'll grow and shrink when she switches.
5. **Paws on the anchor.** The *anchor* is the point where her paws touch the ground. It
   stays fixed in every frame and every animation (default `(128, 236)` in a 256×256 frame,
   i.e. 20 px above the bottom, centred). That point is what stands on the taskbar and
   window edges.
6. Display size is set in the tray (S/M/L = 96/128/192 px for the full frame at 100 %
   scaling). Frames are scaled once at load time; 256 px sources look sharp up to L at
   150 % scaling. Render at 512 px if you want headroom.

## manifest.yaml

```yaml
version: 1
frame_size: [256, 256]   # default size of ONE frame (source px)
anchor: [128, 236]       # default paw point inside a frame

animations:
  sit:    {frames: 8, fps: 6,  loop: true}
  yawn:   {frames: 8, fps: 10, loop: false, next: sit}
  dangle: {frames: 6, fps: 8,  loop: true, grab: [128, 46]}
  stretch: {frames: 10, fps: 10, loop: false, frame_size: [320, 256], anchor: [160, 236]}
```

| Key | Meaning |
|---|---|
| `frames` | number of frames in the strip |
| `fps` | playback speed |
| `loop` | `true` = repeats; `false` = plays once and holds the last frame |
| `file` | optional, defaults to `<name>.png` |
| `frame_size` | optional per-animation override (e.g. a wide stretch) |
| `anchor` | optional per-animation paw point |
| `grab` | `dangle` only: where the mouse holds her (her scruff) |
| `next` | documentation hint for which animation follows a one-shot |

The behaviour engine reads `frames / fps` to know when a one-shot (yawn, land...) has
finished, so **frame counts and fps are part of the behaviour**: a longer yawn really
takes longer.

## Core animations

| Animation | Frames @ fps | Loop | What it should show |
|---|---|---|---|
| `sit` | 8 @ 6 | ✓ | sitting, gentle breathing, tiny tail movement (the idle "hub") |
| `blink` | 4 @ 12 | | sitting blink (played now and then during `sit`) |
| `tail_swish` | 8 @ 10 | | sitting, tail tip lifts and swishes |
| `loaf` | 6 @ 5 | ✓ | "bread loaf": paws tucked, sleepy eyes, breathing |
| `groom` | 10 @ 10 | ✓ | sitting, licking a raised paw / washing face |
| `yawn` | 8 @ 10 | | big yawn, eyes squeezed shut |
| `stretch` | 10 @ 10 | | front stretch (bum up), then back legs |
| `look_around` | 10 @ 8 | | head turns left, right, back |
| `purr` | 6 @ 8 | ✓ | eyes closed happily, blush, slight vibration (being petted) |
| `sleep` | 6 @ 4 | ✓ | curled up asleep, slow breathing (drawn in the hammock bed) |
| `wake_up` | 8 @ 10 | | curled → lifts head → sits up |
| `walk` | 8 @ 12 | ✓ | walk cycle in place (the code moves her) |
| `run` | 6 @ 16 | ✓ | gallop cycle in place |
| `jump` | 6 @ 16 | | 3 crouch frames, push-off, then airborne (last frame held while rising) |
| `fall` | 4 @ 10 | ✓ | airborne, legs reaching down, tail up |
| `land` | 4 @ 16 | | touch-down squash and recover |
| `peek` | 8 @ 8 | | head leans in from the edge, looks around, leans back |
| `stalk` | 6 @ 8 | ✓ | low crouch, creeping, bum wiggle |
| `pounce` | 4 @ 14 | | leap with front paws reaching forward |
| `dangle` | 6 @ 8 | ✓ | held by the scruff, legs dangling, slight swing (front view is fine) |

## Talking and emotions

Optional per cat: if one is missing, Coco shows `sit` instead, so you can add them one
at a time. Which animation each AI emotion uses is set in `cat.yaml` ▸ `emotions:`.

| Animation | Frames @ fps | Loop | What it should show |
|---|---|---|---|
| `talk` | 4 @ 10 | ✓ | sitting, mouth opening and closing (while her answer types out) |
| `think` | 6 @ 6 | ✓ | head tilt, looking up, paw at her chin (waiting for the AI) |
| `happy` | 6 @ 10 | ✓ | tail up, ^^ eyes, a little bounce |
| `sad` | 6 @ 5 | ✓ | ears drooping, eyes down, slumped (also used for sulking) |
| `angry` | 6 @ 10 | ✓ | fur and tail puffed up, ears back, a (cute) hiss |
| `surprised` | 5 @ 12 | | startled hop, wide eyes |
| `curious` | 8 @ 8 | ✓ | head tilting side to side |
| `sleepy` | 6 @ 5 | ✓ | heavy eyelids, slow sway |
| `love` | 8 @ 6 | ✓ | a slow blink (cat for "I love you"), blush |

Mood bubbles are separate small icons in `assets/bubbles/` (`heart.png`, `zzz.png`,
`exclaim.png`, `question.png`, `ear.png`, and for tasks `camera.png`, `note.png`,
`timer.png`, `music.png`; 64×64 RGBA, shown at about 30 px).

**Your Blender pipeline** (`art/export/...`): when you copy your rendered strips into
`cats/coco/sprites/`, keep (or add) these 9 entries in its `manifest.yaml`. Without them
Coco still works, but she'll just sit instead of showing emotions until they're rendered.

Walk/run cycles are played **in place**: Coco's position is moved by code at the speed set
in `config.yaml` (`movement.walk_speed`, `run_speed`). If her feet slide, change the speed
or the fps until they match.

## Task animations

Also optional: until they exist she shows `sit` plus a bubble icon (camera, note, timer,
music: `assets/bubbles/`, made by `tools_dev/make_tool_icons.py`, same style as the heart).

| Animation | Frames @ fps | Loop | What it should show |
|---|---|---|---|
| `camera` | 8 @ 10 | | sitting, she lifts a tiny camera with both paws, *click* (a white flash on frame 5), lowers it, pleased |
| `note` | 8 @ 8 | ✓ | sitting, holding up a small yellow sticky note in one paw (she walks over with it for a reminder) |
| `point` | 6 @ 8 | | sitting, one paw raised and pointing to the side (towards the screen), head turned the same way |

Same rules as everything else: 256×256 frames, paws on the anchor, facing right (Coco
mirrors them), transparent background. Add them to `manifest.yaml` like the others.

## Swapping in your Blender renders

1. Set up a fixed **orthographic side camera** (looking at her left side, so she faces
   left), transparent film (Render ▸ Film ▸ Transparent), toon shading.
2. Put the ground plane so her paws land on the same spot of the frame in every animation.
   For a 256×256 render that's 20 px above the bottom, centred.
3. Render each action to its own folder, e.g. `renders/walk/0001.png ...`.
4. Pack each folder into a strip:
   ```powershell
   .venv\Scripts\python tools_dev\pack_frames.py renders\walk cats\coco\sprites\walk.png
   ```
   (add `--flip` if your camera saw her right side).
5. Update `frames:` and `fps:` in `manifest.yaml` (the packer prints the line).
6. Run the check, then tray ▸ *Reload config + art* (no restart needed):
   ```powershell
   .venv\Scripts\python -m pytest tests\test_manifest.py
   ```
   It checks that every animation exists and that every strip's size matches its manifest entry.

The placeholders are generated by `tools_dev/make_placeholder_sprites.py` into
`cats/coco/sprites_placeholder/` (never into `sprites/`). Re-running it
**overwrites** the strips, so don't run it after you've added real art.

## Coco's bed

`assets/bed/` holds her home: `bed_back.png` (drawn behind her), `bed_front.png` (the
fluffy front rim, drawn in front of her so she sinks into the hammock) and `bed.yaml`:

| Key | Meaning |
|---|---|
| `size` | source size of both layers (384×192) |
| `scale_to_cat` | `1.0` = drawn at the same pixels-per-unit as her sprites (it's scaled by the same factor as her frames) |
| `bed` | where her paw anchor goes when she's in it (source px) |
| `bed_half_width` | how far left/right of that point she can stand in it |

If `assets/bed/` is missing, Coco falls back to the old `assets/basket/`.

## Adding a new cat (Misty, v2)

Copy `cats/coco/` to `cats/misty/`, replace the PNGs, edit `cat.yaml` (name, personality,
voice, `behaviour_weights` such as `{stalk: 9, sleep: 8}` for a mischievous cat), and set
`cat: misty` in `config.yaml`. No code changes.
