#!/usr/bin/env python3
"""live_mcp_probe_test.py — tests for the live MCP session probe's classifier.

The live half of `live_mcp_probe.py` cannot be unit-tested — it needs a real
Freebuff session, which is the whole point of it. The half that *decides* can be,
and that is the half that can be wrong quietly: a transcript reader that reports
"loaded" because a name appeared in an error message, or that misses a tool call
because a TUI redraws with cursor moves instead of line breaks, would turn a
measurement into a comfort.

So the tests below pin both directions — every verdict, and the two rendering
styles that actually occur:

- `test_the_probe_cannot_satisfy_its_own_prompt` — the bug this probe shipped
  with for one run. A pty echoes input, the prompt names the tool, and the search
  found the probe's own words; it reported success without the client doing
  anything. Every verdict-bearing test now passes a prompt for that reason.
- `test_no_signal_is_inconclusive_not_loaded` — an empty or unrelated screen must
  never read as success, because "we could not see" is the honest answer.
- `test_a_tool_name_split_by_cursor_moves_still_reads_as_loaded` — Ink redraws by
  positioning the cursor, so the halves of a name can be separated by escapes
  that carry no whitespace.
- `test_the_loaders_warning_wins_over_a_stray_mention` — a rejected registry that
  also mentions the server name must be reported as rejected.

Run: python3 live_mcp_probe_test.py
"""

import os
import select
import sys
import time
import tty
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import live_mcp_probe as probe


def screen(*lines):
    """A crude Ink-ish frame: clear, cursor moves, text."""
    return "\x1b[2J\x1b[H" + "\r\n".join(lines) + "\x1b[?25h"


