// What the bar button and the panel decide from the daemon's state file.
// Model.js is plain JavaScript with no QML imports, which is what makes this
// possible; the QML load path is covered by qmllint.

const test = require("node:test")
const assert = require("node:assert")
const Model = require("../Model.js")

const RECORDING_START = "2026-01-02T09:30:00Z"
const RECORDING_START_MS = Date.parse(RECORDING_START)

const state = (over) => Object.assign({}, Model.DEFAULT_STATE, over)

test("dictation outranks everything else", () => {
  const busy = state({
    dictation: "listening",
    recording: { started: RECORDING_START },
    transcribing: "/x/y",
    model: "loading"
  })
  assert.equal(Model.mode(busy), "listening")
  assert.equal(Model.mode(state({ dictation: "transcribing", recording: { started: RECORDING_START } })), "transcribing")
})

test("a meeting outranks its own transcription and the model", () => {
  assert.equal(Model.mode(state({ recording: { started: RECORDING_START }, transcribing: "/x/y" })), "recording")
  assert.equal(Model.mode(state({ transcribing: "/x/y", model: "loading" })), "session")
})

test("the model only speaks when nothing is happening", () => {
  assert.equal(Model.mode(state({ model: "loading" })), "loading")
  assert.equal(Model.mode(state({ model: "failed" })), "failed")
  assert.equal(Model.mode(state({ model: "ready" })), "idle")
})

test("a missing state reads as loading, not as broken", () => {
  assert.equal(Model.mode(null), "loading")
  assert.equal(Model.mode(undefined), "loading")
})

test("the elapsed clock rolls over at a minute and an hour", () => {
  const at = (seconds) => Model.elapsed(RECORDING_START, RECORDING_START_MS + seconds * 1000)
  assert.equal(at(0), "0:00")
  assert.equal(at(59), "0:59")
  assert.equal(at(60), "1:00")
  assert.equal(at(3599), "59:59")
  assert.equal(at(3600), "1:00:00")
  assert.equal(at(3661), "1:01:01")
})

test("a clock never runs backwards or reads NaN", () => {
  assert.equal(Model.elapsed(RECORDING_START, RECORDING_START_MS - 5000), "0:00")
  assert.equal(Model.elapsed("not a date", RECORDING_START_MS), "0:00")
  assert.equal(Model.elapsed(undefined, RECORDING_START_MS), "0:00")
})

test("the state line says what is happening", () => {
  const line = (over, seconds) =>
    Model.stateLine(state(over), RECORDING_START_MS + (seconds || 0) * 1000)
  assert.equal(line({ model: "ready" }), "idle · no dictation key bound yet")
  assert.equal(line({ dictation: "listening" }), "● listening")
  assert.equal(line({ dictation: "transcribing" }), "transcribing…")
  assert.equal(line({ recording: { started: RECORDING_START } }, 95), "● recording · 1:35")
  assert.equal(line({ transcribing: "/home/u/Recordings/2026.01.02-0930" }), "transcribing 2026.01.02-0930…")
  assert.equal(line({ model: "loading" }), "loading model…")
  assert.equal(line({ model: "failed" }), "model failed")
})

test("the idle line names the key that is actually bound", () => {
  const lua = [
    '-- o.bind("SUPER + H", nil, "voxtype record toggle")',
    'o.bind("F9", "Start dictation (push-to-talk)", "voxtype record start")',
    'o.bind("SUPER + CTRL + Y", "Toggle dictation", "~/.local/bin/omayap toggle", { release = true })'
  ].join("\n")
  const keys = Model.dictationKeys(lua)
  assert.equal(keys.toggle, "SUPER + CTRL + Y")
  assert.equal(keys.push, "", "voxtype's F9 is not omayap's")
  assert.equal(Model.dictationHint(keys), "press SUPER + CTRL + Y to dictate")
  assert.equal(
    Model.stateLine(state({ model: "ready" }), Date.now(), Model.dictationHint(keys)),
    "idle · press SUPER + CTRL + Y to dictate"
  )
})

test("push-to-talk wins over the toggle, and no bind says so", () => {
  const both = Model.dictationKeys([
    'o.bind("F10", "Start dictation (push-to-talk)", "~/.local/bin/omayap press")',
    'o.bind("F10", "Stop dictation (push-to-talk)", "~/.local/bin/omayap release", { release = true })',
    'o.bind("SUPER + CTRL + Y", "Toggle dictation", "~/.local/bin/omayap toggle")'
  ].join("\n"))
  assert.equal(Model.dictationHint(both), "hold F10 to dictate")
  assert.equal(Model.dictationHint(Model.dictationKeys("")), "no dictation key bound yet")
  // A commented-out bind is not a bind.
  assert.equal(
    Model.dictationHint(Model.dictationKeys('  -- o.bind("F9", "x", "omayap press")')),
    "no dictation key bound yet"
  )
})

test("the record button says what pressing it will do", () => {
  assert.equal(Model.recordingLabel(state({ model: "ready" })), "Start Recording")
  assert.equal(
    Model.recordingLabel(state({ recording: { started: RECORDING_START } }), RECORDING_START_MS + 65000),
    "Stop Recording · 1:05"
  )
})

test("a half-written state file keeps the last good value", () => {
  const good = state({ dictation: "listening" })
  for (const broken of ['{"dictation": "list', "", "null", "[]", "not json at all"]) {
    assert.deepEqual(Model.parseState(broken, good), good)
  }
  assert.deepEqual(Model.parseState("{}", null), {})
  assert.equal(Model.parseState('{"dictation":"idle"}', good).dictation, "idle")
})

test("a partial config still answers every key", () => {
  const parsed = Model.parseConfig('{"keep_audio": false}', null)
  assert.equal(parsed.keep_audio, false)
  assert.equal(parsed.recordings_dir, Model.DEFAULT_CONFIG.recordings_dir)
  assert.equal(parsed.meeting_detection, false)
  assert.deepEqual(Model.parseConfig("broken", null), Model.DEFAULT_CONFIG)
})

test("the HUD draws dictation, and a meeting for as long as it records", () => {
  assert.equal(Model.hudState(state({ dictation: "listening" })), "recording")
  assert.equal(Model.hudState(state({ dictation: "transcribing" })), "transcribing")
  assert.equal(Model.hudState(state({ recording: { started: RECORDING_START } })), "session")
  // Dictation during a recording is still the thing with a key held down.
  assert.equal(
    Model.hudState(state({ dictation: "listening", recording: { started: RECORDING_START } })),
    "recording"
  )
  // Transcribing a finished meeting is background work, not a card.
  assert.equal(Model.hudState(state({ transcribing: "/x/y" })), "idle")
  assert.equal(Model.hudState(state({ model: "ready" })), "idle")
  assert.equal(Model.hudState(null), "idle")
})

test("a directory name is the last segment, trailing slash or not", () => {
  assert.equal(Model.dirName("/home/u/Recordings/2026.01.02-0930-Team Standup"), "2026.01.02-0930-Team Standup")
  assert.equal(Model.dirName("/home/u/Recordings/2026.01.02-0930/"), "2026.01.02-0930")
  assert.equal(Model.dirName(""), "")
  assert.equal(Model.dirName(null), "")
})
