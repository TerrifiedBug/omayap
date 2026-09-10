// Pure presentation logic for the dictation HUD. No QML imports, so
// `node --test` can exercise it directly and a mistake here cannot take the
// shell down with it.
//
// Adapted from omakoe (c) 2026 Oliver Kohl, MIT. The daemon behind it changed;
// the pixels did not.

// The vocabulary the HUD renders. The daemon's words map onto these.
var STATES = ["setup", "idle", "recording", "transcribing", "session", "error"]

// The glyphs, the labels and the tooltips all went with the bar widget and
// then with the card's caption. Nothing renders text on the HUD any more: the
// state is the arrangement of the pixels, so there is nothing left here to
// name it with.

// ---------------------------------------------------------------- the HUD

// Two states, because two is all the daemon tells us. Its state file reports
// idle, listening and transcribing — never whether the text was typed or
// copied. Rather than invent a success card from information we do not have,
// there is none: the words appearing in your window are the confirmation.
//
// The two states are the same pixels in two arrangements. Nothing is labelled
// and nothing is swapped out: the meter's cells rearrange themselves into the
// word, and back.
function hudMode(snapshot, now) {
  if (snapshot.hud === false) return "hidden"
  if (snapshot.state === "recording") return "recording"
  if (snapshot.state === "transcribing") return "transcribing"
  // A meeting recording: the same meter, plus a clock and a way to stop it.
  if (snapshot.state === "session") return "session"
  if (snapshot.state === "error") return "error"
  return "hidden"
}

// ------------------------------------------------------------- the waveform

// The meter is exactly as many columns wide as the word it becomes, so the
// transformation happens in place rather than the card's contents jumping
// sideways. The word's width is what sets this, not the history it buys: at
// the HUD's ~20 Hz, 93 columns happens to be about 4.7 seconds.
var WAVE_COLUMNS = 93
var WAVE_STEPS = 8

// Quantise a 0..1 peak to one of WAVE_STEPS discrete heights. Hard steps, never
// interpolated: the staircase is the aesthetic (design.md section 4). Silence is
// step 1, not 0 — a flat empty box reads as a broken microphone.
function waveStep(peak) {
  var v = typeof peak === "number" && isFinite(peak) ? peak : 0
  if (v < 0) v = 0
  if (v > 1) v = 1
  // Peaks are tiny for normal speech, so weight the low end where the signal is.
  var scaled = Math.sqrt(v)
  var step = Math.round(scaled * WAVE_STEPS)
  if (step < 1) step = 1
  if (step > WAVE_STEPS) step = WAVE_STEPS
  return step
}

// A fixed-length ring of steps, oldest first, so the card never resizes.
function emptyWave() {
  var out = []
  for (var i = 0; i < WAVE_COLUMNS; i++) out.push(1)
  return out
}

function pushWave(wave, peak) {
  var next = wave.slice(1)
  next.push(waveStep(peak))
  return next
}

// ------------------------------------------------------------- placement

// Dead centre covers what you are looking at, so the default sits near the
// bottom edge — the same reasoning as Omarchy's own OSD.
var POSITIONS = ["bottom-center", "center", "top-center",
                 "bottom-left", "bottom-right", "top-left", "top-right"]
var DEFAULT_POSITION = "bottom-center"

function isPosition(name) {
  return POSITIONS.indexOf(name) !== -1
}

// Pure: where the card goes, given the surface and the card. Returns integers
// so the card never lands on a half-pixel and renders blurry. Clamped, so a
// silly margin or a tiny screen can never push it off the edge.
function hudPosition(name, panelW, panelH, cardW, cardH, margin) {
  var pos = isPosition(name) ? name : DEFAULT_POSITION
  var m = typeof margin === "number" && isFinite(margin) && margin >= 0 ? margin : 0
  var centreX = (panelW - cardW) / 2
  var centreY = (panelH - cardH) / 2
  var left = m
  var right = panelW - cardW - m
  var top = m
  var bottom = panelH - cardH - m

  var x, y
  switch (pos) {
    case "center":        x = centreX; y = centreY; break
    case "top-center":    x = centreX; y = top;     break
    case "bottom-left":   x = left;    y = bottom;  break
    case "bottom-right":  x = right;   y = bottom;  break
    case "top-left":      x = left;    y = top;     break
    case "top-right":     x = right;   y = top;     break
    default:              x = centreX; y = bottom;  break // bottom-center
  }

  // Never off-screen, whatever the margin or however small the surface.
  var maxX = Math.max(0, panelW - cardW)
  var maxY = Math.max(0, panelH - cardH)
  return {
    x: Math.round(Math.min(Math.max(x, 0), maxX)),
    y: Math.round(Math.min(Math.max(y, 0), maxY))
  }
}