class VerdictTest(unittest.TestCase):

    def test_an_empty_transcript_is_inconclusive(self):
        status, reason = probe.verdict("")
        self.assertEqual(status, "inconclusive")
        self.assertIn("no output", reason)

    def test_escape_sequences_alone_are_inconclusive(self):
        status, _ = probe.verdict("\x1b[2J\x1b[H\x1b[?25l\x1b[?1049h")
        self.assertEqual(status, "inconclusive")

    def test_no_signal_is_inconclusive_not_loaded(self):
        status, _ = probe.verdict(screen("Freebuff", "> type a message", "LITE mode"))
        self.assertEqual(status, "inconclusive")

    def test_a_namespaced_tool_call_reads_as_loaded(self):
        status, reason = probe.verdict(
            screen("> verify the registry", "  \u23fa builderbro__verify_claim(read_file)",
                   "  \u23fa result: {\"ok\": true, \"level\": \"observed\"}"))
        self.assertEqual(status, "loaded")
        self.assertIn("builderbro__", reason)

    def test_a_tool_name_split_across_a_redraw_still_reads_as_loaded(self):
        # The prefix itself split across a wrapped redraw. The newline between the
        # halves survives `plain()`, so only the whitespace-squashed search finds it.
        frame = screen("> \u23fa builderbro", "__verify_claim(read_file)")
        self.assertNotIn(probe.TOOL_PREFIX, probe.plain(frame))
        status, _ = probe.verdict(frame)
        self.assertEqual(status, "loaded")

    def test_the_loaders_warning_reads_as_rejected(self):
        status, reason = probe.verdict(
            screen("step 1", "warn Failed to load tools from MCP server \"builderbro\"; "
                             "its tools will be unavailable for this step."))
        self.assertEqual(status, "rejected")
        self.assertIn("Failed to load tools", reason)

    def test_the_loaders_warning_wins_over_a_stray_mention(self):
        # The server name appears in the warning *and* in a listing above it; the
        # verdict has to be the failure, not the mention.
        status, _ = probe.verdict(
            screen("mcpServers: builderbro", "warn Failed to load tools from MCP "
                                             "server \"builderbro\"; its tools will be "
                                             "unavailable for this step."))
        self.assertEqual(status, "rejected")

    def test_the_warning_is_recognised_when_a_redraw_splits_it(self):
        frame = screen("Failed to load", "tools from MCP server \"builderbro\"")
        self.assertNotIn(probe.LOAD_FAILURE, probe.plain(frame))
        status, _ = probe.verdict(frame)
        self.assertEqual(status, "rejected")

    def test_a_bare_server_name_is_not_enough(self):
        # Mentioning `builderbro` (in a prompt echo, say) is not evidence that a
        # single tool was registered — the namespace separator is what proves it.
        status, _ = probe.verdict(screen("builderbro", ".agents/mcp.json"))
        self.assertEqual(status, "inconclusive")

    def test_bytes_and_str_both_classify(self):
        text = screen("  \u23fa builderbro__rag_ask(hash: how does memory work)")
        self.assertEqual(probe.verdict(text.encode("utf-8"))[0], "loaded")

    def test_the_probe_cannot_satisfy_its_own_prompt(self):
        # Regression: the first live run of this probe returned `loaded` on a
        # transcript that was 270 bytes of its own prompt echoed back by the pty.
        status, reason = probe.verdict(screen(probe.DEFAULT_PROMPT), probe.DEFAULT_PROMPT)
        self.assertEqual(status, "inconclusive")
        self.assertIn("echo", reason)

    def test_the_prompt_echo_does_not_mask_a_real_tool_call(self):
        frame = screen(probe.DEFAULT_PROMPT, "  \u23fa builderbro__verify_claim(read_file)")
        self.assertEqual(probe.verdict(frame, probe.DEFAULT_PROMPT)[0], "loaded")

    def test_the_prompt_echo_does_not_mask_the_loaders_warning(self):
        frame = screen(probe.DEFAULT_PROMPT,
                       "warn Failed to load tools from MCP server \"builderbro\"")
        self.assertEqual(probe.verdict(frame, probe.DEFAULT_PROMPT)[0], "rejected")

    def test_a_client_that_paints_without_a_signal_says_so(self):
        status, reason = probe.verdict(screen("Freebuff", "> ready"), probe.DEFAULT_PROMPT)
        self.assertEqual(status, "inconclusive")
        self.assertIn("painted", reason)

    def test_client_output_excludes_the_prompt_but_keeps_the_rest(self):
        transcript = screen(probe.DEFAULT_PROMPT, "  \u23fa builderbro__verify_claim(ok)")
        body = probe.client_output(transcript, probe.DEFAULT_PROMPT)
        self.assertNotIn("CalltheMCPtool", body)
        self.assertIn("builderbro__verify_claim", body)


@unittest.skipUnless(probe.POSIX, "a pty needs a POSIX platform")
class PtyPlumbingTest(unittest.TestCase):
    """The probe can only report on the client if it can carry bytes at all.

    Without this, "the session produced no output" would be indistinguishable from
    "the probe cannot read output" — and the second one would silently turn every
    real session into a reassurance about nothing.
    """

    def _session(self, command):
        proc, master = probe.spawn(command, cwd=os.getcwd())
        self.addCleanup(probe.terminate, proc)
        self.addCleanup(os.close, master)
        return proc, master

    def test_a_pty_session_delivers_its_output(self):
        proc, master = self._session([sys.executable, "-c", "print('pty-alive')"])
        transcript = probe.drive(proc, master, "ignored", timeout=10,
                                 boot_quiet=0.3, warmup=0.3)
        self.assertIn("pty-alive", transcript.decode("utf-8", "replace"))

    def test_the_prompt_actually_reaches_the_session(self):
        # `cat` echoes what it is given: if `drive` never types the prompt, every
        # real session would look inconclusive no matter what the client did.
        proc, master = self._session(["cat"])
        transcript = probe.drive(proc, master, "hello-from-the-probe", timeout=10,
                                 boot_quiet=0.3, warmup=0.3)
        self.assertIn("hello-from-the-probe", transcript.decode("utf-8", "replace"))

    def test_a_verdict_stops_the_session_early(self):
        # The reverse of the above: a session that already says something decidable
        # must not burn the whole budget.
        proc, master = self._session(
            [sys.executable, "-c", "print('\u23fa builderbro__verify_claim(ok)'); "])
        started = time.time()
        probe.drive(proc, master, "ignored", timeout=30, boot_quiet=0.3, warmup=0.3)
        self.assertLess(time.time() - started, 25)


