#!/bin/bash
# Undo setup.sh. Nothing here needs sudo, and it never touches your recordings.
#
# By default it stops the service and unhooks the CLI, leaving the venv and the
# models so a reinstall is instant. `--purge` deletes those too.
#
# Run it before `omarchy plugin remove`, because removing a plugin runs nothing
# of the plugin's own.

set -euo pipefail

here=$(dirname "$(readlink -f "$0")")
share=$HOME/.local/share/omayap
config_dir=$HOME/.config/omayap
unit=$HOME/.config/systemd/user/omayap.service
purge=0

for arg in "$@"; do
  case $arg in
  --purge) purge=1 ;;
  *)
    echo "usage: uninstall.sh [--purge]" >&2
    exit 1
    ;;
  esac
done

say() { printf '  %s\n' "$*"; }

echo "omayap uninstall"

if systemctl --user list-unit-files omayap.service >/dev/null 2>&1; then
  systemctl --user disable --now omayap.service 2>/dev/null || true
  say "stopped and disabled omayap.service"
fi
if [[ -f $unit ]]; then
  rm -f "$unit"
  systemctl --user daemon-reload
  say "removed $unit"
fi

# Only our own symlink, never someone else's binary of the same name.
if [[ -L $HOME/.local/bin/omayap ]] &&
  [[ $(readlink -f "$HOME/.local/bin/omayap") == "$here/bin/omayap" ]]; then
  rm -f "$HOME/.local/bin/omayap"
  say "removed ~/.local/bin/omayap"
fi

rm -rf "${XDG_RUNTIME_DIR:-/run/user/$UID}/omayap"

if ((purge)); then
  rm -rf "$share" "$config_dir"
  say "removed $share and $config_dir"
else
  [[ -d $share ]] && say "kept $share ($(du -sh "$share" 2>/dev/null | cut -f1) of venv and models)"
  [[ -d $config_dir ]] && say "kept $config_dir"
fi

recordings=$(python3 - <<'PY' 2>/dev/null || true
import json, pathlib
path = pathlib.Path.home() / ".config/omayap/config.json"
try:
    value = json.loads(path.read_text()).get("recordings_dir", "~/Recordings")
except Exception:
    value = "~/Recordings"
print(pathlib.Path(str(value)).expanduser())
PY
)
[[ -n $recordings ]] && say "your recordings are untouched in $recordings"

cat <<'REST'

Still yours to do:

  1. omarchy plugin remove io.github.terrifiedbug.omayap
  2. delete omayap's o.bind lines from ~/.config/hypr/bindings.lua
  3. hyprctl reload
REST
