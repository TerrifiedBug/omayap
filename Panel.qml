import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Model.js" as Model

// The popup: what is happening, one button to record a meeting, and the three
// settings worth a click.
//
// Everything else lives in ~/.config/omayap/config.json, including the
// recordings directory and the excluded-app list, because a text field in a
// bar popup is a worse editor than a text editor.
Panel {
  id: root
  moduleName: "io.github.terrifiedbug.omayap"
  manageIpc: false

  property var anchorItem: null

  // The bar tracks the widget mounted in its slot (BarWidget.qml), not this
  // nested panel, so everything the bar identifies a panel by must be that
  // widget.
  property var hostWidget: null

  // Only while the panel is open: a clock nobody is looking at is a wakeup
  // every second for nothing.
  property double nowMs: Date.now()

  readonly property var barIdentity: hostWidget || root
  readonly property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function" ? bar.shell.serviceFor(moduleName) : null
  readonly property var snapshot: service ? service.snapshot : Model.DEFAULT_STATE
  readonly property var config: service ? service.config : Model.DEFAULT_CONFIG
  readonly property bool recording: !!snapshot.recording

  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property color dimmedForeground: Qt.darker(contentForeground, 1.8)
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function toggleSetting(key) {
    if (root.service) root.service.setConfig(key, !root.config[key])
  }

  Timer {
    interval: 1000
    repeat: true
    running: root.opened && root.recording
    triggeredOnStart: true
    onTriggered: root.nowMs = Date.now()
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(320))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(8)

        PanelSectionHeader {
          text: "OmaYap"
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: Model.stateLine(root.snapshot, root.nowMs, root.service ? root.service.hint : "")
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.body
          // A key combination makes this line long and unpredictable, and the
          // key is the whole point of it, so it wraps rather than eliding.
          wrapMode: Text.WordWrap
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: "parakeet-tdt-ctc-110m · on device"
          color: root.dimmedForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }

        Button {
          width: parent.width
          text: Model.recordingLabel(root.snapshot, root.nowMs)
          active: root.recording
          bordered: true
          tooltipText: root.recording
            ? "Stop and transcribe"
            : "Record microphone and system audio as two tracks"
          onClicked: if (root.service) root.service.record()
        }

        PanelSeparator {
          width: parent.width
          foreground: root.contentForeground
        }

        Toggle {
          width: parent.width
          label: "Enter after dictating"
          description: "Sends the line as soon as it is typed"
          checked: root.config.newline_after_dictation === true
          onClicked: root.toggleSetting("newline_after_dictation")
        }

        PanelSeparator {
          width: parent.width
          foreground: root.contentForeground
        }

        Toggle {
          width: parent.width
          label: "Meeting detection"
          description: "Offer to record when another app takes the mic"
          checked: root.config.meeting_detection === true
          onClicked: root.toggleSetting("meeting_detection")
        }

        Toggle {
          width: parent.width
          label: "Auto-record calls"
          description: "Start without asking"
          checked: root.config.meeting_auto_record === true
          onClicked: root.toggleSetting("meeting_auto_record")
        }

        Toggle {
          width: parent.width
          label: "Keep audio"
          description: "Leave the two tracks beside the transcript"
          checked: root.config.keep_audio === true
          onClicked: root.toggleSetting("keep_audio")
        }

        PanelSeparator {
          width: parent.width
          foreground: root.contentForeground
        }

        Button {
          width: parent.width
          text: "Recordings: " + root.config.recordings_dir
          tooltipText: "Open the recordings folder"
          bordered: true
          onClicked: if (root.service) root.service.openRecordings()
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: "~/.config/omayap/config.json"
          color: root.dimmedForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }
}
