# Architecture

Coco is one Python process for the cat (Qt), plus a small web server for the dashboard
(NiceGUI) and a scheduled "keeper" that restarts things when they're missing. Everything
talks through a small event bus.

```
 DesktopSensor (Win32)  ──world──►  BehaviourEngine  ──pose──►  CatWindow, bed, bubbles
 CatWindow mouse events ──bus────►  (pure Python)
 Speech bubble / voice  ──chat.ask─► Brain (worker thread, Ollama) ──brain.reply──► bubble, voice
                                      └─ tool + args ─► ToolRunner ─► tool.result
```

## Packages

| Package | What's in it | Rules |
|---|---|---|
| `coco/core/` | event bus, config, cat definition, sprite manifest, geometry, paths, launch helpers | no Qt, no Win32 |
| `coco/behaviour/` | states, plans, Director, physics, surfaces, petting, mood, canned lines | no Qt, no Win32 |
| `coco/desktop/` | `base.py` interface, `windows.py` (all Win32 calls), `fake.py` for tests, `sensors.py` | |
| `coco/ui/` | Qt windows: cat, bed, speech and mood bubbles, chips, overlay, tray | |
| `coco/brain/` | Ollama backend, streaming reply parser, prompt, name filter | talks to the UI only through `bus.post` |
| `coco/voice/` | speech recognition (`stt.py`), wake word (`wake.py`), voice (`tts.py`) | no Qt |
| `coco/tools/` | every tool, the registry and runner, phrase rules, SQLite store | |
| `coco/agent/` | "do it for me": accessibility tree, actions, guard, step loop | |
| `coco_hub/` | the dashboard | talks to Coco through `coco/hub_link.py` |

## One frame

```
app._on_frame()
  bus.drain()                       # events posted from other threads
  world = sensor.snapshot(...)      # surfaces, cursor, idle time, fullscreen
  pose  = engine.tick(dt, world)    # x, y, animation, frame, facing
  cat_window.show_pose(pose)        # move / repaint only if something changed
```

The timer runs at 60 fps while she moves. Otherwise it only wakes up for her next
animation frame (4 fps while she sleeps), and it stops while she's hidden.

## Coordinates

The engine and Win32 use physical pixels; her position is her paw anchor. Config values
are in logical pixels and get multiplied by the monitor's scale factor, so she moves at
the same visual speed at 100 %, 125 % and 150 %. `ui/coords.py` converts to Qt's
logical pixels.

## States

Each state is a small class in `behaviour/states.py` with `enter()`, `update(dt)` (returns
`None`, `DONE` or the next state) and a few flags (`grounded`, `moving`, `pettable`,
`clickable`, `visible`). Multi-step activities are plans: a queue of factories, so each
step's target is worked out when the step starts.

