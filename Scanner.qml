import QtQuick
import "HudModel.js" as Model

// The bar the meter turns into while the model is working: a row of cells with a
// light swung along it, back and forth, the way a Knight Rider scanner does.
//
// It says the same thing a word did — something is happening, wait — without
// spelling anything out, and it says it in the alphabet the rest of the card is
// already written in. It also has no end: a word arrives and then sits there
// looking finished, while a light that is still moving cannot be mistaken for a
// transcription that has stalled.
//
// Colours arrive as properties rather than from `qs.Commons`, so this file
// loads outside the shell and the sweep can be rendered to a PNG and looked at
// frame by frame. Hud.qml is the one that knows about the theme.
Item {
  id: root

  property int cols: 0
  property int pitch: 0
  property int block: 0
  property color barColor: "transparent"

  property bool running: false

  // Where the light is, in columns. Everything else is a function of it.
  property real head: 0

  // Eased at the turns, which is what makes it swing rather than bounce: the
  // light slows as it reaches an end, turns, and gathers speed again.
  SequentialAnimation {
    running: root.running
    loops: Animation.Infinite
    NumberAnimation {
      target: root; property: "head"; to: root.cols - 1
      duration: Model.SCAN_MS; easing.type: Easing.InOutSine
    }
    NumberAnimation {
      target: root; property: "head"; to: 0
      duration: Model.SCAN_MS; easing.type: Easing.InOutSine
    }
  }

  Repeater {
    model: root.cols

    delegate: Repeater {
      id: column
      required property int index
      readonly property int col: index
      model: Model.SCAN_ROWS

      // Drawn from the same arithmetic as Model.scanCellPoints, which is where
      // the morph's blocks are told to land. The bar and the blocks that become
      // it have to agree cell for cell, or the handover between them shows.
      delegate: Rectangle {
        required property int index
        x: column.col * root.pitch + (root.pitch - root.block) / 2
        y: root.height / 2 + (index - Model.SCAN_ROWS / 2 + 0.5) * root.pitch
           - root.block / 2
        width: root.block
        height: root.block
        color: Qt.rgba(root.barColor.r, root.barColor.g, root.barColor.b,
                       Model.scanAlpha(column.col, root.head))
        antialiasing: false
        radius: 0
      }
    }
  }
}
