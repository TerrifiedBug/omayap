import QtQuick
import QtQuick.Shapes

// yap's own mark, so the bar button is omayap rather than a microphone that
// looks like the system's audio settings.
//
// It is Lucide `speech`: a head in profile with two sound arcs. The path data
// is copied verbatim from yap's menu-bar icon (Sources/yap/UI/StatusIcon.swift),
// which is the point — one mark, one product, whichever machine it is on.
// Stroke 2 for the same reason yap chose it: 1.5 reads spindly beside the
// battery and network outlines, and 2.5 closes up the head at bar size.
//
// Drawn at its 24-unit design size and scaled by transform, never by
// recomputing coordinates, so the curves stay smooth at any bar height.
Item {
  id: root

  property color color: "white"
  property real iconSize: 16

  readonly property real scale24: iconSize / 24

  implicitWidth: iconSize
  implicitHeight: iconSize

  Shape {
    id: mark
    width: 24
    height: 24
    // The curve renderer keeps the arcs clean at 16 px; the geometry renderer
    // flattens them into visible facets.
    preferredRendererType: Shape.CurveRenderer
    transform: Scale { xScale: root.scale24; yScale: root.scale24 }

    // ---- the head
    ShapePath {
      strokeColor: root.color
      fillColor: "transparent"
      strokeWidth: 2
      capStyle: ShapePath.RoundCap
      joinStyle: ShapePath.RoundJoin

      PathSvg {
        path: "M8.8 20v-4.1l1.9.2a2.3 2.3 0 0 0 2.164-2.1V8.3A5.37 5.37 0 0 0 2 8.25c0 2.8.656 3.054 1 4.55a5.77 5.77 0 0 1 .029 2.758L2 20"
      }
    }

    // ---- the outer sound arc
    ShapePath {
      strokeColor: root.color
      fillColor: "transparent"
      strokeWidth: 2
      capStyle: ShapePath.RoundCap
      joinStyle: ShapePath.RoundJoin

      PathSvg {
        path: "M19.8 17.8a7.5 7.5 0 0 0 .003-10.603"
      }
    }

    // ---- the inner sound arc
    ShapePath {
      strokeColor: root.color
      fillColor: "transparent"
      strokeWidth: 2
      capStyle: ShapePath.RoundCap
      joinStyle: ShapePath.RoundJoin

      PathSvg {
        path: "M17 15a3.5 3.5 0 0 0-.025-4.975"
      }
    }
  }
}
