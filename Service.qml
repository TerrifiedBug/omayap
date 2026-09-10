import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// The shell's view of the daemon: two watched files in, four commands out.
//
// There is no socket and no IPC handler here on purpose. The daemon's public
// interface is `bin/omayap`, which the keybindings already use, so the panel
// pressing a button and the user pressing F9 go through exactly the same door.
// State comes back as one line of JSON in $XDG_RUNTIME_DIR, rewritten in
// place on every transition.
Item {
  id: root

  property var shell: null
  property var manifest: null

  // Named snapshot rather than state: Item already has a `state` property.
  property var snapshot: Model.DEFAULT_STATE
  property var config: Model.DEFAULT_CONFIG

  // The client, from wherever this plugin happens to be installed.
  readonly property string cli: Qt.resolvedUrl("bin/omayap").toString().replace(/^file:\/\//, "")
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") + "/omayap"
  readonly property string configDir: Quickshell.env("HOME") + "/.config/omayap"
  readonly property string bindingsFile: Quickshell.env("HOME") + "/.config/hypr/bindings.lua"

  // What the idle state should tell the user to press. Read from the Hyprland
  // config, because the plugin does not own the keybinding and cannot assume
  // the README's suggestion was taken.
  property var keys: Model.dictationKeys("")
  readonly property string hint: Model.dictationHint(root.keys)

  function record(): void {
    Quickshell.execDetached([root.cli, "record"])
  }

  function setConfig(key, value) {
    configProc.command = [root.cli, "config", key, String(value)]
    configProc.running = true
  }

  function openRecordings(): void {
    Quickshell.execDetached(["xdg-open", root.config.recordings_dir.replace(/^~/, Quickshell.env("HOME"))])
  }

  // watchChanges reports the change; the re-read is the handler's job.
  FileView {
    id: stateFile
    path: root.runtimeDir + "/state"
    watchChanges: true
    printErrors: false

    onFileChanged: reload()
    onLoaded: root.snapshot = Model.parseState(text(), root.snapshot)
  }

  FileView {
    id: configFile
    path: root.configDir + "/config.json"
    watchChanges: true
    printErrors: false

    onFileChanged: reload()
    onLoaded: root.config = Model.parseConfig(text(), root.config)
  }

  // `omayap config` writes through a temporary file and renames, which moves
  // the file out from under a watch on the file itself. Watching the directory
  // is what notices the replacement.
  FileView {
    id: configDirView
    path: root.configDir
    watchChanges: true
    printErrors: false

    onFileChanged: configFile.reload()
  }

  FileView {
    id: bindingsView
    path: root.bindingsFile
    watchChanges: true
    printErrors: false

    onFileChanged: reload()
    onLoaded: root.keys = Model.dictationKeys(text())
  }

  Process {
    id: configProc
    onExited: configFile.reload()
  }
}
