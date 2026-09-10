"""Meeting detection: who has the microphone, and which window that is.

The pw-dump fixtures are the shape PipeWire 1.6.8 actually produces, including
the part that bites: the stream node knows nothing about which program owns
it, so the pid and the binary have to come off the client object.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omayap import detect  # noqa: E402


def node(name, state="running", client=10, app=None, extra=None):
    props = {
        "media.class": "Stream/Input/Audio",
        "node.name": name,
        "client.id": client,
    }
    if app:
        props["application.name"] = app
    props.update(extra or {})
    return {"id": 100, "type": "PipeWire:Interface:Node", "info": {"state": state, "props": props}}


def client(cid, binary, pid, app=None):
    props = {"application.process.binary": binary, "application.process.id": pid}
    if app:
        props["application.name"] = app
    return {"id": cid, "type": "PipeWire:Interface:Client", "info": {"props": props}}


# pw-record on PipeWire 1.6.8, copied out of a real pw-dump: the node calls
# itself pw-record, and the client behind it is the pw-cat binary the symlink
# points at.
PW_RECORD = [
    node("pw-record", app="pw-record"),
    client(10, "pw-cat", 258306, app="pw-cat"),
]


class TestCapturing(unittest.TestCase):
    def test_a_running_stream_is_reported_with_its_process(self):
        dump = [node("Chromium input", app="Chromium input"), client(10, "chromium", 4321)]
        self.assertEqual(
            detect.capturing(dump),
            [{"pid": 4321, "app": "Chromium", "binary": "chromium"}],
        )

    def test_the_stream_is_named_by_the_app_not_the_symlink_target(self):
        self.assertEqual(
            detect.capturing(PW_RECORD),
            [{"pid": 258306, "app": "pw-record", "binary": "pw-cat"}],
        )

    def test_either_name_excludes_the_stream(self):
        for excluded in ("pw-record", "pw-cat", "PW-Record"):
            self.assertEqual(detect.capturing(PW_RECORD, [excluded]), [], excluded)
        self.assertEqual(len(detect.capturing(PW_RECORD, ["firefox"])), 1)

    def test_idle_streams_are_not_a_call(self):
        dump = [node("Chromium input", state="idle"), client(10, "chromium", 4321)]
        self.assertEqual(detect.capturing(dump), [])

    def test_monitors_are_not_a_call(self):
        dump = [
            node("loopback", extra={"stream.monitor": "true"}),
            client(10, "pavucontrol", 99),
        ]
        self.assertEqual(detect.capturing(dump), [])

    def test_our_own_captures_are_ignored(self):
        dump = [node("omayap-mic"), node("omayap-system"), client(10, "python3", 7)]
        self.assertEqual(detect.capturing(dump), [])

    def test_the_shell_is_ignored(self):
        dump = [node("quickshell input"), client(10, "quickshell", 55)]
        self.assertEqual(detect.capturing(dump), [])

    def test_one_process_with_two_streams_is_one_call(self):
        dump = [
            node("Teams capture 1", app="Microsoft Teams"),
            node("Teams capture 2", app="Microsoft Teams"),
            client(10, "teams", 900),
        ]
        self.assertEqual(
            detect.capturing(dump),
            [{"pid": 900, "app": "Microsoft Teams", "binary": "teams"}],
        )

    def test_output_streams_are_not_a_call(self):
        dump = [
            {
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "running",
                    "props": {"media.class": "Stream/Output/Audio", "node.name": "spotify"},
                },
            }
        ]
        self.assertEqual(detect.capturing(dump), [])

    def test_a_stream_with_nothing_to_call_it_falls_back_to_the_node_name(self):
        self.assertEqual(
            detect.capturing([node("mystery", client=None)]),
            [{"pid": None, "app": "mystery", "binary": "mystery"}],
        )


class TestPrettyApp(unittest.TestCase):
    def test_the_stream_direction_comes_off(self):
        self.assertEqual(detect.pretty_app("Google Chrome input"), "Google Chrome")
        self.assertEqual(detect.pretty_app("Firefox Capture"), "Firefox")
        self.assertEqual(detect.pretty_app("Slack"), "Slack")

    def test_a_name_that_is_only_a_direction_survives(self):
        self.assertEqual(detect.pretty_app("input"), "input")
        self.assertEqual(detect.pretty_app(""), "")
        self.assertEqual(detect.pretty_app(None), "")


class TestCleanTitle(unittest.TestCase):
    def test_the_app_name_comes_off_the_end(self):
        self.assertEqual(detect.clean_title("Foo - Google Chrome", "chrome"), "Foo")
        self.assertEqual(
            detect.clean_title("Quarterly Review | Microsoft Teams", "teams"),
            "Quarterly Review",
        )
        self.assertEqual(detect.clean_title("Standup | Slack", "slack"), "Standup")

    def test_a_title_that_only_looks_like_a_suffix_survives(self):
        self.assertEqual(
            detect.clean_title("Chrome tips - Notes", "chrome"), "Chrome tips - Notes"
        )

    def test_a_title_that_is_only_the_app_name_survives(self):
        self.assertEqual(detect.clean_title("Google Chrome", "chrome"), "Google Chrome")

    def test_nothing_left_is_no_title(self):
        self.assertIsNone(detect.clean_title("   ", "chrome"))
        self.assertIsNone(detect.clean_title(None, "chrome"))


class TestWindowTitle(unittest.TestCase):
    WINDOWS = [
        {"pid": 4321, "class": "chromium", "title": "Quarterly Review - Google Chrome"},
        {"pid": 77, "class": "Alacritty", "title": "nvim"},
    ]

    def test_by_pid(self):
        self.assertEqual(
            detect.window_title(4321, self.WINDOWS, "chrome", chain=[4321]),
            "Quarterly Review",
        )

    def test_by_ancestry_because_browsers_capture_from_a_child(self):
        self.assertEqual(
            detect.window_title(9999, self.WINDOWS, "chrome", chain=[9999, 5000, 4321]),
            "Quarterly Review",
        )

    def test_by_class_when_no_ancestor_owns_a_window(self):
        # The class carries the binary name, and the title's suffix here is
        # the brand, so the suffix stays: it does not name the binary.
        self.assertEqual(
            detect.window_title(9999, self.WINDOWS, "chromium", chain=[9999]),
            "Quarterly Review - Google Chrome",
        )

    def test_no_window_anywhere_is_no_title(self):
        self.assertIsNone(detect.window_title(9999, self.WINDOWS, "teams", chain=[9999]))
        self.assertIsNone(detect.window_title(None, [], None, chain=[]))

    def test_pid_chain_stops_at_init_and_never_loops(self):
        parents = {5: 4, 4: 1}
        chain = detect.pid_chain(5, ppid=lambda pid: parents.get(pid))
        self.assertEqual(chain, [5, 4])
        self.assertEqual(detect.pid_chain(5, ppid=lambda pid: 5), [5])


if __name__ == "__main__":
    unittest.main()
