// The HUD's geometry and its meter. Ported from omakoe's own model tests,
// which are the reason these pixels line up: the meter's cells and the
// scanning bar's cells are drawn from the same arithmetic, so the handover
// between them has to agree cell for cell.

const test = require("node:test")
const assert = require("node:assert")
const Hud = require("../HudModel.js")

test("the meter quantises a peak to discrete steps", () => {
  assert.equal(Hud.waveStep(0), 1, "silence is step 1, never 0: a flat box reads as a broken mic")
  assert.equal(Hud.waveStep(1), Hud.WAVE_STEPS)
  assert.ok(Number.isInteger(Hud.waveStep(0.37)))
  assert.ok(Hud.waveStep(0.05) > 1, "quiet speech is not pinned to the floor")
  for (const bad of [NaN, Infinity, -1, 2, "loud", null, undefined]) {
    const step = Hud.waveStep(bad)
    assert.ok(Number.isInteger(step) && step >= 1 && step <= Hud.WAVE_STEPS, `waveStep(${String(bad)})`)
  }
})

test("the wave is a fixed-length ring, so the card never resizes", () => {
  const empty = Hud.emptyWave()
  assert.equal(empty.length, Hud.WAVE_COLUMNS)
  const pushed = Hud.pushWave(empty, 1)
  assert.equal(pushed.length, Hud.WAVE_COLUMNS)
  assert.equal(pushed[pushed.length - 1], Hud.WAVE_STEPS, "the newest sample is last")
  assert.equal(empty[0], 1, "pushing does not mutate the wave it was given")
})

test("a column grows outwards from the centre line", () => {
  const rows = Hud.WAVE_STEPS
  for (let steps = 1; steps <= rows; steps++) {
    let lit = 0
    for (let row = 0; row < rows; row++) if (Hud.columnLit(steps, row, rows)) lit++
    assert.equal(lit, steps, `${steps} steps lights ${steps} cells`)
  }
  assert.ok(
    [...Array(rows).keys()].every((row) => Hud.columnLit(4, row, rows) === Hud.columnLit(4, rows - 1 - row, rows)),
    "symmetric about the centre"
  )
})

test("the card stays on screen whatever the margin", () => {
  const PW = 1920, PH = 1080, CW = 400, CH = 100, M = 64
  const bottom = Hud.hudPosition("bottom-center", PW, PH, CW, CH, M)
  assert.equal(bottom.x, (PW - CW) / 2)
  assert.equal(bottom.y, PH - CH - M)
  assert.equal(Hud.hudPosition("center", PW, PH, CW, CH, M).y, (PH - CH) / 2)
  assert.equal(Hud.hudPosition("top-center", PW, PH, CW, CH, M).y, M)
  assert.deepEqual(
    Hud.hudPosition("nowhere", PW, PH, CW, CH, M),
    Hud.hudPosition(Hud.DEFAULT_POSITION, PW, PH, CW, CH, M),
    "an unknown position falls back to the default"
  )
  for (const [pw, ph, cw, ch, m] of [[1920, 1080, 400, 100, 5000], [300, 200, 400, 300, 64], [1920, 1080, 400, 100, -9]]) {
    const at = Hud.hudPosition("bottom-right", pw, ph, cw, ch, m)
    assert.ok(at.x >= 0 && at.y >= 0 && at.x <= Math.max(0, pw - cw) && at.y <= Math.max(0, ph - ch))
    assert.ok(Number.isInteger(at.x) && Number.isInteger(at.y), "integral, so the card never renders blurry")
  }
  for (const bad of [NaN, undefined, null, "wide"]) {
    const at = Hud.hudPosition("bottom-center", PW, PH, CW, CH, bad)
    assert.ok(Number.isInteger(at.x) && Number.isInteger(at.y), `margin ${String(bad)} degrades to zero`)
  }
})

test("every meter cell has a point and every point is on a lit cell", () => {
  const pitch = Hud.CELL_PITCH
  const geom = { x: 0, y: 20, pitch }
  const wave = [1, 4, 8, 3]
  const points = Hud.waveCellPoints(wave, geom)
  assert.equal(points.length, 1 + 4 + 8 + 3)
  const rowOf = (p) => Math.round((p.y - geom.y) / pitch + Hud.WAVE_STEPS / 2 - 0.5)
  const colOf = (p) => Math.round((p.x - geom.x - pitch / 2) / pitch)
  assert.ok(points.every((p) => Hud.columnLit(wave[colOf(p)], rowOf(p), Hud.WAVE_STEPS)))
  assert.equal(Hud.waveCellPoints([], geom).length, 0)
  assert.equal(Hud.waveCellPoints(null, geom).length, 0)
})