@unittest.skipUnless(probe.POSIX, "a pty needs a POSIX platform")
class QueryResponseTest(unittest.TestCase):
    """The harness has to answer the terminal, or it measures its own silence.

    The live runs failed on this: the client asked for the kitty keyboard protocol
    and a bare pty said nothing, so the session never drew and every verdict came
    back inconclusive for a reason that had nothing to do with the registry.
    """

    def _reply(self, chunk, stats=None):
        master, slave = probe.pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        # The client puts the terminal in raw mode; a bare pty is in canonical
        # mode and would hold an answer with no newline in it, so a test that
        # skips this reads back nothing and blames the probe.
        tty.setraw(slave)
        count = probe.answer(master, chunk, stats)
        ready, _, _ = select.select([slave], [], [], 0.5)
        return count, (os.read(slave, 4096).decode("utf-8") if ready else "")

    def test_the_kitty_keyboard_query_is_declined(self):
        count, reply = self._reply("\x1b[>0q")
        self.assertEqual(count, 1)
        self.assertEqual(reply, "\x1b[?u")

    def test_a_cursor_position_query_gets_a_report(self):
        count, reply = self._reply("\x1b[6n")
        self.assertEqual(count, 1)
        self.assertEqual(reply, "\x1b[1;1R")

    def test_every_occurrence_is_answered(self):
        count, reply = self._reply("\x1b[6n\x1b[6n")
        self.assertEqual(count, 2)
        self.assertEqual(reply, "\x1b[1;1R" * 2)

    def test_ordinary_output_is_not_mistaken_for_a_query(self):
        count, reply = self._reply("Freebuff\r\n> type a message")
        self.assertEqual((count, reply), (0, ""))

    def test_a_private_mode_query_is_declined_rather_than_ignored(self):
        # The five that were left unanswered on the live runs (`?1004$p ?1016$p
        # ?2004$p ?2027$p ?2031$p`) are exactly what this covers.
        stats = {}
        count, reply = self._reply("\x1b[?2004$p\x1b[?1004$p", stats)
        self.assertEqual(count, 2)
        self.assertEqual(reply, "\x1b[?2004;0$y\x1b[?1004;0$y")
        self.assertNotIn("unanswered_queries", stats)
        self.assertEqual(stats["queries_answered"], [("decrmq_declined", 2)])

    def test_a_query_it_cannot_answer_is_recorded(self):
        stats = {}
        count, reply = self._reply("\x1b[?7n", stats)
        self.assertEqual((count, reply), (0, ""))
        self.assertIn("\x1b[?7n", stats.get("unanswered_queries", set()))

    def test_answers_are_counted_by_name(self):
        stats = {}
        self._reply("\x1b[>0q\x1b[6n", stats)
        self.assertEqual(sorted(stats["queries_answered"]),
                         [("cursor_position", 1), ("kitty_keyboard", 1)])


class NormalisationTest(unittest.TestCase):

    def test_colour_and_erase_sequences_are_stripped(self):
        text = "\x1b[38;5;204mbuilderbro\x1b[0m__\x1b[1mverify_claim\x1b[0m"
        self.assertEqual(probe.plain(text), "builderbro__verify_claim")

    def test_an_osc_title_sequence_is_stripped(self):
        self.assertEqual(probe.plain("\x1b]0;freebuff\x07hello"), "hello")

    def test_a_carriage_return_is_removed_not_kept(self):
        self.assertNotIn("\r", probe.plain("a\r\nb"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
