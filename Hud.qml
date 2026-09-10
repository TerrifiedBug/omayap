import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "HudModel.js" as Hud
import "Model.js" as Model

// The dictation HUD: a small card while you speak, and the one surface you
// actually look at. Adapted from omakoe (c) 2026 Oliver Kohl, MIT.
//
// It never takes focus and it never covers the cursor, because the text has to
// land in the window that was focused before the key went down.
//
// Two inputs, both from the daemon: the state file says which arrangement to
// draw, and a FIFO of levels drives the meter. The FIFO is why the daemon does
// no work when nothing is watching — it only writes levels while this process
// holds the read end open.
Item {
  id: root

  // The host assigns `service` once, in the overlay loader's onLoaded. With
  // keepLoaded the overlay loads at shell startup, which can happen before the
  // service singleton exists, and a one-shot assignment of null never
  // corrects itself. So the HUD also asks for its own service until it has
  // one: no service means no state, and no state means no card, silently.
  property var service: null
  property var shell: null
  property var manifest: null
  property var resolvedService: null

  property bool hudEnabled: true
  property string hudPosition: "bottom-center"
  property int hudMargin: 64
  property var wave: Hud.emptyWave()
  property real peak: 0
  property bool scanning: false

  readonly property var daemon: service || resolvedService

  readonly property string mode: Hud.hudMode({
    state: Model.hudState(root.daemon ? root.daemon.snapshot : null),
    hud: root.hudEnabled
  })

  // One grid for the whole card. The meter's cells and the bar's cells are the
  // same size and sit on the same pitch, which is what lets a cell travel from
  // one to the other and still read as the cell it was.
  readonly property int pitch: Style.space(Hud.CELL_PITCH)
  readonly property int block: Style.space(Hud.CELL_BLOCK)
  // Two beats to the transcribing state: the meter collapses and reappears as
  // the bar, and then the light runs along it for as long as the model works.
  readonly property bool transcribing: mode === "transcribing"
  readonly property bool morphing: transcribing && !scanning
  readonly property bool showing: mode !== "hidden"
  readonly property bool listening: mode === "recording"
  // A meeting recording: the same meter, on screen for as long as it runs,
  // with the clock and the stop button a notification cannot give you.
  readonly property bool session: mode === "session"
  readonly property bool metering: listening || session
  readonly property string elapsed: root.daemon && root.daemon.snapshot && root.daemon.snapshot.recording
    ? Model.elapsed(root.daemon.snapshot.recording.started, clock.date.getTime())
    : "0:00"

  onTranscribingChanged: {
    if (transcribing) {
      // Read the meter where it stands and pair it up with the bar: every cell
      // takes the cell of the bar nearest to it.
      morph.begin(
        Hud.waveCellPoints(root.wave, { x: 0, y: grid.height / 2, pitch: root.pitch }),
        Hud.scanCellPoints(Hud.WAVE_COLUMNS, { x: 0, y: grid.height / 2, pitch: root.pitch }))
      morphClock.restart()
    } else {
      scanning = false
      morphClock.stop()
      morph.reset()
    }
  }

  // The level stream. `cat` on a FIFO returns at end of file when the daemon
  // closes it, which is once per press, so this process is restarted per press
  // and spends the time in between blocked on open() rather than polling.
  // setpriv keeps it a direct child that dies with the shell.
  // Until the service turns up there is nothing to render and nothing to
  // read levels from. One second is imperceptible at shell startup and the
  // timer stops for good once it has an answer.
  Timer {
    interval: 1000
    repeat: true
    running: root.daemon === null
    triggeredOnStart: true
    onTriggered: root.resolvedService = root.shell && typeof root.shell.serviceFor === "function"
      ? root.shell.serviceFor(root.manifest ? root.manifest.id : "")
      : null
  }

  Process {
    id: levels
    running: root.daemon !== null
    command: ["bash", "-c", "exec setpriv --pdeathsig TERM " + (root.daemon ? root.daemon.cli : "true") + " levels"]

    stdout: SplitParser {
      onRead: function(line) {
        try {
          var frame = JSON.parse(line)
          if (frame.peak !== undefined) root.peak = frame.peak
        } catch (e) {
          // A malformed line is a dropped frame, never a crash.
        }
      }
    }

    onExited: levelsRestart.restart()
  }

  Timer {
    id: levelsRestart
    interval: 500
    onTriggered: levels.running = root.daemon !== null
  }

  // When the last block has landed, the blocks are dropped and the bar takes
  // over in the same frame. They occupy exactly the same cells, so the only
  // thing that changes is the brightness: the bar arrives fully lit and the
  // light starts running through it.
  Timer {
    id: morphClock
    interval: Hud.MORPH_IN_MS + Hud.MORPH_SPREAD_MS + Hud.MORPH_OUT_MS
    onTriggered: {
      root.scanning = true
      morph.reset()
    }
  }

  // Only ticks while something is on screen, so an idle HUD is an idle CPU.
  SystemClock {
    id: clock
    precision: SystemClock.Seconds
    enabled: root.session
  }

  Timer {
    running: root.metering
    interval: 50 // ~20 Hz; 93 columns is a little under 5 s of history
    repeat: true
    onTriggered: root.wave = Hud.pushWave(root.wave, root.peak)
    onRunningChanged: if (running) root.wave = Hud.emptyWave()
  }

  // ------------------------------------------------------------- the card
  PanelWindow {
    id: panel
    visible: root.showing
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "omayap-hud"
    WlrLayershell.layer: WlrLayer.Overlay
    // Never take focus: insertion targets the window focused before dictation
    // began, and stealing it would break the whole premise.
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    // Clicks pass through everywhere except the card itself.
    mask: Region { item: card }

    BorderSurface {
      id: card
      // Placement is a pure function of the surface and the card, so it is
      // unit-tested rather than eyeballed.
      readonly property var place: Hud.hudPosition(
        root.hudPosition, panel.width, panel.height, width, height, root.hudMargin)
      x: place.x
      y: place.y
      // Sized by its content. The meter is exactly as wide as the bar it
      // becomes, so dictation never resizes the card; a meeting recording adds
      // one row under it and nothing else moves.
      width: content.width + Style.space(22)
      height: content.height + Style.space(16)
      color: Util.alpha(Color.background, 0.97)
      borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
      radius: Style.cornerRadius
      opacity: root.showing ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: 120 } }

      // No glyph and no label while dictating: the state is the arrangement of
      // the pixels. A meeting recording is the one case that needs words,
      // because you have to be able to see how long it has been going and stop
      // it without hunting for the bar.
      Column {
        id: content
        anchors.centerIn: parent
        spacing: root.session ? Style.space(6) : 0

      Item {
        id: grid
        width: Hud.WAVE_COLUMNS * root.pitch
        height: Hud.WAVE_STEPS * root.pitch

        // 8-bit meter: hard-quantised steps, discrete cells, mirrored, no
        // smoothing between frames. Cells rather than solid bars because they
        // are what moves — a bar has no pixels to take apart.
        Item {
          id: meter
          anchors.fill: parent
          // Goes at once, with no fade. Every block of the transformation
          // starts on the cell it replaces, at the same size and colour, so
          // swapping one for the other is invisible.
          visible: !root.transcribing

          Repeater {
            model: Hud.WAVE_COLUMNS
            delegate: Repeater {
              id: columnCells
              required property int index
              readonly property int column: index
              model: Hud.WAVE_STEPS
              delegate: Rectangle {
                required property int index
                // A nested Repeater parents its delegates to the same item the
                // outer one does, so the column is read from the Repeater by
                // id — `parent` here is the meter, not the column.
                x: columnCells.column * root.pitch + (root.pitch - root.block) / 2
                y: grid.height / 2 - Hud.WAVE_STEPS * root.pitch / 2
                   + index * root.pitch + (root.pitch - root.block) / 2
                width: root.block
                height: root.block
                visible: Hud.columnLit(root.wave[columnCells.column], index, Hud.WAVE_STEPS)
                color: Color.urgent
                antialiasing: false
                radius: 0
              }
            }
          }
        }

        // The bar, once the blocks have finished becoming it.
        Scanner {
          id: scanner
          anchors.fill: parent
          visible: root.scanning
          cols: Hud.WAVE_COLUMNS
          pitch: root.pitch
          block: root.block
          barColor: Color.accent
          running: root.scanning
        }

        PixelMorph {
          id: morph
          anchors.fill: parent
          block: root.block
          // accent from the moment they start moving, in one cut rather than a
          // fade: releasing the key stops the recording, so urgent would be
          // saying something untrue for as long as the blocks were in the air.
          blockColor: Color.accent
          running: root.morphing
        }
      }

      // ---- the recording footer
      Row {
        id: footer
        anchors.horizontalCenter: parent.horizontalCenter
        visible: root.session
        height: visible ? implicitHeight : 0
        spacing: Style.space(8)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: "\uf111 " + root.elapsed  // nf-fa-circle
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        PanelActionButton {
          anchors.verticalCenter: parent.verticalCenter
          iconText: "\uf00d"  // nf-fa-times
          tooltipText: "Stop recording"
          bordered: true
          foreground: Color.foreground
          // The one interactive pixel on the HUD. The window mask covers the
          // card only, so everything around it still clicks through.
          onClicked: if (root.daemon) root.daemon.record()
        }
      }
      }
    }
  }
}
