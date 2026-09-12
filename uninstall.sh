#!/bin/bash
# Undo setup.sh. Nothing here needs sudo, and it never touches your recordings.
#
# By default it stops the service and unhooks the CLI, leaving the venv and the
# models so a reinstall is instant. `--purge` deletes those too.
#
# Run it before `omarchy plugin remove`, because removing a plugin runs nothing
# of the plugin's own.

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
systemctl_bin=$(tool systemctl)
rm_bin=$(tool rm)
du_bin=$(tool du)
cut_bin=$(tool cut)
python3_bin=$(tool python3)
cat_bin=$(tool cat)

here=$("$dirname_bin" "$("$readlink_bin" -f "$0")")
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

if "$systemctl_bin" --user list-unit-files omayap.service >/dev/null 2>&1; then
  "$systemctl_bin" --user disable --now omayap.service 2>/dev/null || true
  say "stopped and disabled omayap.service"
fi
if [[ -f $unit ]]; then
  "$rm_bin" -f "$unit"
  "$systemctl_bin" --user daemon-reload
  say "removed $unit"
fi

# Only our own symlink, never someone else's binary of the same name.
if [[ -L $HOME/.local/bin/omayap ]] &&
  [[ $("$readlink_bin" -f "$HOME/.local/bin/omayap") == "$here/bin/omayap" ]]; then
  "$rm_bin" -f "$HOME/.local/bin/omayap"
  say "removed ~/.local/bin/omayap"
fi

"$rm_bin" -rf "${XDG_RUNTIME_DIR:-/run/user/$UID}/omayap"

if ((purge)); then
  "$rm_bin" -rf "$share" "$config_dir"
  say "removed $share and $config_dir"
else
  [[ -d $share ]] && say "kept $share ($("$du_bin" -sh "$share" 2>/dev/null | "$cut_bin" -f1) of venv and models)"
  [[ -d $config_dir ]] && say "kept $config_dir"
fi

recordings=$("$python3_bin" - <<'PY' 2>/dev/null || true
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

"$cat_bin" <<'REST'

Still yours to do:

  1. omarchy plugin remove io.github.terrifiedbug.omayap
  2. delete omayap's o.bind lines from ~/.config/hypr/bindings.lua
  3. hyprctl reload
REST
