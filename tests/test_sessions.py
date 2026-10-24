import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from _support_policy import write

from tooled.cli import main
from tooled.errors import MeshError, UsageError
from tooled.sessions import IDLE, Where, _idle, _stdin_of, connected, runtime_dir

TESTS = Path(__file__).parent

HEAD = '[protocol]\n\n[app]\nfactory = "_support_app:chained"\nstate = "_support_app:state"\n'
BODY = '[[allow]]\nops = ["see", "do"]\n'


@pytest.fixture
def runtime(monkeypatch):
    """A runtime directory short enough for a socket's path, and no session named in the environment."""
    base = Path(tempfile.mkdtemp(prefix='tooled-'))
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(base))
    monkeypatch.delenv('TOOLED_SESSION', raising=False)
    yield base
    shutil.rmtree(base, ignore_errors=True)


class TestWhere:
    def test_a_name_lives_in_the_runtime_directory(self, runtime):
        where = Where.of('seat-a')
        assert (where.handle, where.socket, where.status, where.log) == (
            'seat-a',
            runtime / 'tooled' / 'seat-a.sock',
            runtime / 'tooled' / 'seat-a.json',
            runtime / 'tooled' / 'seat-a.log',
        )

    def test_a_path_lives_where_it_says(self, runtime, monkeypatch):
        monkeypatch.chdir(runtime)
        where = Where.of('./seat/s.sock')
        assert (where.handle, where.status, where.log) == (
            str(runtime / 'seat' / 's.sock'),
            runtime / 'seat' / 's.sock.json',
            runtime / 'seat' / 's.sock.log',
        )

    @pytest.mark.parametrize('handle', ['', '-a', 'a b', 'a' * 65, 'seat:1'])
    def test_neither_a_name_nor_a_path(self, runtime, handle):
        with pytest.raises(UsageError, match='a session is named by letters, digits'):
            Where.of(handle)

    def test_a_socket_path_too_long(self, runtime):
        with pytest.raises(UsageError, match='too long for a unix socket'):
            Where.of('/srv/' + 'x' * 120 + '.sock')

    def test_why_none_is_open(self, runtime):
        where = Where.of('gone')
        assert where.gone() == "no session 'gone' is open: tooled session start opens one"
        where.status.write_text(json.dumps({'open': False, 'ended': '2026-10-24T12:00:00Z', 'reason': 'idle for 60s'}))
        assert where.gone() == "no session 'gone' is open: it ended at 2026-10-24T12:00:00Z: idle for 60s"


class TestRuntimeDir:
    def test_made_the_user_s_alone(self, runtime):
        found = runtime_dir()
        assert (found, oct(found.stat().st_mode & 0o777)) == (runtime / 'tooled', '0o700')

    def test_one_others_can_read_is_refused(self, runtime):
        (runtime / 'tooled').mkdir(mode=0o755)
        (runtime / 'tooled').chmod(0o755)
        with pytest.raises(UsageError, match="must be the user's alone"):
            runtime_dir()

    def test_without_the_variable_under_the_temporary_directory(self, runtime, monkeypatch):
        monkeypatch.delenv('XDG_RUNTIME_DIR')
        monkeypatch.setattr(tempfile, 'tempdir', str(runtime))
        assert runtime_dir() == runtime / f'tooled-{os.getuid()}'


