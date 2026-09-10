#!/bin/bash
# One-time setup: a venv, the speech models, a config file, and a user service.
#
# No sudo, and nothing outside your home directory. `omarchy plugin add` only
# clones a repo — it never runs plugin code — so this is the step the README
# tells you to run yourself.
#
# Idempotent: run it again after a git pull and it will only do what is
# missing. The two model downloads are checksummed, so a truncated or
# substituted file fails loudly instead of becoming a mysterious silence.

set -euo pipefail

here=$(dirname "$(readlink -f "$0")")
share=$HOME/.local/share/omayap
models=$share/models
config_dir=$HOME/.config/omayap
unit_dir=$HOME/.config/systemd/user

model_url=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8.tar.bz2
model_sha256=17f945007b52ccd8b7200ffc7c5652e9e8e961dfdf479cefcabd06cf5703630b
model_dir=$models/parakeet-tdt-ctc-110m
tarball_root=sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8

vad_url=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
vad_sha256=9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6

say() { printf '  %s\n' "$*"; }

verify() {
  local path=$1 want=$2 got
  got=$(sha256sum "$path" | cut -d' ' -f1)
  if [[ $got != "$want" ]]; then
    echo "omayap: checksum mismatch for $path" >&2
    echo "  expected $want" >&2
    echo "  got      $got" >&2
    rm -f "$path"
    exit 1
  fi
}

echo "omayap setup"

# ---- the venv: sherpa-onnx and nothing else
if [[ ! -x $share/venv/bin/python ]]; then
  say "creating $share/venv"
  mkdir -p "$share"
  python3 -m venv "$share/venv"
fi
if ! "$share/venv/bin/python" -c 'import sherpa_onnx' 2>/dev/null; then
  say "installing sherpa-onnx"
  "$share/venv/bin/pip" install --quiet --disable-pip-version-check sherpa-onnx==1.13.7
fi

# ---- the models: 126 MB of int8 Parakeet, plus Silero for segmenting meetings
mkdir -p "$models"
if [[ ! -f $model_dir/model.int8.onnx ]]; then
  say "downloading the speech model (126 MB)"
  curl -fL --max-time 600 -o "$models/model.tar.bz2" "$model_url"
  verify "$models/model.tar.bz2" "$model_sha256"
  mkdir -p "$model_dir"
  tar -xjf "$models/model.tar.bz2" --strip-components=2 -C "$model_dir" \
    "./$tarball_root/model.int8.onnx" "./$tarball_root/tokens.txt" "./$tarball_root/test_wavs"
  rm -f "$models/model.tar.bz2"
fi
if [[ ! -f $models/silero_vad.onnx ]]; then
  say "downloading the voice activity model"
  curl -fL --max-time 300 -o "$models/silero_vad.onnx" "$vad_url"
  verify "$models/silero_vad.onnx" "$vad_sha256"
fi

# ---- config: yours to edit, never overwritten
mkdir -p "$config_dir"
if [[ ! -f $config_dir/config.json ]]; then
  say "writing $config_dir/config.json"
  cp "$here/config.template.json" "$config_dir/config.json"
fi

# ---- the CLI on PATH, so a keybinding is one word
mkdir -p "$HOME/.local/bin"
ln -sfn "$here/bin/omayap" "$HOME/.local/bin/omayap"

# ---- the service: dies with the graphical session, comes back with it
mkdir -p "$unit_dir"
cat >"$unit_dir/omayap.service" <<UNIT
[Unit]
Description=omayap dictation and meeting recorder
PartOf=graphical-session.target
After=graphical-session.target
ConditionPathExists=$here/bin/omayap

[Service]
ExecStart=$here/bin/omayap daemon
Restart=on-failure
RestartSec=2
Environment=XDG_RUNTIME_DIR=%t

[Install]
WantedBy=graphical-session.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now omayap.service
say "omayap.service enabled"

if command -v voxtype >/dev/null 2>&1; then
  cat <<'VOXTYPE'

voxtype is still on PATH, so Omarchy's own bindings hold F9 and SUPER + CTRL + X,
and adding omayap on the same keys leaves both bound. Either take the keys off
voxtype:

  readlink /usr/bin/voxtype              # note where it points
  sudo mv /usr/bin/voxtype /usr/bin/voxtype.disabled
  systemctl --user disable --now voxtype.service

which is reversible and uninstalls nothing, and then use the F9 bindings in the
README. Or leave voxtype alone and give omayap a key of its own:

  o.bind("SUPER + CTRL + Y", "Toggle dictation", "~/.local/bin/omayap toggle", { release = true })

The toggle is bound on release: a modifier you are still holding merges into
every letter the transcript types, and SUPER + CTRL + <letter> is a menu
shortcut in Omarchy.

One toggle is enough to use omayap. Push-to-talk needs a key of its own, since
it binds a press and a release, so add F10 only if you want to hold instead of
toggle:

  o.bind("F10", "Start dictation (push-to-talk)", "~/.local/bin/omayap press")
  o.bind("F10", "Stop dictation (push-to-talk)", "~/.local/bin/omayap release", { release = true })

Then run `hyprctl reload`.
VOXTYPE
else
  cat <<'KEYS'

Add these to ~/.config/hypr/bindings.lua, then run `hyprctl reload`:

  o.bind("F9", "Start dictation (push-to-talk)", "~/.local/bin/omayap press")
  o.bind("F9", "Stop dictation (push-to-talk)", "~/.local/bin/omayap release", { release = true })
  o.bind("SUPER + CTRL + X", "Toggle dictation", "~/.local/bin/omayap toggle", { release = true })
KEYS
fi