test("the bar the meter becomes spans exactly the meter", () => {
  const points = Hud.scanCellPoints(Hud.WAVE_COLUMNS, { x: 0, y: 20, pitch: Hud.CELL_PITCH })
  assert.equal(points.length, Hud.WAVE_COLUMNS * Hud.SCAN_ROWS)
  assert.equal(new Set(points.map((p) => p.x)).size, Hud.WAVE_COLUMNS)
  assert.equal(new Set(points.map((p) => p.y)).size, Hud.SCAN_ROWS)
  assert.ok(points.some((p) => p.y < 20) && points.some((p) => p.y > 20), "straddles the centre line")
  assert.equal(Hud.scanCellPoints(0, { x: 0, y: 0, pitch: 3 }).length, 0)
  assert.equal(Hud.scanCellPoints(10, null).length, 0)
})

test("every cell of the meter is given somewhere to go, and the bar is whole", () => {
  const pitch = Hud.CELL_PITCH
  const targets = Hud.scanCellPoints(Hud.WAVE_COLUMNS, { x: 0, y: 20, pitch })
  const shapes = {
    silent: () => 1,
    loud: () => Hud.WAVE_STEPS,
    ramp: (i) => (i % Hud.WAVE_STEPS) + 1,
    gappy: (i) => (i % 9 < 4 ? 1 : Hud.WAVE_STEPS - 1)
  }
  for (const [name, step] of Object.entries(shapes)) {
    const wave = []
    for (let i = 0; i < Hud.WAVE_COLUMNS; i++) wave.push(step(i))
    const sources = Hud.waveCellPoints(wave, { x: 0, y: 20, pitch })
    const pairs = Hud.pairCells(sources, targets)
    assert.ok(pairs.length >= sources.length, `${name}: no cell is dropped`)
    assert.equal(new Set(pairs.map((p) => `${p.to.x},${p.to.y}`)).size, targets.length, `${name}: the bar is covered`)
    assert.ok(
      pairs.every((p) => sources.some((s) => s.x === p.from.x && s.y === p.from.y)),
      `${name}: every block starts on a cell the meter had`
    )
  }
  assert.equal(Hud.pairCells([], targets).length, 0)
  assert.equal(Hud.pairCells([{ x: 1, y: 1 }], []).length, 0)
})

test("the light on the bar is quantised and brightest at its head", () => {
  assert.equal(Hud.scanAlpha(10, 10), 1)
  assert.ok(Hud.scanAlpha(10 + Hud.SCAN_TAIL, 10) === Hud.SCAN_FLOOR, "past the tail is the floor")
  assert.ok(Hud.scanAlpha(12, 10) < Hud.scanAlpha(11, 10), "it fades away from the head")
  assert.equal(Hud.scanAlpha(4, 10), Hud.scanAlpha(16, 10), "and fades symmetrically")
  const levels = new Set()
  for (let col = 0; col < 40; col++) levels.add(Hud.scanAlpha(col, 20))
  assert.ok(levels.size <= Hud.SCAN_LEVELS + 1, "hard steps, not a gradient")
})

test("the transformation is a flash rather than a move", () => {
  const total = Hud.MORPH_IN_MS + Hud.MORPH_SPREAD_MS + Hud.MORPH_OUT_MS
  assert.ok(total < 320 && total > 120, `${total}ms`)
  assert.ok(Hud.MORPH_SPREAD_MS < Hud.MORPH_OUT_MS)
  assert.ok(Hud.MORPH_IN_MS <= Hud.MORPH_OUT_MS)
  assert.ok(Hud.CELL_BLOCK < Hud.CELL_PITCH && Hud.CELL_BLOCK > 0, "the grid stays a grid")
  assert.equal(Hud.writeDelay(0, 100, 50), 0)
  assert.equal(Hud.writeDelay(100, 100, 50), 50)
  assert.equal(Hud.writeDelay(50, 100, 50), 25)
  assert.equal(Hud.writeDelay(50, 0, 50), 0, "a zero-width card cannot divide by it")
})

test("the HUD draws nothing for a state it has no arrangement for", () => {
  assert.equal(Hud.hudMode({ state: "recording", hud: true }), "recording")
  assert.equal(Hud.hudMode({ state: "transcribing", hud: true }), "transcribing")
  assert.equal(Hud.hudMode({ state: "session", hud: true }), "session")
  assert.equal(Hud.hudMode({ state: "session", hud: false }), "hidden")
  assert.equal(Hud.hudMode({ state: "idle", hud: true }), "hidden")
  assert.equal(Hud.hudMode({ state: "recording", hud: false }), "hidden")
  assert.equal(Hud.hudMode({ state: "banana", hud: true }), "hidden")
})