// ------------------------------------------------------- the transformation

// One pitch for everything on the card. The meter's cells and the word's
// blocks are the same size and sit on the same grid, which is what lets a cell
// travel from one to the other and still look like the cell it was.
//
// The block is a pixel short of the pitch, so the grid stays legible as a grid
// instead of merging into solid strokes.
var CELL_PITCH = 3
var CELL_BLOCK = 2

// The transition into transcribing, on a clock of its own rather than the
// daemon's. Fast on purpose — a quarter of a second, short enough to read as a
// snap rather than a move. You do not watch it happen so much as catch that it
// happened, and the bar is already there.
//
// Two legs, not one. Every cell of the meter collapses to the centre of the
// card, and the bar is fired back out of that point. The collapse is what makes
// it flashy: for one frame the entire waveform is a single lit pixel.
var MORPH_IN_MS = 80     // the whole meter, to one point
var MORPH_SPREAD_MS = 50 // then fired out left to right, over this long
var MORPH_OUT_MS = 120   // and each block's own flight out

// The bar the meter becomes: a short row of cells across the middle of the
// card, which then has a light swung along it. Two rows reads as a bar; one is
// a hairline and three is a block.
var SCAN_ROWS = 2

// One sweep, end to end. A Knight Rider scanner is unhurried — it is saying
// something is working, not that something is urgent — and easing at the turns
// is what makes it swing rather than bounce.
var SCAN_MS = 700

// How far the light carries either side of its head, in columns, and how many
// discrete brightnesses it falls through on the way. Quantised hard, like every
// other value on this card: a smooth ramp would be the one gradient in an
// interface that has none.
var SCAN_TAIL = 7
var SCAN_LEVELS = 4

// An unlit segment is dim rather than dark, so the bar reads as a bar the light
// is travelling along rather than as a lone block sliding across nothing.
var SCAN_FLOOR = 0.16

// How bright one column of the bar is, with the head `head` columns along.
// Pure, so the shape of the trail is unit-tested rather than eyeballed.
function scanAlpha(col, head) {
  var d = Math.abs(col - head)
  if (!(d < SCAN_TAIL)) return SCAN_FLOOR
  // Ceil, so the head is always full brightness and the steps land inside the
  // tail rather than one of them falling off its end.
  var q = Math.ceil((1 - d / SCAN_TAIL) * SCAN_LEVELS) / SCAN_LEVELS
  return SCAN_FLOOR + (1 - SCAN_FLOOR) * q
}

// The cells of the bar, in the coordinates waveCellPoints uses. The scanner
// draws itself from these and the morph flies to them, so a block lands exactly
// where the bar is — the same reason waveCellPoints is derived from columnLit.
//
// geom: { x, y, pitch } — x the bar's left edge, y the card's centre line.
function scanCellPoints(cols, geom) {
  var pts = []
  if (!(cols > 0) || !geom) return pts
  for (var c = 0; c < cols; c++) {
    for (var r = 0; r < SCAN_ROWS; r++) {
      pts.push({
        x: geom.x + c * geom.pitch + geom.pitch / 2,
        y: geom.y + (r - SCAN_ROWS / 2 + 0.5) * geom.pitch,
        col: c
      })
    }
  }
  return pts
}

// Every lit cell of the meter, as the centre point of the block drawn there.
//
// Derived from columnLit rather than from its own arithmetic, because these
// points are where the flying blocks *start* and the meter is what they start
// on. Two formulas that agreed only approximately would show up as every
// pixel jumping half a cell the moment it left.
//
// geom: { x, y, pitch } — x the meter's left edge, y its centre line.
function waveCellPoints(wave, geom) {
  var pts = []
  if (!wave || !wave.length || !geom) return pts
  var pitch = geom.pitch
  for (var c = 0; c < wave.length; c++) {
    for (var r = 0; r < WAVE_STEPS; r++) {
      if (!columnLit(wave[c], r, WAVE_STEPS)) continue
      pts.push({
        x: geom.x + c * pitch + pitch / 2,
        y: geom.y + (r - WAVE_STEPS / 2 + 0.5) * pitch
      })
    }
  }
  return pts
}

