// Everything the bar button and the panel need to work out from the daemon's
// state file, and nothing that needs Qt to do it. Loadable by `node --test`,
// which is where the precedence rules and the clock are actually checked.

// The daemon's defaults, repeated here so a panel rendered before the config
// file has been read shows the truth rather than blanks.
var DEFAULT_CONFIG = {
  recordings_dir: "~/Recordings",
  keep_audio: true,
  newline_after_dictation: false,
  meeting_detection: false,
  meeting_auto_record: false,
  meeting_excluded_apps: []
}

var DEFAULT_STATE = {
  dictation: "idle",
  model: "loading",
  recording: null,
  pending: null,
  transcribing: null
}

// The daemon truncates and rewrites the state file in place, so a reader can
// catch it mid-write. Keeping the last good value is the whole error handling:
// the next write is milliseconds away.
function parse(text, previous, fallback) {
  try {
    var value = JSON.parse(text)
    if (value && typeof value === "object" && !Array.isArray(value)) return value
  } catch (e) {
    // A half-written file is not an error worth showing anyone.
  }
  return previous || fallback
}

function parseState(text, previous) {
  return parse(text, previous, DEFAULT_STATE)
}

function parseConfig(text, previous) {
  var value = parse(text, previous, DEFAULT_CONFIG)
  var out = {}
  for (var key in DEFAULT_CONFIG) out[key] = DEFAULT_CONFIG[key]
  for (var given in value) out[given] = value[given]
  return out
}

// Dictation outranks everything: it is the thing with a key held down for it.
// A meeting recording outranks its own transcription, and the model's state
// only matters when nothing else is happening.
function mode(state) {
  var s = state || DEFAULT_STATE
  if (s.dictation === "listening") return "listening"
  if (s.dictation === "transcribing") return "transcribing"
  if (s.recording) return "recording"
  if (s.transcribing) return "session"
  if (s.model === "loading") return "loading"
  if (s.model === "failed") return "failed"
  return "idle"
}

// m:ss, or h:mm:ss once a meeting passes the hour — the same clock the
// transcript timestamps use.
function elapsed(startedIso, nowMs) {
  var started = Date.parse(startedIso)
  if (!isFinite(started)) return "0:00"
  var now = typeof nowMs === "number" && isFinite(nowMs) ? nowMs : Date.now()
  var total = Math.floor(Math.max(0, now - started) / 1000)
  var hours = Math.floor(total / 3600)
  var minutes = Math.floor((total % 3600) / 60)
  var seconds = total % 60
  if (hours) return hours + ":" + pad(minutes) + ":" + pad(seconds)
  return minutes + ":" + pad(seconds)
}

function pad(value) {
  return value < 10 ? "0" + value : String(value)
}

function dirName(path) {
  var parts = String(path || "").split("/")
  while (parts.length && !parts[parts.length - 1]) parts.pop()
  return parts.length ? parts[parts.length - 1] : ""
}

// Which keys are actually bound to omayap, read out of the Hyprland config
// rather than assumed. The README suggests F9 and SUPER + CTRL + X, this
// machine might use SUPER + CTRL + Y, and a panel that tells you the wrong key
// is worse than one that says nothing.
//
// Matches `o.bind("<keys>", "<desc>", "<...omayap press|release|toggle>")`,
// skipping commented lines. One pass, no Qt, so it is testable.
function dictationKeys(lua) {
  var out = { push: "", toggle: "" }
  var lines = String(lua || "").split("\n")
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i]
    if (/^\s*--/.test(line)) continue
    if (line.indexOf("omayap") === -1) continue
    var keys = line.match(/o\.bind\(\s*"([^"]+)"/)
    if (!keys) continue
    if (/omayap\s+toggle/.test(line)) out.toggle = keys[1]
    else if (/omayap\s+press/.test(line)) out.push = keys[1]
  }
  return out
}

// What to tell someone who is looking at an idle HUD-less bar button.
function dictationHint(keys) {
  var k = keys || { push: "", toggle: "" }
  if (k.push) return "hold " + k.push + " to dictate"
  if (k.toggle) return "press " + k.toggle + " to dictate"
  return "no dictation key bound yet"
}

// One line, for the tooltip and the top of the panel. Says what is happening
// and, when something is running, how long it has been running for.
function stateLine(state, nowMs, hint) {
  var s = state || DEFAULT_STATE
  var idle = "idle · " + (hint || dictationHint(null))
  switch (mode(s)) {
    case "listening": return "● listening"
    case "transcribing": return "transcribing…"
    case "recording": return "● recording · " + elapsed(s.recording.started, nowMs)
    case "session": return "transcribing " + dirName(s.transcribing) + "…"
    case "loading": return "loading model…"
    case "failed": return "model failed"
    default: return idle
  }
}

// What the recording title should say when a session has one.
function recordingLabel(state, nowMs) {
  var s = state || DEFAULT_STATE
  if (!s.recording) return "Start Recording"
  return "Stop Recording · " + elapsed(s.recording.started, nowMs)
}

// Three things the HUD draws. Dictation wins, because a key is held down for
// it; a meeting recording gets the same card with a clock and a stop button,
// which is a better home for "you are being recorded" than a notification that
// scrolls away behind the next one.
function hudState(state) {
  var s = state || DEFAULT_STATE
  if (s.dictation === "listening") return "recording"
  if (s.dictation === "transcribing") return "transcribing"
  if (s.recording) return "session"
  return "idle"
}

if (typeof module !== "undefined")
  module.exports = {
    DEFAULT_CONFIG: DEFAULT_CONFIG,
    DEFAULT_STATE: DEFAULT_STATE,
    dictationKeys: dictationKeys,
    dictationHint: dictationHint,
    parseState: parseState,
    parseConfig: parseConfig,
    mode: mode,
    elapsed: elapsed,
    dirName: dirName,
    stateLine: stateLine,
    recordingLabel: recordingLabel,
    hudState: hudState
  }
