import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Bar entry point: the button in the bar, and the host for Panel.qml.
//
// The button keeps no state of its own. Everything it draws comes from the
// service's copy of the daemon's state file, so the glyph, the tooltip and the
// panel can never disagree with what the daemon is actually doing — and two
// bars on two monitors always say the same thing.
BarWidget {
  id: root
  moduleName: "io.github.terrifiedbug.omayap"

  // Ticked only while a meeting is recording, which is the one thing on this
  // button that changes without the daemon writing anything.
  property double nowMs: Date.now()

  readonly property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function" ? bar.shell.serviceFor(moduleName) : null
  readonly property var snapshot: service ? service.snapshot : Model.DEFAULT_STATE
  readonly property string mode: Model.mode(snapshot)
  // Anything worth a dot on the mark: speaking, recording, or decoding.
  readonly property bool busy: mode === "listening" || mode === "recording"
    || mode === "transcribing" || mode === "session"

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function toggle() {
    if (panelLoader.item) panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  Timer {
    id: elapsedTimer
    interval: 1000
    repeat: true
    running: root.mode === "recording"
    triggeredOnStart: true
    onTriggered: root.nowMs = Date.now()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // yap's own mark rather than a microphone glyph: a mic at bar size is the
    // system's audio icon, and two identical icons in one bar tell you nothing.
    // The dot says something is running, which a glyph swap could not do
    // without changing the shape people learn to look for.
    iconComponent: Component {
      Item {
        YapIcon {
          id: mark
          anchors.centerIn: parent
          iconSize: Style.space(13)
          // The button's own foreground, which the bar host sets and keeps in
          // step with the theme. `bar.barForeground` exists on the real bar
          // but not on the facade a third-party widget is handed.
          color: button.foreground
        }

        Rectangle {
          // Bottom-right of the mark, the corner yap badges too.
          x: mark.x + mark.width - width
          y: mark.y + mark.height - height
          width: Math.max(3, Style.space(4))
          height: width
          radius: width / 2
          visible: root.busy
          color: root.mode === "recording" ? Color.urgent : Color.accent
          antialiasing: true
        }
      }
    }
    // Lit while something is running, dimmed while the model is still coming
    // off disk. Nothing about a failed model is urgent enough to flash.
    active: root.mode === "listening" || root.mode === "recording"
    dimmed: root.mode === "loading"
    tooltipText: Model.stateLine(root.snapshot, root.nowMs, root.service ? root.service.hint : "")

    onPressed: function(b) {
      if (b === Qt.LeftButton) root.toggle()
    }
  }
}
