import contextlib
import io
import os
import signal
import sys
import threading
from pathlib import Path

import pytest

from tooled import attached
from tooled.attached import CALL, Call, Stop, Stopped


def a_call(**given):
    return Call(
        **{'out': io.StringIO(), 'err': io.StringIO(), 'stdin': io.StringIO(''), 'cwd': '/w', 'environ': {}} | given
    )


@pytest.fixture
def call():
    """A call set here, as a session sets one in the thread that runs it."""
    made = a_call(environ={'TOOLED_MAX_BYTES': '10'}, tty=True, held='the session')
    token = CALL.set(made)
    yield made
    CALL.reset(token)


class TestWithoutACall:
    """A cold call takes what it needs from its process."""

    def test_none(self):
        assert (attached.current(), attached.held()) == (None, None)

    def test_the_process_s_variables(self):
        assert attached.environ() is os.environ

    def test_a_path_as_given(self):
        assert attached.path('out.json') == Path('out.json')
        assert attached.path('~/out.json') == Path.home() / 'out.json'

    def test_a_stop_of_its_own(self):
        assert attached.stop() is not attached.stop()

    def test_kept_changes_nothing(self):
        with attached.kept(io.StringIO()):
            assert attached.current() is None


class TestInACall:
    def test_its_own(self, call):
        assert (attached.current(), attached.held(), attached.environ()) == (call, 'the session', call.environ)
        assert attached.stop() is call.stop

    def test_a_relative_path_against_its_directory(self, call):
        assert attached.path('out.json') == Path('/w/out.json')
        assert attached.path('/srv/out.json') == Path('/srv/out.json')

    def test_kept_for_a_line_s_handlers(self, call):
        wrote = io.StringIO()
        with attached.kept(wrote):
            kept = attached.current()
            assert (kept.out, kept.stdin.read(), kept.tty, kept.stop) == (wrote, '', False, call.stop)
        assert attached.current() is call

    def test_a_thread_does_not_inherit_it(self, call):
        seen = []
        worker = threading.Thread(target=lambda: seen.append(attached.current()))
        worker.start()
        worker.join()
        assert seen == [None]


class TestStop:
    def test_the_first_signal_stands(self):
        stop = Stop()
        assert (stop.is_set(), stop.signum) == (False, signal.SIGTERM)
        stop.by(signal.SIGINT)
        stop.by(signal.SIGPIPE)
        assert (stop.is_set(), stop.signum) == (True, signal.SIGINT)

    def test_stopped_carries_it(self):
        assert Stopped(2).signum == 2


@contextlib.contextmanager
def routed():
    """The process's streams, routed by call while the block runs: in the test's body, as pytest's capture is set."""
    own = io.StringIO(), io.StringIO(), io.StringIO('typed')
    held = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr, sys.stdin = own
    restore = attached.install()
    try:
        yield own
    finally:
        restore()
        assert (sys.stdout, sys.stderr, sys.stdin) == own
        sys.stdout, sys.stderr, sys.stdin = held


class TestInstall:
    """A session's streams, routed by call: a call's to its own, anything else to the process's."""

    def test_outside_a_call_the_process_s(self):
        with routed() as own:
            sys.stdout.write('to the log\n')
            sys.stderr.write('also\n')
            got = sys.stdin.read(), sys.stdout.isatty()
        assert (own[0].getvalue(), own[1].getvalue(), *got) == ('to the log\n', 'also\n', 'typed', False)

    def test_in_a_call_its_own(self):
        made = a_call(stdin=io.StringIO('{"a": 1}'), tty=True)
        with routed() as own:
            token = CALL.set(made)
            try:
                sys.stdout.write('result\n')
                sys.stderr.write('note\n')
                sys.stdout.flush()
                got = sys.stdin.read(), sys.stdin.readline(), sys.stdout.isatty(), sys.stderr.isatty()
                with pytest.raises(io.UnsupportedOperation):
                    sys.stdout.fileno()
            finally:
                CALL.reset(token)
        assert got == ('{"a": 1}', '', True, False)
        assert (made.out.getvalue(), made.err.getvalue(), own[0].getvalue()) == ('result\n', 'note\n', '')

    def test_a_handler_s_prompt_reads_nothing(self):
        made, wrote = a_call(), io.StringIO()
        with routed():
            token = CALL.set(made)
            try:
                with attached.kept(wrote), pytest.raises(EOFError):
                    input('the note: ')
            finally:
                CALL.reset(token)
        assert (wrote.getvalue(), made.out.getvalue()) == ('the note: ', '')
