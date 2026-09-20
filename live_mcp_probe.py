#!/usr/bin/env python3
"""live_mcp_probe.py — did a *live Freebuff session* actually load our MCP registry?

WHY THIS EXISTS
---------------
`.agents/mcp.json` registers `builderbro_mcp.py` with Freebuff, and everything on
this side of that seam is measured: the server's own tests, a client that speaks
the exact spawn contract, and `RegistryTest`, which asserts the file's placement,
key set, transport and env against the loader's own code (read out of the
installed binary, not out of documentation).

None of that is the same as Freebuff loading it. A registry can pass every check
here and still be skipped by the client — that is exactly what happened: the file
sat at the repository root, where the loader never looks, and nothing anywhere
reported a problem.

So this probe starts the real client and looks at what it does. It is deliberately
dumb about the TUI: it spawns `freebuff --trust-agents` on a pty, waits for the
first frame, types one read-only prompt that requires the tool, and scans the raw
transcript for two strings that only exist on either side of the verdict:

The pty also has to *answer* the client. TUIs query the terminal before drawing,
and a bare pty replies to nothing, so the first live runs showed a client sitting
silently on a query — which looks exactly like a client that found no MCP server.
`QUERY_RESPONSES` answers the specific queries this client makes and `answer()`
declines every remaining DECRQM mode query; anything still unanswered is recorded
in the summary rather than guessed at.

  loaded       the loader namespaces what it registers as `<server>__<tool>`, so
               `builderbro__` in the *client's own output* is proof the registry
               was read
  rejected     the loader's own warning, `Failed to load tools from MCP server
               "builderbro"`, which is what a bad placement or a schema failure
               produces
  inconclusive anything else — including a session that never rendered

The first draft of this probe reported `loaded` on its first live run, and it was
wrong in the one way that matters: a pty echoes input, the prompt necessarily
names the tool it is asking for, and the search found the probe's own words. So
the prompt echo is removed before anything is searched — otherwise this proves
only that the probe can talk to itself.

Inconclusive is a real result, not a failure to try: it means this probe could not
see, and the honest thing is to say so rather than to report the thing we hoped
for. It is also the common outcome on a slow device, where the 138 MB core takes
minutes to start (measured here: no bytes at all in the first 28s).

WHAT IT CANNOT TELL YOU
-----------------------
- **Why** the client acted. It reads a transcript, not the loader's internals.
- Anything about a session you did not start here. `--classify FILE` re-scores a
  saved transcript, which is how the classifier is tested without a live run.
- Whether the *answer* the model produced was honest. That is the verifier's job
  and it is on the other side of the seam.

Run: python3 live_mcp_probe.py --timeout 600
     python3 live_mcp_probe.py --classify /tmp/transcript.txt
Exit: 0 loaded · 1 rejected · 2 inconclusive
"""

import argparse
import json
import os
import re
import select
import signal
import subprocess
import sys
import time

try:  # POSIX only, and the classifier must stay importable without it
    import fcntl
    import pty
    import struct
    import termios
    POSIX = True
except ImportError:  # pragma: no cover - the repository's targets are all POSIX
    POSIX = False

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, ".agents", "mcp.json")

# What the loader prefixes a registered tool with (`X + "__" + B`).
TOOL_PREFIX = "builderbro__"

# The loader's own warning when a server cannot be spawned or does not parse.
# Copied from the installed binary; if it ever changes, this probe goes
# inconclusive rather than wrong, which is the failure mode you want from a probe.
LOAD_FAILURE = 'Failed to load tools from MCP server "builderbro"'

# Read-only, and it requires the tool: the model has to read a file and pass the
# contents in, which no amount of answering from priors can fake.
DEFAULT_PROMPT = (
    "Call the MCP tool builderbro__verify_claim exactly once. Read .agents/mcp.json "
    "first, then pass its contents as the output argument with tool=read_file, "
    'arg=.agents/mcp.json, expect=contains:builderbro. Do not edit any files. '
    "Then print the raw JSON the tool returned."
)

# A TUI asks the terminal questions before it will draw, and a bare pty answers
# none of them. The first live run of this probe ended on `ESC[>0q` — the kitty
# keyboard-protocol query — with the client sitting on a reply that never came, so
# every session looked like "produced no output". Each entry is (name, query,
# reply); the replies decline where declining is safe (kitty keys, which we cannot
# encode anyway) and are honest where a report is expected.
QUERY_RESPONSES = (
    ("kitty_keyboard", re.compile(r"\x1b\[>[0-9;]*q"), "\x1b[?u"),
    ("cursor_position", re.compile(r"\x1b\[6n"), "\x1b[1;1R"),
    ("device_status", re.compile(r"\x1b\[5n"), "\x1b[0n"),
    ("device_attributes", re.compile(r"\x1b\[c"), "\x1b[?62;1;2;6;9;15;18;21;22c"),
    ("secondary_attributes", re.compile(r"\x1b\[>c"), "\x1b[>41;354;0c"),

    ("background_colour", re.compile(r"\x1b\]11;\?"), "\x1b]11;rgb:0000/0000/0000\x07"),
    ("foreground_colour", re.compile(r"\x1b\]10;\?"), "\x1b]10;rgb:ffff/ffff/ffff\x07"),
)