// Which cells of a column are lit, given its height in steps. Mirrored around
// the centre row, so a column grows outwards from the middle rather than up
// from the floor.
function columnLit(steps, row, totalRows) {
  var s = steps > 0 ? steps : 0
  var mid = totalRows / 2
  return row >= mid - s / 2 && row < mid + s / 2
}

// The index of the point nearest to `to`, skipping any already spoken for.
function nearestIndex(points, to, taken) {
  var best = -1, bestD = 0
  for (var i = 0; i < points.length; i++) {
    if (taken && taken[i]) continue
    var dx = points[i].x - to.x
    var dy = points[i].y - to.y
    var d = dx * dx + dy * dy      // squared: the ordering is the same
    if (best < 0 || d < bestD) { best = i; bestD = d }
  }
  return best
}

// Give every cell of the meter a cell of the bar to fly to.
//
// Source-driven, and that is the whole point: the thing that moves is the
// waveform's own pixels, so every one of them gets a destination and travels
// to it. Nothing fades in and nothing fades out. A loud meter has more cells
// than the bar has cells, and the extras converge on the cell nearest them
// and stack up behind it — a cell that lands on an occupied pixel is simply
// hidden by the one in front, which costs nothing and needs no blending.
//
// Nearest, so a cell moves the shortest distance it can. Where the meter
// already has ink under a letter those cells shuffle a row or two and they are
// the letter; only the few with nowhere near to come from cross the card.
//
// The bar is claimed first, one cell each, so it is complete before any cell
// is spent on stacking. Returns [{ from, to }], one per cell.
function pairCells(sources, targets) {
  var pairs = []
  var n = sources ? sources.length : 0
  var m = targets ? targets.length : 0
  if (!n || !m) return pairs

  var taken = []
  for (var i = 0; i < n; i++) taken.push(false)

  for (var t = 0; t < m; t++) {
    var best = nearestIndex(sources, targets[t], taken)
    if (best < 0) {
      // Fewer cells in the meter than in the bar, which means the meter was
      // almost silent for the whole utterance. Send a second block out of the
      // nearest cell rather than leave a gap in the bar: it starts underneath a
      // real one, so nothing about it reads as an extra pixel.
      pairs.push({ from: sources[nearestIndex(sources, targets[t], null)], to: targets[t] })
      continue
    }
    taken[best] = true
    pairs.push({ from: sources[best], to: targets[t] })
  }

  // Then every cell the bar did not need, to whichever cell is closest.
  for (var j = 0; j < n; j++) {
    if (taken[j]) continue
    pairs.push({ from: sources[j], to: targets[nearestIndex(targets, sources[j], null)] })
  }
  return pairs
}

// Left to right, by where a block is going. The delay depends only on the
// horizontal position, so a letter's whole height arrives together.
function writeDelay(x, width, spreadMs) {
  if (!(width > 0)) return 0
  var t = x / width
  if (!(t >= 0)) t = 0
  if (t > 1) t = 1
  return Math.round(t * spreadMs)
}

if (typeof module !== "undefined")
  module.exports = {
    STATES: STATES,
    WAVE_COLUMNS: WAVE_COLUMNS,
    WAVE_STEPS: WAVE_STEPS,
    CELL_PITCH: CELL_PITCH,
    CELL_BLOCK: CELL_BLOCK,
    MORPH_IN_MS: MORPH_IN_MS,
    MORPH_SPREAD_MS: MORPH_SPREAD_MS,
    MORPH_OUT_MS: MORPH_OUT_MS,
    SCAN_ROWS: SCAN_ROWS,
    SCAN_MS: SCAN_MS,
    SCAN_TAIL: SCAN_TAIL,
    SCAN_LEVELS: SCAN_LEVELS,
    SCAN_FLOOR: SCAN_FLOOR,
    POSITIONS: POSITIONS,
    DEFAULT_POSITION: DEFAULT_POSITION,
    hudMode: hudMode,
    waveStep: waveStep,
    emptyWave: emptyWave,
    pushWave: pushWave,
    isPosition: isPosition,
    hudPosition: hudPosition,
    scanAlpha: scanAlpha,
    scanCellPoints: scanCellPoints,
    waveCellPoints: waveCellPoints,
    columnLit: columnLit,
    nearestIndex: nearestIndex,
    pairCells: pairCells,
    writeDelay: writeDelay
  }
