# OmaYap

Hold a key, talk, let go, and the words appear where your cursor is. Record a
meeting and get both sides of it as a transcript. Everything happens on this
machine, with one 126 MB speech model and no accounts.

It is a port of [yap](https://github.com/TerrifiedBug/yap)'s core to Omarchy,
and it replaces voxtype.

## What you get

Dictation: hold F9, speak, let go. The text is typed into whatever window had
focus. If nothing will take typed text, it goes to the clipboard and says so. A
tap shorter than 0.3 s is ignored, so a mis-hit stays out of your document.

A meeting recorder: your microphone and everything the speakers play, recorded
as two separate tracks and transcribed as `me` and `them`. You get one
directory per session with `transcript.md`, `transcript.json`, `meta.json`, and
the audio if you want to keep it.

Meeting detection: when another app takes the microphone, omayap offers to
record it. Click the notification, or let it start on its own.

A HUD: an 8-bit meter while you speak, and a scanning bar while the model
works. A meeting recording gets the same card with a clock and a stop button,
so "you are recording" stays on screen instead of scrolling away behind your
next notification. Adapted from [omakoe](https://github.com/ok/omakoe).

A bar button: state at a glance, with a popup for Start/Stop Recording and the
three toggles.

The daemon holds the model in memory and waits for a signal. Between presses it
wakes once a second to check its timers and does nothing else.

## Requirements

Omarchy 4.x, PipeWire, and these on `PATH`: `pw-record`, `wtype`, `wl-copy`,
`pactl`, `pw-dump`, `hyprctl`, `setpriv`. All of them ship with Omarchy.

Setup needs `python3` and about 130 MB of disk for the venv and the models.
Nothing needs `sudo` except removing voxtype.

## Install

```bash
# 1. Take the keys off voxtype (see below; reversible, needs sudo once)
sudo mv /usr/bin/voxtype /usr/bin/voxtype.disabled
systemctl --user disable --now voxtype.service

# 2. Install the plugin
omarchy plugin add https://github.com/TerrifiedBug/omayap.git --enable

# 3. Build the venv, fetch the models, start the service
~/.config/omarchy/plugins/io.github.terrifiedbug.omayap/setup.sh
```

`omarchy plugin add` only clones the repo and never runs plugin code, so step 3
is yours to run. It creates `~/.local/share/omayap/venv`, downloads the Parakeet
and Silero models (checksummed), writes `~/.config/omayap/config.json`, links
`~/.local/bin/omayap`, and enables `omayap.service` as a user service tied to
your graphical session.

Then add the keybindings to `~/.config/hypr/bindings.lua`:

```lua
o.bind("F9", "Start dictation (push-to-talk)", "~/.local/bin/omayap press")
o.bind("F9", "Stop dictation (push-to-talk)", "~/.local/bin/omayap release", { release = true })
o.bind("SUPER + CTRL + X", "Toggle dictation", "~/.local/bin/omayap toggle", { release = true })
```

and run `hyprctl reload`. The toggle carries modifiers, so it is bound on
release for the reason described under "Sharing keys with voxtype".

If you run omakoe, remove it: `omarchy plugin remove io.github.ok.omakoe`. Its
HUD is built in here, and two of them draw the same card twice.

## Sharing keys with voxtype

Omarchy ships `bindings/voxtype.lua`, and it holds both of the keys above:

```lua
if o.cmd_present("voxtype") then
  o.bind("SUPER + CTRL + X", "Toggle dictation", "voxtype record toggle")
  o.bind("F9", "Start dictation (push-to-talk)", "voxtype record start")
  o.bind("F9", "Stop dictation (push-to-talk)", "voxtype record stop", { release = true })
end
```

Adding omayap's binds on top does not replace voxtype's. Both stay bound:
`hyprctl binds` lists three F9 entries, and what one press then does is not
something you want to depend on. One of the two has to let go of the keys.

`cmd_present` looks for the `voxtype` file in every `PATH` directory, so the
binds disappear when that file does and nothing else turns them off.
`systemctl --user disable --now voxtype.service` stops the daemon but leaves the
keys bound to a program that is no longer listening.

Three ways to handle it, in the order most people want them.

### Disable voxtype, keep it installed (recommended)

On this machine `/usr/bin/voxtype` is a symlink into `/usr/lib/voxtype/` that no
package owns, which makes renaming it a clean off switch. Check yours before you
touch it, because the variant and the packaging can differ:

```bash
readlink /usr/bin/voxtype            # e.g. /usr/lib/voxtype/voxtype-vulkan
pacman -Qo /usr/bin/voxtype          # who owns it, if anyone
sudo mv /usr/bin/voxtype /usr/bin/voxtype.disabled
systemctl --user disable --now voxtype.service
hyprctl reload
```

Moving it rather than replacing it keeps whatever it pointed at, so the rollback
is a move back and you never have to remember the variant. If a package does own
the file, expect `pacman -Qkk voxtype-bin` to report it as altered until you move
it back.

The package, `/etc/voxtype`, `~/.config/voxtype`, and its models under
`~/.local/share/voxtype` all stay where they are.

A `voxtype-bin` package update recreates `/usr/bin/voxtype` and the binds come
back with it. If dictation suddenly doubles up after an update, that is what
happened.

### Keep voxtype fully working, give omayap a key of its own

No sudo, nothing moved, and one line is enough:

```lua
o.bind("SUPER + CTRL + Y", "Toggle dictation", "~/.local/bin/omayap toggle", { release = true })
```

Press to start, press again to stop. Everything works from that one bind: the
HUD, the transcript, the typing.

Bind the toggle on release, not on press. The transcript is typed by a virtual
keyboard, and a modifier you are still physically holding merges into every
injected letter at the seat, so a five-letter word becomes five
`SUPER + CTRL + <letter>` shortcuts and Omarchy's menu obliges by launching
things. Omarchy's own clipboard bindings inject chords through the compositor
for the same reason. `{ release = true }` means the keys are up before any text
is sent.

Push-to-talk on F9 or F10 is unaffected: those carry no modifier.


Push-to-talk is the only thing that needs a key to itself, because it binds a
press and a release on the same key. Add it if you prefer holding:

```lua
o.bind("F10", "Start dictation (push-to-talk)", "~/.local/bin/omayap press")
o.bind("F10", "Stop dictation (push-to-talk)", "~/.local/bin/omayap release", { release = true })
```

Both dictation systems then work side by side, and you pick which key you reach
for. That costs about 140 MB: voxtype's daemon sits at 105 MB RSS and the stock
bar indicator keeps two `voxtype status --follow` processes at 17 MB each.
Disable `voxtype.service` if you would rather only one daemon sat on the
microphone, and remember its keys stay bound either way.

### Remove voxtype for good

`omarchy-voxtype-remove` drops the `voxtype-bin` package and deletes
`~/.config/voxtype` and `~/.local/share/voxtype`. That includes its downloaded
models, so there is nothing to roll back to except a reinstall.

## Uninstalling omayap

Removing the plugin runs no teardown of its own, so do this before
`omarchy plugin remove`, while the scripts are still on disk:

```bash
cd ~/.config/omarchy/plugins/io.github.terrifiedbug.omayap
./uninstall.sh --restore-voxtype   # only if you disabled voxtype
./uninstall.sh
cd ~ && omarchy plugin remove io.github.terrifiedbug.omayap
```

`uninstall.sh` stops and disables `omayap.service`, removes the unit and the
`~/.local/bin/omayap` symlink, and tells you what is left on disk. Add
`--purge` to also delete `~/.local/share/omayap` (the venv and the 126 MB of
models) and `~/.config/omayap`. Your recordings are never touched.

`--restore-voxtype` moves `/usr/bin/voxtype.disabled` back to `/usr/bin/voxtype`
with its original target intact and re-enables `voxtype.service`. It runs on its
own and does nothing else, it is the one thing here that asks for sudo, it does
nothing if that file is not there, and it refuses to overwrite a `/usr/bin/voxtype`
that a package update has already put back.

Last step is yours: delete omayap's three lines from
`~/.config/hypr/bindings.lua` and run `hyprctl reload`.

## Settings

Everything lives in `~/.config/omayap/config.json`. The panel toggles write to
it, and editing it by hand works just as well: the next press reads the file.

```json
{
  "recordings_dir": "~/Recordings",
  "keep_audio": true,
  "newline_after_dictation": false,
  "meeting_detection": false,
  "meeting_auto_record": false,
  "meeting_excluded_apps": []
}
```

`recordings_dir` is where sessions go.

`keep_audio` keeps `mic.f32` and `system.f32` beside the transcript. Turn it off
and they are deleted once the transcript is written. Raw f32 costs 3.8 MB a
minute per track.

`newline_after_dictation` presses Enter once the text is typed, which sends the
message or runs the command instead of leaving it in the box. It does nothing
when the transcript went to the clipboard, because there is nothing to submit.

`meeting_detection` watches for another app taking the microphone.

`meeting_auto_record` starts recording without asking.

`meeting_excluded_apps` is a list of names to ignore. Each name is matched
against what the audio stream calls itself and against its binary, so either
spelling works: `pw-record` and `pw-cat` both silence the same program.

## What a session looks like

```
~/Recordings/2026.01.02-0930-Team Standup/
  meta.json
  transcript.md
  transcript.json
  mic.f32          # only with keep_audio
  system.f32
  transcribe.log
```

`transcript.md`:

```markdown
# 2026.01.02-0930-Team Standup

engine: parakeet (parakeet-tdt-ctc-110m)

**[0:02] me:** Morning, can you hear me?

**[0:05] them:** Loud and clear.
```

The directory is named for the local time the recording started, plus the window
title when detection found one. `transcript.json` has the same segments with
millisecond timestamps. `meta.json` has the start and end in UTC, the duration,
and how far apart the two tracks started.

## The CLI

`omayap` is what the keybindings and the panel call, and it is the whole API.

```bash
omayap press          # start dictating (hot path: one signal, no Python)
omayap release        # stop, transcribe, type
omayap toggle         # same thing without holding a key
omayap record         # start a meeting recording, or stop the running one
omayap status         # the daemon's state, as JSON
omayap config KEY VAL # write one setting
omayap bench FILE.wav # time the model on 16 kHz mono audio
```

## Speed

On an Intel Core Ultra X7 358H, `omayap bench` on the model's own 7.4 s sample:

```
audio 0.wav (7.4s)
min 62ms  p50 62ms  max 65ms  x119 realtime
```

The model loads in about half a second at startup and stays resident, so
releasing the key costs a decode and a keystroke. The daemon sits at roughly
215 MB RSS.

## How it fits together

```
bin/omayap ──signal──> omayap.service (daemon.py)
                          model resident, one selectors loop
                          ├─ dictation: pw-record → parakeet → wtype
                          ├─ meetings:  two pw-record tracks → meta.json
                          │             → transcribe.py in its own process
                          └─ detection: pactl subscribe + pw-dump

$XDG_RUNTIME_DIR/omayap/state  ──watched──> Service.qml ──> bar button, panel, HUD
$XDG_RUNTIME_DIR/omayap/levels ──FIFO────>  HUD meter
```

The daemon has no socket. Everything you do arrives as a signal, so the hot path
never starts an interpreter. The QML side reads one state file and runs the same
CLI the keys do, which is why the bar and the keyboard cannot disagree.

The HUD's level stream is a FIFO. The daemon computes peaks only while something
holds the read end open, so a HUD nobody is watching costs nothing.

## Privileges and processes

Runs as you, in your user session. No `sudo`, no `pkexec`, no setuid.

One user service, `omayap.service`, stopped with your graphical session. One
resident model in the daemon, plus a second short-lived one per transcription.

No network at runtime. `setup.sh` downloads two model files once, over HTTPS, and
verifies both against pinned SHA-256 checksums.

Nothing is uploaded. What gets written, and where:

| What | Where | Contains |
| --- | --- | --- |
| Transcripts | `recordings_dir/<session>/transcript.{md,json}` | every word that was said |
| Audio | same directory, `mic.f32` and `system.f32` | the recording, unless `keep_audio` is off |
| Meeting metadata | same directory, `meta.json` | the app name and the window title, when detection found one |
| Directory names | `recordings_dir` | the window title, which is the point of them |
| Daemon log | `journalctl --user -u omayap` | timing, counts, app names, errors. No transcribed words, no window titles, no session paths |
| Per-session log | `recordings_dir/<session>/transcribe.log` | which track was decoded and how many segments |
| Live state | `$XDG_RUNTIME_DIR/omayap/state` | the current session's directory path, until the next reboot |

Notifications put the meeting title on screen while a call is being offered or
recorded, which is the one place a title is meant to be visible.

## Troubleshooting

```bash
systemctl --user status omayap
journalctl --user -u omayap -n 50
omayap status
```

"daemon is not running": `systemctl --user start omayap`.

"model still loading": the first half second after the service starts.

Nothing was typed: the focused surface would not take keystrokes, and the text is
on your clipboard.

F9 does nothing: voxtype still owns it. Run `omarchy-voxtype-remove`, then
`hyprctl reload`.

A recording has no transcript: read `transcribe.log` in the session directory.
The daemon retries untranscribed sessions when it next starts.

## Credits and licence

MIT. See `LICENSE` and `NOTICE`.

`Hud.qml`, `PixelMorph.qml`, `Scanner.qml`, and `HudModel.js` are adapted from
[omakoe](https://github.com/ok/omakoe) (c) 2026 Oliver Kohl, MIT.

Speech recognition uses [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
with the Parakeet TDT CTC 110M and Silero VAD models it publishes. The model
files are downloaded by `setup.sh` and are not part of this repository.
