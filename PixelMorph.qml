import QtQuick
import "HudModel.js" as Model

// The meter's pixels moving to the word's pixels.
//
// One block per lit cell of the frozen meter — the thing that moves is the
// waveform's own pixels, so every one of them gets a destination and travels
// to it. What they are travelling into is the scanner's bar.
//
// It travels in two straight legs. Every cell collapses to the centre of the
// card at once, so that for one frame the whole waveform is a single lit pixel,
// and the word is then fired back out of that point left to right. Linear, no
// overshoot and no arc on either leg: these are the same pixels being moved,
// and anything springy would make them read as new objects arriving.
//
// Nothing fades. A block is opaque from the moment it exists to the moment it
// is thrown away, and it starts exactly on the meter cell it replaces, at the
// same size, so the handover is invisible. A loud meter has more cells than the
// word has pixels; the extras converge on the pixel nearest them and stack up
// behind it, hidden by the block in front rather than blended away.
//
// Colours arrive as properties rather than from `qs.Commons`, so this file
// loads outside the shell and the transformation can be rendered to a PNG and
// looked at frame by frame. Hud.qml is the one that knows about the theme.
Item {
  id: root

  // [{ from, to }], one per cell of the meter. Set by begin(), never bound:
  // the pairing has to be settled before the delegates exist, because each
  // block reads where it starts from as it is created and there is no second
  // chance to tell it.
  property var pairs: []

  property int block: 0
  property color blockColor: "transparent"

  property bool running: false

  // Pair the meter's cells with the bar's cells, then hand over the pairs.
  function begin(sourcePoints, targetPoints) {
    root.pairs = Model.pairCells(sourcePoints, targetPoints)
  }

  function reset() {
    // Dropping the pairs destroys the blocks, so the next transformation starts
    // from the meter again rather than from where this one finished.
    root.pairs = []
  }

  Repeater {
    model: root.pairs

    delegate: Rectangle {
      id: cell
      required property var modelData

      width: root.block
      height: root.block
      color: root.blockColor
      antialiasing: false
      radius: 0

      // Assigned rather than bound: the animation takes ownership of x and y,
      // and a binding it has to break is a binding that was lying.
      Component.onCompleted: {
        x = modelData.from.x - root.block / 2
        y = modelData.from.y - root.block / 2
      }

      SequentialAnimation {
        running: root.running

        // In: the whole meter to one point, together and unstaggered. A
        // stagger here would trickle rather than collapse, and the collapse is
        // the flash.
        ParallelAnimation {
          NumberAnimation {
            target: cell; property: "x"; to: root.width / 2 - root.block / 2
            duration: Model.MORPH_IN_MS; easing.type: Easing.Linear
          }
          NumberAnimation {
            target: cell; property: "y"; to: root.height / 2 - root.block / 2
            duration: Model.MORPH_IN_MS; easing.type: Easing.Linear
          }
        }

        // Out: left to right by destination, so the word is written out of the
        // point rather than appearing around it all at once. A block still
        // waiting its turn sits in the pile at the centre, which is where it
        // reads as being fired from.
        PauseAnimation {
          duration: Model.writeDelay(cell.modelData.to.x, root.width, Model.MORPH_SPREAD_MS)
        }

        ParallelAnimation {
          NumberAnimation {
            target: cell; property: "x"; to: cell.modelData.to.x - root.block / 2
            duration: Model.MORPH_OUT_MS; easing.type: Easing.Linear
          }
          NumberAnimation {
            target: cell; property: "y"; to: cell.modelData.to.y - root.block / 2
            duration: Model.MORPH_OUT_MS; easing.type: Easing.Linear
          }
        }
      }
    }
  }
}
