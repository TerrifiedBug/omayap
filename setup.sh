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

PATH=/usr/bin:/bin
export PATH
IFS=$' \t\n'
unset CDPATH BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
unset PIP_INDEX_URL PIP_EXTRA_INDEX_URL PIP_CONFIG_FILE
unset "${!PIP_@}"
PIP_CONFIG_FILE=/dev/null
export PIP_CONFIG_FILE

stat_bin=/usr/bin/stat
readlink_bin=/usr/bin/readlink

tool() {
  local name=$1 candidate path dir owner mode group_mode other_mode

  for candidate in "/usr/bin/$name" "/bin/$name"; do
    [[ -f $candidate && -x $candidate ]] || continue
    path=$("$readlink_bin" -f -- "$candidate") || continue
    case $path in
    /usr/bin/* | /bin/*) ;;
    *) continue ;;
    esac
    if ! read -r owner mode < <("$stat_bin" -L -c '%u %a' -- "$path") ||
      [[ ! $owner =~ ^[0-9]+$ || ! $mode =~ ^[0-7]{3,4}$ ]]; then
      continue
    fi
    group_mode=${mode: -2:1}
    other_mode=${mode: -1}
    if [[ $owner != 0 ]] ||
      ((10#$group_mode & 2 || 10#$other_mode & 2)); then
      continue
    fi

    dir=${path%/*}
    while :; do
      if [[ ! -d $dir ]] ||
        ! read -r owner mode < <("$stat_bin" -L -c '%u %a' -- "$dir") ||
        [[ ! $owner =~ ^[0-9]+$ || ! $mode =~ ^[0-7]{3,4}$ ]]; then
        printf 'omayap: unsafe tool directory %s for %s\n' "$dir" "$name" >&2
        exit 1
      fi
      group_mode=${mode: -2:1}
      other_mode=${mode: -1}
      if [[ $owner != 0 ]] ||
        ((10#$group_mode & 2 || 10#$other_mode & 2)); then
        printf 'omayap: unsafe tool directory %s for %s\n' "$dir" "$name" >&2
        exit 1
      fi
      [[ $dir == / ]] && break
      dir=${dir%/*}
      [[ -n $dir ]] || dir=/
    done

    printf '%s\n' "$path"
    return
  done

  printf 'omayap: required tool %s is unavailable or unsafe\n' "$name" >&2
  exit 1
}

stat_bin=$(tool stat)
readlink_bin=$(tool readlink)
dirname_bin=$(tool dirname)
sha256sum_bin=$(tool sha256sum)
cut_bin=$(tool cut)
rm_bin=$(tool rm)
mv_bin=$(tool mv)
mktemp_bin=$(tool mktemp)
mkdir_bin=$(tool mkdir)
python3_bin=$(tool python3)
curl_bin=$(tool curl)
tar_bin=$(tool tar)
cp_bin=$(tool cp)
chmod_bin=$(tool chmod)
ln_bin=$(tool ln)
cat_bin=$(tool cat)
systemctl_bin=$(tool systemctl)

here=$("$dirname_bin" "$("$readlink_bin" -f "$0")")
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
  got=$("$sha256sum_bin" "$path" | "$cut_bin" -d' ' -f1)
  if [[ $got != "$want" ]]; then
    echo "omayap: checksum mismatch for $path" >&2
    echo "  expected $want" >&2
    echo "  got      $got" >&2
    "$rm_bin" -f "$path"
    exit 1
  fi
}

echo "omayap setup"

refuse() {
  printf 'omayap: refusing unsafe path %s\n' "$1" >&2
  exit 1
}

# ---- preflight: everything this script will write to, checked before it
# writes anything. A setup that rebuilds the venv and then refuses to install
# a unit file has taken a working install apart for nothing.
#
# Home is resolved once, so a symlinked home directory is fine and a symlink
# anywhere below it is not: after this, a path that resolves to itself has no
# links on it at all, which is the cheap way to check a whole ancestry in
# shell.
home=$("$readlink_bin" -f "$HOME") || refuse "$HOME"
share=$home/.local/share/omayap
models=$share/models
model_dir=$models/parakeet-tdt-ctc-110m
config_dir=$home/.config/omayap
unit_dir=$home/.config/systemd/user
config_file=$config_dir/config.json
cli_dir=$home/.local/bin
cli_file=$cli_dir/omayap
unit_file=$unit_dir/omayap.service

# Built one component at a time, each checked before the next is created, so a
# symlink partway down is refused rather than followed. `mkdir -p` on the whole
# path would have created directories at the far end of a link before anything
# had a chance to object.
make_dir() {
  local full=$1 walked=$home rest=${1#"$home"/} part
  [[ $full == "$home"/* ]] || refuse "$full"
  while [[ -n $rest ]]; do
    part=${rest%%/*}
    rest=${rest#"$part"}
    rest=${rest#/}
    walked=$walked/$part
    [[ ! -L $walked ]] || refuse "$walked"
    if [[ ! -e $walked ]]; then
      "$mkdir_bin" "$walked"
    fi
    [[ -d $walked && ! -L $walked ]] || refuse "$walked"
    [[ -O $walked ]] || refuse "$walked"
  done
}

for directory in "$share" "$models" "$config_dir" "$cli_dir" "$unit_dir"; do
  make_dir "$directory"
done

# A file or directory we write is ours to write or absent. A symlink in any of
# these places would send the write somewhere this script never claimed to
# touch.
for target in "$config_file" "$unit_file" "$share/venv" "$model_dir" \
  "$models/model.tar.bz2" "$models/silero_vad.onnx"; do
  [[ ! -L $target ]] || refuse "$target"
done
for target in "$config_file" "$unit_file"; do
  [[ ! -e $target || -f $target ]] || refuse "$target"
done

# The CLI is a symlink of ours, or nothing at all. Where it currently points
# does not matter: reinstalling from another checkout is an ordinary thing to
# do. A regular file by that name belongs to somebody else.
if [[ -e $cli_file || -L $cli_file ]]; then
  [[ -L $cli_file && $("$stat_bin" -c '%u' -- "$cli_file") == "$UID" ]] ||
    refuse "$cli_file"
fi

# ---- the venv: sherpa-onnx and nothing else
lock_digest=$("$sha256sum_bin" "$here/requirements.lock" | "$cut_bin" -d' ' -f1)
lock_marker=$share/venv/.omayap-lock
marker_digest=
if [[ -r $lock_marker ]]; then
  marker_digest=$("$cat_bin" "$lock_marker" 2>/dev/null) || marker_digest=
fi
if [[ $marker_digest != "$lock_digest" ]] ||
  [[ ! -x $share/venv/bin/python ]] ||
  ! "$share/venv/bin/python" -c 'import sherpa_onnx' 2>/dev/null; then
  "$rm_bin" -rf "$share/venv"
  say "creating $share/venv"
  "$mkdir_bin" -p "$share"
  "$python3_bin" -m venv "$share/venv"
  say "installing sherpa-onnx"
  # A version only names a release. The hashes bind the installed bytes.
  "$share/venv/bin/python" -m pip install \
    --quiet \
    --require-hashes \
    --only-binary=:all: \
    --no-cache-dir \
    --disable-pip-version-check \
    --index-url https://pypi.org/simple \
    -r "$here/requirements.lock"
  lock_tmp=$("$mktemp_bin" "$share/venv/.omayap-lock.XXXXXX")
  printf '%s\n' "$lock_digest" >"$lock_tmp"
  "$mv_bin" -f "$lock_tmp" "$lock_marker"
fi

# ---- the models: 126 MB of int8 Parakeet, plus Silero for segmenting meetings
"$mkdir_bin" -p "$models"
if [[ ! -f $model_dir/model.int8.onnx ]]; then
  say "downloading the speech model (126 MB)"
  "$curl_bin" --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
    --max-time 600 -o "$models/model.tar.bz2" "$model_url"
  verify "$models/model.tar.bz2" "$model_sha256"
  "$mkdir_bin" -p "$model_dir"
  "$tar_bin" -xjf "$models/model.tar.bz2" --strip-components=2 -C "$model_dir" \
    "./$tarball_root/model.int8.onnx" "./$tarball_root/tokens.txt" "./$tarball_root/test_wavs"
  "$rm_bin" -f "$models/model.tar.bz2"
fi
if [[ ! -f $models/silero_vad.onnx ]]; then
  say "downloading the voice activity model"
  "$curl_bin" --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
    --max-time 300 -o "$models/silero_vad.onnx" "$vad_url"
  verify "$models/silero_vad.onnx" "$vad_sha256"
fi

# ---- config: yours to edit, never overwritten
if [[ ! -e $config_file ]]; then
  say "writing $config_file"
  config_tmp=$("$mktemp_bin" "$config_dir/.config.json.XXXXXX")
  "$cp_bin" "$here/config.template.json" "$config_tmp"
  "$chmod_bin" 600 "$config_tmp"
  "$mv_bin" -f "$config_tmp" "$config_file"
fi

# ---- the CLI on PATH, so a keybinding is one word
"$ln_bin" -sfn "$here/bin/omayap" "$cli_file"

# ---- the service: dies with the graphical session, comes back with it
unit_tmp=$("$mktemp_bin" "$unit_dir/.omayap.service.XXXXXX")
"$cat_bin" >"$unit_tmp" <<UNIT
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
"$chmod_bin" 644 "$unit_tmp"
"$mv_bin" -f "$unit_tmp" "$unit_file"

"$systemctl_bin" --user daemon-reload
"$systemctl_bin" --user enable omayap.service
"$systemctl_bin" --user restart omayap.service
say "omayap.service enabled"

if command -v voxtype >/dev/null 2>&1; then
  "$cat_bin" <<'VOXTYPE'

voxtype is on PATH, so Omarchy's bindings already hold F9 and SUPER + CTRL + X,
and a second bind on the same key does not replace the first. Give omayap a key
of its own, then run `hyprctl reload`:

  o.bind("SUPER + CTRL + Y", "Toggle dictation", "~/.local/bin/omayap toggle", { release = true })

Bind on release: a modifier you are still holding merges into every letter the
transcript types. Add F10 as well if you want push-to-talk, or run
`omarchy-voxtype-remove` if you would rather have F9 back. See the README.
VOXTYPE
else
  "$cat_bin" <<'KEYS'

Add these to ~/.config/hypr/bindings.lua, then run `hyprctl reload`:

  o.bind("F9", "Start dictation (push-to-talk)", "~/.local/bin/omayap press")
  o.bind("F9", "Stop dictation (push-to-talk)", "~/.local/bin/omayap release", { release = true })
  o.bind("SUPER + CTRL + X", "Toggle dictation", "~/.local/bin/omayap toggle", { release = true })
KEYS
fi