- Idle: sit (with blink and tail-swish fidgets), loaf, groom, yawn, stretch, look around.
- Moving: walk, run, jump onto the active window, fall, land, peek in from the screen edge.
- Home: walk to the bed, jump in, sleep, wake up, hop out.
- Play: stalk the cursor and pounce.
- Interrupts: dragging (dangle, then fall with the mouse's velocity), losing the surface
  under her, petting (purr), clicks, fullscreen apps (hide).

The Director picks the next idle activity every few seconds from weights in
`config.yaml`, cooldowns, the time of day, her mood and what's possible right now.

## Surfaces

`behaviour/surfaces.py` turns window rectangles (in z-order) into segments she can stand
on: the ground (top of the taskbar, or the screen bottom when it auto-hides), her bed, and
the visible parts of window top edges. Windows are scanned twice a second, and the one
she's standing on is re-checked every frame. Landing is swept: each physics step checks
the whole path travelled, so a fast throw can't pass through a thin edge.

All Win32 calls live in `desktop/windows.py`: monitors and DPI, window enumeration and
DWM frame bounds, cursor and idle time, fullscreen detection, taskbar info, hotkeys,
the startup entry and the single-instance mutex. Click-through and tool windows are
ignored when looking for surfaces.

## Chat

```
click / Ctrl+Alt+M ─► chat.open ─► text box; the model starts loading
Enter ─► chat.ask ─► Brain.ask() on a worker thread ─► THINK
  stream ─► brain.reply (partial) ─► bubble types it, TALK
  end    ─► brain.reply (final, emotion) ─► EMOTE ─► SIT
```

Ollama's structured output forces a JSON reply: `emotion`, optional `action`, `tool` and
`args`, `text`, `speech_hi` (Devanagari, for Hinglish replies) and `remember`. The
emotion comes first, and `brain/reply_parser.py` reads fields out of the partial JSON, so
her face can change before the sentence is done. Thinking is off, and prewarm and chat
use the same `num_ctx` (a different one makes Ollama reload the model).

Mood (`behaviour/mood.py`) has a few stats that drift over time and feed the Director's
weights and the prompt. It's saved to `mood.json` and caught up on start.

## Voice

```
 mic (open while "Microphone ready")
   ├─ 1.5 s ring buffer (pre-roll for push-to-talk)
   └─ WakeFlow: detector every 80 ms ─► Whisper checks the last 2 s ─► record until quiet
 push-to-talk (Ctrl+Alt+V)
        │
 Listener.hear(): English ear (large-v3-turbo) + Hinglish ear ─► pick_transcript()
        │
 voice.heard ─► quick command, or the Brain ─► text + speech_hi ─► Kokoro
```

`pick_transcript()` prefers a Hinglish command from the Hinglish ear, then an English
command from the English ear, then whichever ear was more confident. Song requests go
through `voice/music_words.py`, which matches names by how they sound.

Setting up the Hinglish ear (once). The conversion needs PyTorch, so do it in a separate
venv:

```powershell
py -3.12 -m venv %TEMP%\convvenv
%TEMP%\convvenv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu
%TEMP%\convvenv\Scripts\pip install transformers ctranslate2
%TEMP%\convvenv\Scripts\ct2-transformers-converter --model Oriserve/Whisper-Hindi2Hinglish-Apex --output_dir %LOCALAPPDATA%\Coco\models\whisper\hinglish-apex --quantization float16 --copy_files tokenizer.json preprocessor_config.json
```

Hinglish replies: if the question looks Hinglish (`core/hindi.py`), the schema makes
`speech_hi` required, and the Hindi voice reads it. Kokoro can only pronounce Hindi from
Devanagari.

The wake word model (`tools_dev/train_wake_word.py`) is a small MLP on openWakeWord
embeddings, trained on your recordings plus synthetic clips, exported to numpy. Its
threshold is chosen on held-out clips.

## Tools

```
chat.ask "remind me at 6 to submit"
  1. parse_command()   cat actions (behaviour/commands.py)
  2. parse_task()      tool phrases (tools/intents.py), English and Hinglish
  3. Brain             picks "tool" + "args" from the allowed tools only
       └─► ToolRunner: allow-list check, Yes/No for risky calls, runs the tool
            └─► tool.result: a line to say, or facts for the Brain to answer from
```

A tool is available only if it's registered, listed under `allowed_tools` in
`cats/coco/cat.yaml` and switched on. Tools that need the Qt main thread (screen capture,
clipboard) run there; the rest run on worker threads. Notes, reminders, chats, memories
and activity live in `coco.db` (`tools/store.py`).

"Do it for me" (`coco/agent/`) reads the target window through UI Automation, asks the
model for one action per step, checks it in `guard.py` and performs it in `actions.py`.

## Dashboard

`coco_hub/` is a NiceGUI app on 127.0.0.1:8765. It reads `coco.db` directly and talks to
the running Coco over `hub_link.py`: a localhost HTTP link with a token that's new on
every start (`%LOCALAPPDATA%\Coco\hub\link.json`). The phone reaches it through
`tailscale serve`. Pages need a PIN (scrypt hash, lockout after failed tries).

## Startup

Windows starts Coco through the `HKCU\...\Run` entry. A few seconds later she starts
Ollama, the dashboard and Tailscale's tray app if they aren't running
(`core/companions.py`). The "Coco keeper" scheduled task (`coco/keeper.py`) runs at sign-in,
on unlock, on wake and every 5 minutes and restarts anything that's missing, unless Coco
was quit on purpose.

## Packaging

`packaging/coco.spec` builds a one-folder PyInstaller app. `Coco.exe` runs the cat,
`--hub` the dashboard, `--keep` the keeper and `--selftest` a check of every part. Only
the CUDA DLLs Whisper needs are bundled. `packaging/coco.iss` builds the installer.