# `CSI ? Ps $ p` (DECRQM) asks whether a private mode is set. Every one that does
# not have a specific reply above is answered "not recognised", which is a
# terminal honestly declining to implement a mode (bracketed paste, focus
# reporting, synchronised output) rather than leaving the client blocking on a
# question that never comes back — measured, not theorised: the client's entire
# output on the live runs was its two init sequences and then silence.
_DECRQM = re.compile(r"\x1b\[\?(\d+)\$p")

# Query-shaped bytes we still do not recognise. Recorded rather than guessed at: a
# client that stays silent on an unanswered query is a fact about this harness,
# and the summary should say so instead of reporting a registry problem.
_ANY_QUERY = re.compile(r"\x1b\[[0-9;?]*[nqc]|\x1b\[\?[0-9;]*\$p|\x1b\][0-9]+;\?")

_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_OTHER = re.compile(r"\x1b[()#][0-9A-Za-z]|\x1b[=><]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def plain(transcript):
    """Terminal text, with escape sequences and control bytes removed."""
    text = _ANSI_OSC.sub("", transcript)
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OTHER.sub("", text)
    return _CONTROL.sub("", text)


def squash(text):
    """Whitespace-free text, so a name split across a wrapped redraw still matches."""
    return re.sub(r"\s+", "", text)


def client_output(transcript, prompt=None):
    """What the *client* drew, with the probe's own prompt echo removed.

    This is the correction that matters. A pty echoes input until the child puts
    the terminal in raw mode, and the prompt necessarily contains the tool name it
    is asking the model to call — so a transcript that is nothing but our own
    prompt echoed back satisfies a naive search for `builderbro__`. That is the
    probe proving itself, not the client.
    """
    if not isinstance(transcript, str):
        transcript = transcript.decode("utf-8", "replace")
    body = squash(plain(transcript))
    if prompt:
        body = body.replace(squash(prompt), "")
    return body


def verdict(transcript, prompt=None):
    """`(status, reason)` for a session transcript.

    Signals are searched in whitespace-free text, because a TUI redraws a line by
    moving the cursor rather than by reprinting it — the halves of a name can end
    up separated by escapes that carry no visible whitespace. `prompt` is removed
    first, so the probe cannot satisfy its own question.
    """
    if not isinstance(transcript, str):
        transcript = transcript.decode("utf-8", "replace")
    text = plain(transcript)
    body = client_output(text, prompt)

    if LOAD_FAILURE in text or squash(LOAD_FAILURE) in body:
        return "rejected", 'the loader warned: %s' % LOAD_FAILURE
    if TOOL_PREFIX in body:
        return "loaded", "the namespaced tool name `%s…` appeared in the client's own output" % TOOL_PREFIX
    if not text.strip():
        return "inconclusive", "the session produced no output at all"
    if not body.strip():
        return "inconclusive", ("nothing but the echo of this probe's own prompt — the "
                                "client never painted a frame")
    return "inconclusive", "the client painted, but no MCP signal appeared"


def answer(fd, chunk, stats=None):
    """Reply to the terminal queries in `chunk`; returns how many were answered.

    Anything query-shaped that is not in `QUERY_RESPONSES` is recorded in
    `stats["unanswered_queries"]` — the harness admitting what it could not
    answer, which is the difference between "the client did not draw" and "we did
    not give it what it asked for".
    """
    if isinstance(chunk, bytes):
        chunk = chunk.decode("utf-8", "replace")
    replies = []
    residue = chunk
    answered = 0
    for name, pattern, reply in QUERY_RESPONSES:
        found = pattern.findall(residue)
        if found:
            replies.append(reply * len(found))
            answered += len(found)
            if stats is not None:
                stats.setdefault("queries_answered", []).append((name, len(found)))
            residue = pattern.sub("", residue)
    modes = _DECRQM.findall(residue)
    if modes:
        replies.extend("\x1b[?%s;0$y" % mode for mode in modes)
        answered += len(modes)
        if stats is not None:
            stats.setdefault("queries_answered", []).append(("decrmq_declined", len(modes)))
        residue = _DECRQM.sub("", residue)
    if stats is not None:
        for unknown in set(_ANY_QUERY.findall(residue)):
            stats.setdefault("unanswered_queries", set()).add(unknown)
    if not replies:
        return 0
    try:
        os.write(fd, "".join(replies).encode("utf-8"))
    except OSError:
        return 0
    return answered


def spawn(command, cwd, columns=120, rows=44):
    """Start `command` on a fresh pty; returns `(process, master_fd)`."""
    if not POSIX:
        raise RuntimeError("a pty needs a POSIX platform")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    proc = subprocess.Popen(command, cwd=cwd, stdin=slave, stdout=slave, stderr=slave,
                            env=env, start_new_session=True)
    os.close(slave)
    return proc, master


def drive(proc, master, prompt, timeout, boot_quiet=8.0, warmup=20.0, stats=None):
    """Type `prompt` once the session goes quiet, then collect until `timeout`.

    Returns the raw transcript. Stops early the moment a verdict is decidable, so
    a rejected registry costs seconds instead of the whole budget.

    `boot_quiet` and `warmup` are the two dials the self-tests turn down: the
    probe must not type into a client that is still painting its first frame.
    """
    buf = bytearray()
    started = time.time()
    last_byte = started
    sent = False
    while time.time() - started < timeout:
        ready, _, _ = select.select([master], [], [], 0.5)
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            last_byte = time.time()
            if stats is not None and "first_byte_seconds" not in stats:
                stats["first_byte_seconds"] = round(last_byte - started, 1)
            answer(master, chunk, stats)
        text = bytes(buf).decode("utf-8", "replace")
        if verdict(text, prompt)[0] != "inconclusive":
            break
        # Quiet is measured from the last byte *or from the start*, so a client
        # that says nothing at all still gets prompted once `warmup` is up. Gating
        # this on "have we seen a byte yet" would mean a silent-but-ready client
        # is never asked anything and every session reports inconclusive.
        quiet = time.time() - last_byte
        if not sent and quiet > boot_quiet and time.time() - started > warmup:
            os.write(master, (prompt + "\r").encode("utf-8"))
            sent = True
            if stats is not None:
                stats["prompt_sent_seconds"] = round(time.time() - started, 1)
    return bytes(buf)


def terminate(proc):
    """Kill the whole session group, the way a probe should leave no client behind."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def run(command, cwd, prompt, timeout, transcript=None):
    """Start a live session and return `(status, reason, details)`."""
    started = time.time()
    proc, master = spawn(command, cwd)
    stats = {}
    try:
        raw = drive(proc, master, prompt, timeout, stats=stats)
    finally:
        terminate(proc)
        try:
            os.close(master)
        except OSError:
            pass
    text = raw.decode("utf-8", "replace")
    status, reason = verdict(text, prompt)
    details = {
        "client_output_chars": len(client_output(text, prompt)),
        "command": command,
        "cwd": cwd,
        "prompt": prompt,
        "first_byte_seconds": stats.get("first_byte_seconds"),
        "prompt_sent_seconds": stats.get("prompt_sent_seconds"),
        "seconds": round(time.time() - started, 1),
        "transcript_bytes": len(raw),
        "rendered_bytes": len(plain(text)),
        "registry": REGISTRY,
        "registry_present": os.path.isfile(REGISTRY),
        "status": status,
        "reason": reason,
    }
    answered = sorted(stats.get("queries_answered", []))
    details["queries_answered"] = ["%s x%d" % (name, count) for name, count in answered]
    details["unanswered_queries"] = sorted(stats.get("unanswered_queries", ()))
    if transcript:
        with open(transcript, "w", encoding="utf-8") as handle:
            handle.write(text)
        details["transcript"] = transcript
    return status, reason, details


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--freebuff", default="freebuff",
                        help="the client to drive (default: freebuff on PATH)")
    parser.add_argument("--cwd", default=HERE, help="where to start it (default: this repo)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="seconds to wait for a decidable session (default: 600)")
    parser.add_argument("--transcript", default=None, help="write the raw terminal output here")
    parser.add_argument("--summary", default=None, help="write the measured details as JSON here")
    parser.add_argument("--classify", metavar="FILE", default=None,
                        help="score a saved transcript instead of starting a session")
    args = parser.parse_args(argv)

    if args.classify:
        with open(args.classify, encoding="utf-8", errors="replace") as handle:
            status, reason = verdict(handle.read(), args.prompt)
        print(json.dumps({"status": status, "reason": reason, "classified": args.classify}))
        return {"loaded": 0, "rejected": 1}.get(status, 2)

    command = [args.freebuff, "--trust-agents"]
    status, reason, details = run(command, args.cwd, args.prompt, args.timeout,
                                  transcript=args.transcript)
    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as handle:
            json.dump(details, handle, indent=2, sort_keys=True)
    print(json.dumps(details, indent=2, sort_keys=True))
    print("verdict: %s — %s" % (status, reason), file=sys.stderr)
    return {"loaded": 0, "rejected": 1}.get(status, 2)


if __name__ == "__main__":
    sys.exit(main())