class TestTheClient:
    def test_stdin_read_only_when_asked(self, monkeypatch):
        import io

        monkeypatch.setattr(sys, 'stdin', io.StringIO('{"a": 1}'))
        assert _stdin_of(['call', 'x', '--args', '{}']) is None
        assert _stdin_of(['see', '-']) is None
        assert _stdin_of(['call', 'x', '--args', '-']) == '{"a": 1}'
        monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
        assert _stdin_of(['call', 'x', '--args=-']) == '{}'

    def test_idle_by_default_unless_tied(self):
        assert (_idle(None, tied=None), _idle(None, tied=1), _idle(0.0, tied=None), _idle(60.0, tied=1)) == (
            IDLE,
            None,
            None,
            60.0,
        )

    def test_a_session_that_is_not_open(self, runtime, capsys, monkeypatch, tmp_path):
        log = tmp_path / 'calls.jsonl'
        monkeypatch.setenv('TOOLED_LOG', str(log))
        monkeypatch.setenv('TOOLED_SESSION', 'nowhere')
        assert main(['operations']) == 5
        assert json.loads(capsys.readouterr().err) == {
            'error': 'mesh',
            'message': "no session 'nowhere' is open: tooled session start opens one",
        }
        (line,) = (json.loads(each) for each in log.read_text().splitlines())
        assert (line['verb'], line['session'], line['exit'], line['error']) == ('operations', 'nowhere', 5, 'mesh')

    def test_an_empty_name_is_none(self, runtime, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv('TOOLED_SESSION', 'nowhere')
        policy = write(tmp_path, BODY, head=HEAD)
        assert main(['operations', '--session', '', '--policy', str(policy)]) == 0
        assert json.loads(capsys.readouterr().out)

    def test_check_never_attaches(self, runtime, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv('TOOLED_SESSION', 'nowhere')
        assert main(['check', '--policy', str(write(tmp_path, BODY, head=HEAD))]) == 0


def tooled(*argv, env=None, check=False, timeout=60):
    """``python -m tooled``, as a harness runs it: its exit code, stdout and stderr."""
    environ = {**os.environ, 'PYTHONPATH': str(TESTS), **(env or {})}
    done = subprocess.run(
        [sys.executable, '-m', 'tooled', *argv],
        capture_output=True,
        text=True,
        env=environ,
        timeout=timeout,
        check=check,
    )
    return done.returncode, done.stdout, done.stderr


@pytest.fixture
def policy(tmp_path, runtime):
    return str(write(tmp_path, BODY, head=HEAD))


class TestStartAndStop:
    """The verbs, run as processes: a session started, attached to, asked about and stopped."""

    def test_a_session_s_life(self, policy, runtime):
        code, out, err = tooled('session', 'start', '--policy', policy, '--name', 'life')
        assert (code, err) == (0, '')
        started = json.loads(out)
        assert {key: started[key] for key in ('session', 'open', 'app', 'idle', 'for', 'calls')} == {
            'session': 'life',
            'open': True,
            'app': True,
            'idle': IDLE,
            'for': None,
            'calls': 0,
        }
        try:
            code, out, _ = tooled('see', 'station', 's1', 'show', env={'TOOLED_SESSION': 'life'})
            assert (code, json.loads(out)['reply']['stock']) == (0, 5)
            code, out, _ = tooled('session', 'status', 'life')
            assert (code, json.loads(out)['calls']) == (0, 1)
            code, out, _ = tooled('session', 'status')
            assert [each['session'] for each in json.loads(out)] == ['life']
            code, out, err = tooled('session', 'start', '--policy', policy, '--name', 'life')
            assert (code, json.loads(err)['message']) == (
                2,
                "a session 'life' is open already: tooled session stop life closes it",
            )
        finally:
            code, out, _ = tooled('session', 'stop', 'life')
        assert (code, json.loads(out)['reason'], json.loads(out)['calls']) == (0, 'stopped', 1)
        code, _, err = tooled('session', 'status', 'life')
        assert (code, json.loads(err)['message'].endswith(': stopped')) == (5, True)
        code, out, _ = tooled('session', 'status')
        assert [(each['session'], each['reason']) for each in json.loads(out)] == [('life', 'stopped')]
        assert tooled('session', 'status')[1] == '[]\n'  # listed once, then cleared

    def test_one_that_will_not_open(self, tmp_path, runtime):
        broken = write(tmp_path, BODY, head=HEAD.replace('_support_app:state', '_support_app:broken_state'))
        code, out, err = tooled('session', 'start', '--policy', str(broken), '--name', 'broken')
        assert (code, out, json.loads(err)['error']) == (5, '', 'mesh')
        assert json.loads(err)['message'].startswith("the app's state would not build")

    def test_a_policy_that_does_not_load(self, tmp_path, runtime):
        code, _, err = tooled('session', 'start', '--policy', str(tmp_path / 'none.toml'))
        assert (code, json.loads(err)['error']) == (2, 'policy')

    def test_stop_names_one(self, runtime):
        code, _, err = tooled('session', 'stop')
        assert (code, json.loads(err)['message']) == (2, 'which session: name one, or set $TOOLED_SESSION')

    def test_idle_ends_it(self, policy, runtime):
        code, out, _ = tooled('session', 'start', '--policy', policy, '--name', 'idle', '--idle', '0.5s')
        assert (code, json.loads(out)['idle']) == (0, 0.5)
        deadline = time.monotonic() + 10
        while ((found := Where.of('idle').record()) is None or found['open']) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert Where.of('idle').record()['reason'] == 'idle for 0.5s'

    def test_tied_to_a_process(self, policy, runtime):
        sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            code, out, _ = tooled('session', 'start', '--policy', policy, '--name', 'tied', '--for', str(sleeper.pid))
            assert (code, json.loads(out)['for'], json.loads(out)['idle']) == (0, sleeper.pid, None)
        finally:
            sleeper.kill()
            sleeper.wait()
        deadline = time.monotonic() + 10
        while Where.of('tied').record()['open'] and time.monotonic() < deadline:
            time.sleep(0.1)
        assert Where.of('tied').record()['reason'] == f'the process it was for ({sleeper.pid}) ended'

    def test_a_process_that_is_not_running(self, policy, runtime):
        code, _, err = tooled('session', 'start', '--policy', policy, '--for', '999999999')
        assert (code, json.loads(err)['message']) == (
            2,
            'tooled session start: argument --for: no process 999999999 is running',
        )


class TestRun:
    def test_a_command_in_a_session_of_its_own(self, policy, runtime):
        script = (
            'import os, subprocess, sys; print(os.environ["TOOLED_SESSION"]); '
            'done = subprocess.run([sys.executable, "-m", "tooled", "see", "station", "s1", "show"], '
            'capture_output=True, text=True); print(done.stdout.strip()); sys.exit(3)'
        )
        code, out, err = tooled(
            'session', 'run', '--policy', policy, '--name', 'cmd', '--', sys.executable, '-c', script
        )
        assert (code, err) == (3, '')
        name, reply = out.splitlines()
        assert (name, json.loads(reply)['reply']['stock']) == ('cmd', 5)
        assert Where.of('cmd').record()['reason'] == 'stopped'

    def test_no_command(self, policy, runtime):
        code, _, err = tooled('session', 'run', '--policy', policy)
        assert (code, json.loads(err)['message']) == (
            2,
            'tooled session run takes a command, after --: tooled session run -- COMMAND …',
        )

    def test_a_command_that_can_not_run(self, policy, runtime):
        code, _, err = tooled('session', 'run', '--policy', policy, '--name', 'bad', '--', '/nowhere/at/all')
        assert (code, json.loads(err)['message']) == (2, "could not run '/nowhere/at/all': No such file or directory")
        assert Where.of('bad').record()['reason'] == 'stopped'


class TestForeground:
    def test_held_here_until_stopped(self, policy, runtime):
        environ = {**os.environ, 'PYTHONPATH': str(TESTS)}
        argv = [sys.executable, '-m', 'tooled', 'session', 'start', '--foreground', '--policy', policy, '--name', 'fg']
        with subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, env=environ) as held:
            try:
                assert held.stdout is not None
                assert json.loads(held.stdout.readline())['session'] == 'fg'
                assert tooled('see', 'station', 's1', 'show', env={'TOOLED_SESSION': 'fg'})[0] == 0
                held.terminate()
                assert held.wait(30) == 0
            finally:
                held.kill()
        assert Where.of('fg').record()['reason'] == 'SIGTERM'


def test_errors_raise_as_tooled_s(runtime):
    where = Where.of('nowhere')
    with pytest.raises(MeshError):
        connected(where)
