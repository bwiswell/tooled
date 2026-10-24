import io
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import _support_app
import pytest
from _support_held import BODY, HEAD, WIRE, serving
from _support_policy import write

import tooled
import tooled.cli
import tooled.server
import tooled.tree
from tooled.cli import main
from tooled.server import Server
from tooled.sessions import Where, asked, connected, send


def ran(capsys, *argv):
    """Run the command: its exit code, and its stdout and stderr as written."""
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def attached(argv, name='t'):
    """``argv`` naming the session ``name``, after its verb, where a line's verb takes tooled's own options."""
    return [argv[0], '--session', name, *argv[1:]]


#: Calls whose output is the same, cold or attached: every verb that attaches, done, refused, failed and asked.
SAME = [
    ['operations'],
    ['describe', 'StationShow'],
    ['describe', 'relay/Run'],
    ['describe', 'Nowhere'],
    ['tools', '--format', 'anthropic'],
    ['tools', '--sizes'],
    ['see', 'station', 's1', 'show'],
    ['see', 'station s1 show'],
    ['see', '--select', 'reply.stock', 'station', 's1', 'show'],
    ['see', '--max-bytes', '50', 'station', 's1', 'show'],
    ['see', 'relay', 'status'],
    ['do', 'fleet', 'f1', 'mine'],
    ['do', 'orders', 'fleet', 'f1', 'jump', 'sol', 'then', 'dock', 'vesta'],
    ['do', 'dock', 'ceres'],
    ['do', '--yes', 'dock', 'vesta'],
    ['do', 'dock', 'ceres', '--yes'],
    ['see', 'fleet', 'f1', 'jump'],
    ['see', 'station', 's1', 'map'],
    ['do', 'relay', 'run'],
    ['see', 'fleet', 'f1', 'mine'],
    ['see', 'station', 's1', 'show', '--help'],
    ['see'],
    ['call', 'see_station_show', '--args', '{"station": "s1"}'],
    ['call', 'do_station_dock', '--args', '{"station": "vesta", "confirm": true}'],
    ['call', 'do_station_dock', '--args', '{"station": "ceres", "confirm": true}'],
    ['call', 'see_station_show', '--args', '{"station": "s1", "stock": 3}'],
    ['call', 'see_nothing'],
]


class TestTheSameOutput:
    """An attached call writes what the cold call writes, byte for byte, and exits as it does."""

    @pytest.mark.parametrize('argv', SAME, ids=' '.join)
    def test_as_cold(self, policy, capsys, argv):
        cold = ran(capsys, *argv, '--policy', policy) if argv[0] not in {'see', 'do'} else None
        if cold is None:  # a line's verb: tooled's options first
            cold = ran(capsys, argv[0], '--policy', policy, *argv[1:])
        with serving(policy):
            warm = ran(capsys, *attached([argv[0], '--policy', policy, *argv[1:]]))
            bare = ran(capsys, *attached(argv))  # the session's own policy, named by nothing
        assert warm == cold
        assert bare == cold

    def test_tooled_s_own_help(self, policy, capsys):
        cold = main(['see', '--help'])
        cold_out = capsys.readouterr().out
        with serving(policy):
            warm = main(attached(['see', '--help']))
        assert (warm, capsys.readouterr().out) == (cold, cold_out)
        assert (cold, '--session <session>' in cold_out) == (0, True)

    def test_a_wait_met(self, policy, capsys):
        argv = ['see', '--policy', policy, 'station', 's1', 'show', '--until', 'stock=5', '--timeout', '2s']
        cold = ran(capsys, *argv)
        with serving(policy):
            warm = ran(capsys, *attached(argv))
        assert (warm[0], json.loads(warm[1])['met']) == (cold[0], json.loads(cold[1])['met']) == (0, True)

    def test_the_call_s_cap_from_its_environment(self, policy, capsys, monkeypatch):
        monkeypatch.setenv('TOOLED_MAX_BYTES', '40')
        cold = ran(capsys, 'see', '--policy', policy, 'station', 's1', 'show')
        with serving(policy):
            warm = ran(capsys, *attached(['see', 'station', 's1', 'show']))
        assert warm == cold
        assert cold[0] == 6

    def test_out_against_the_call_s_directory(self, policy, capsys, monkeypatch, tmp_path):
        here = tmp_path / 'here'
        here.mkdir()
        with serving(policy):
            monkeypatch.chdir(here)
            warm = ran(capsys, *attached(['see', '--out', 'station.json', 'station', 's1', 'show']))
        written = here / 'station.json'
        assert json.loads(warm[1]) == {'written': str(written), 'bytes': len(written.read_bytes())}
        assert json.loads((here / 'station.json').read_text())['reply']['stock'] == 5

    def test_arguments_on_the_call_s_stdin(self, policy, capsys, monkeypatch):
        monkeypatch.setattr(sys, 'stdin', io.StringIO('{"station": "s2"}'))
        cold = ran(capsys, 'call', 'see_station_show', '--args', '-', '--policy', policy)
        monkeypatch.setattr(sys, 'stdin', io.StringIO('{"station": "s2"}'))
        with serving(policy):
            warm = ran(capsys, *attached(['call', 'see_station_show', '--args', '-']))
        assert warm == cold
        assert json.loads(cold[1])['reply']['station'] == 's2'

    def test_the_call_log_names_the_session(self, policy, capsys, monkeypatch, tmp_path):
        log = tmp_path / 'calls.jsonl'
        monkeypatch.setenv('TOOLED_LOG', str(log))
        ran(capsys, 'see', '--policy', policy, 'station', 's1', 'show')
        with serving(policy):
            ran(capsys, *attached(['see', 'station', 's1', 'show']))
        cold, warm = (json.loads(line) for line in log.read_text().splitlines())
        assert (cold['session'], warm['session'], warm['flags']) == (None, 't', ['--session'])
        mine = {'at', 'seconds', 'session', 'flags'}
        assert {k: v for k, v in warm.items() if k not in mine} == {k: v for k, v in cold.items() if k not in mine}
        assert warm['operation'] == 'StationShow'


class TestWhatItHolds:
    def test_the_state_built_once(self, policy, capsys):
        built = len(_support_app.BUILT)
        with serving(policy) as server:
            assert len(_support_app.BUILT) == built + 1
            for _ in range(3):
                ran(capsys, *attached(['see', 'relay', 'status']))
            assert len(_support_app.BUILT) == built + 1
            state = _support_app.BUILT[-1]
            assert (state.entered, state.left, state.injected) == (True, False, {'ticket': 'tk-1'})
            assert server.status()['calls'] == 3
        assert state.left  # left as the session closed

    def test_injected_values_read_at_the_start(self, policy, capsys, monkeypatch):
        with serving(policy) as server:
            monkeypatch.setenv('DEMO_TICKET', 'tk-2')
            assert server.held.injected == {'ticket': 'tk-1'}
            _, out, _ = ran(capsys, *attached(['see', 'relay', 'status']))
        assert json.loads(out)['text'] == "up, bound ['fleet'], injected ['ticket']"

    def test_a_source_it_can_not_read_keeps_it_from_starting(self, policy, monkeypatch):
        from tooled.errors import PolicyFileError

        monkeypatch.delenv('DEMO_TICKET')
        server = Server(Where.of('t'), Path(policy).resolve(), idle=None, tied=None)
        with pytest.raises(PolicyFileError, match='DEMO_TICKET'):
            server.open()
        assert not server.where.socket.exists()

    def test_another_policy_is_refused(self, policy, capsys, tmp_path, monkeypatch):
        other = write(tmp_path, BODY, head=HEAD, name='other.toml')
        with serving(policy):
            code, out, err = ran(capsys, *attached(['see', '--policy', str(other), 'station', 's1', 'show']))
            assert (code, out, json.loads(err)['error']) == (2, '', 'usage')
            assert json.loads(err)['message'].endswith(
                f'and this call names {other} (--policy): a session runs only its own'
            )
            monkeypatch.setenv('TOOLED_POLICY', str(other))
            code, _, err = ran(capsys, *attached(['operations']))
            assert (code, f'(${"TOOLED_POLICY"})' in json.loads(err)['message']) == (2, True)
            monkeypatch.setenv('TOOLED_POLICY', policy)
            assert ran(capsys, *attached(['operations']))[0] == 0

    def test_a_relative_policy_against_the_call_s_directory(self, policy, capsys, monkeypatch):
        with serving(policy):
            monkeypatch.chdir(Path(policy).parent)
            assert ran(capsys, *attached(['operations', '--policy', Path(policy).name]))[0] == 0


def raw(server, request):
    """A connection to ``server``, ``request`` sent: a client of the test's own."""
    link = connected(server.where)
    send(link, {'tooled': tooled.__version__, 'cwd': '/', 'env': {}, 'tty': False, 'stdin': None} | request)
    return link, link.makefile('r', encoding='utf-8')


def frames(lines, *, until='end'):
    out = []
    for line in lines:
        out.append(json.loads(line))
        if until in out[-1]:
            break
    return out


class TestCallsSideBySide:
    def test_a_call_answered_while_a_watch_runs(self, desk, capsys):
        with serving(desk) as server:
            link, lines = raw(server, {'argv': ['see', 'clock', 'show', '--watch']})
            with link, lines:
                first = json.loads(lines.readline())
                assert json.loads(first['out'])['reply']['tick'] >= 1
                code, _, _ = ran(capsys, *attached(['see', 'clock', 'show']))
                assert (code, server.status()['in_flight']) == (0, 1)
                send(link, {'stop': 2})
                end = frames(lines)[-1]
            assert (end['end'], end['entry']['verb']) == (0, 'see')  # stopped, as a signal stops a cold watch

    def test_a_client_gone_ends_its_call(self, desk):
        with serving(desk) as server:
            link, lines = raw(server, {'argv': ['see', 'clock', 'show', '--watch']})
            lines.readline()
            lines.close()
            link.close()
            deadline = time.monotonic() + 5
            while server.status()['in_flight'] and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.status()['in_flight'] == 0

    def test_a_line_stopped_is_cut_short(self, desk):
        _support_app.RELEASED.clear()
        try:
            with serving(desk) as server:
                link, lines = raw(server, {'argv': ['see', 'bell', 'ring']})
                with link, lines:
                    time.sleep(0.2)
                    send(link, {'stop': 15})
                    assert frames(lines)[-1] == {'end': 143, 'cut': True}
        finally:
            _support_app.RELEASED.set()

    def test_another_version_of_tooled_is_refused(self, policy):
        with serving(policy) as server:
            link, lines = raw(server, {'argv': ['operations'], 'tooled': '0.0.1'})
            with link, lines:
                got = frames(lines)
        error = json.loads(got[0]['err'])
        assert (error['error'], got[-1]['end']) == ('usage', 2)
        assert error['message'] == (
            f"the session 't' runs tooled {tooled.__version__}, and this call is tooled 0.0.1: start the session again "
            'with this tooled'
        )

    def test_another_user_is_refused(self, policy, capsys, monkeypatch):
        monkeypatch.setattr(tooled.server, '_peer_is_us', lambda link: False)
        with serving(policy), pytest.raises(SystemExit) as exc:
            main(attached(['operations']))
        assert (exc.value.code, json.loads(capsys.readouterr().err)['error']) == (3, 'refused')

    def test_a_crash_is_cut_short_with_its_traceback(self, policy, capsys, monkeypatch):
        def broken(given, entry):
            msg = 'a bug'
            raise RuntimeError(msg)

        with serving(policy):
            monkeypatch.setattr(tooled.cli, 'ran', broken)
            with pytest.raises(SystemExit) as exc:
                main(attached(['operations']))
        assert exc.value.code == 1
        assert capsys.readouterr().err.rstrip().endswith('RuntimeError: a bug')


class TestTheGuard:
    def test_a_line_past_its_bound_is_cancelled_and_the_session_goes_on(self, desk, capsys):
        with serving(desk):
            code, _, err = ran(capsys, *attached(['see', '--timeout', '0.3', 'bell', 'ring']))
            assert (code, json.loads(err)['error']) == (7, 'timeout')
            assert ran(capsys, *attached(['see', 'clock', 'show']))[0] == 0

    def test_a_prompt_fails_at_once(self, desk, capsys):
        with serving(desk):
            code, out, err = ran(capsys, *attached(['do', 'note', 'edit']))
        assert (code, json.loads(out)['error']['message']) == (1, 'EOFError: EOF when reading a line')
        assert json.loads(err)['note'] == "the line's handler wrote 10 characters to stdout, kept out of the result"

    def test_a_handler_that_blocks_the_loop_ends_the_session(self, desk, capsys, monkeypatch):
        monkeypatch.setattr(tooled.tree, 'GRACE', 0.2)
        _support_app.RELEASED.clear()
        try:
            with serving(desk, name='w') as server:
                code, out, err = ran(capsys, *attached(['see', '--timeout', '0.3', 'screen', 'show'], name='w'))
                assert (code, out, json.loads(err)['error']) == (5, '', 'mesh')
                assert json.loads(err)['message'].startswith(
                    "the session's loop is blocked: a handler of 'screen show'"
                )
                deadline = time.monotonic() + 10
                while server.reason is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert server.wedged
        finally:
            _support_app.RELEASED.set()
        assert server.record['reason'].startswith("the session's loop is blocked")
        code, _, err = ran(capsys, *attached(['see', 'clock', 'show'], name='w'))
        assert code == 5
        assert json.loads(err)['message'].startswith("no session 'w' is open: it ended at ")


class TestEndings:
    def test_idle(self, policy):
        with serving(policy, idle=0.2) as server:
            deadline = time.monotonic() + 5
            while server.reason is None and time.monotonic() < deadline:
                time.sleep(0.05)
        assert server.record['reason'] == 'idle for 0.2s'

    def test_a_call_in_flight_is_not_idle(self, desk):
        with serving(desk, idle=0.2) as server:
            link, lines = raw(server, {'argv': ['see', 'clock', 'show', '--watch']})
            with link, lines:
                lines.readline()
                time.sleep(0.6)
                assert server.reason is None
                send(link, {'stop': 2})
                frames(lines)

    def test_the_process_it_is_for(self, policy):
        sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with serving(policy, tied=sleeper.pid) as server:
                sleeper.kill()
                sleeper.wait()
                deadline = time.monotonic() + 5
                while server.reason is None and time.monotonic() < deadline:
                    time.sleep(0.05)
        finally:
            sleeper.kill()
        assert server.record['reason'] == f'the process it was for ({sleeper.pid}) ended'

    def test_asked_to_stop(self, policy):
        with serving(policy, name='s') as server:
            where = server.where
            status = asked(where, {'control': 'status'}, until='status', timeout=5)
            assert (status['session'], status['open'], status['app'], status['policy']) == (
                's',
                True,
                True,
                str(server.path),
            )
            stopped = asked(where, {'control': 'stop'}, until='stopped', timeout=30)
        assert {key: stopped[key] for key in ('session', 'open', 'reason', 'calls')} == {
            'session': 's',
            'open': False,
            'reason': 'stopped',
            'calls': 0,
        }
        assert not where.socket.exists()
        assert where.record() == stopped
        assert oct(where.status.stat().st_mode & 0o777) == '0o600'

    def test_a_watch_in_flight_is_stopped_as_its_client_would(self, desk, monkeypatch):
        monkeypatch.setattr(tooled.server, 'ENDING', 0.2)
        with serving(desk) as server:
            link, lines = raw(server, {'argv': ['see', 'clock', 'show', '--watch']})
            lines.readline()
        with link, lines:
            assert frames(lines)[-1]['end'] == 0
        assert server.record['reason'] == 'stopped'

    def test_its_socket_is_the_user_s_alone(self, policy):
        with serving(policy) as server:
            assert oct(server.where.socket.stat().st_mode & 0o777) == '0o600'
            with socket.socket(socket.AF_UNIX) as link:
                link.connect(str(server.where.socket))


#: The wire's verbs, whose output is the same cold or attached: read, asked, written, waited on and listened to.
SAME_WIRE = [
    ['fetch', 'Station'],
    ['fetch', 'Station', 'station=s2', '--select', 'stock'],
    ['fetch', 'MoveResult'],
    ['query', 'LookReply', 'player=ann', '--request', '{"kind": "ping", "station": "s1"}', '--param', 'depth=2'],
    ['query', 'LookReply', 'player=ann', '--request', '{"kind": "dev-peek"}'],
    ['invoke', 'MoveResult', 'player=ann', '--request', '{"to": "s2"}'],
    ['invoke', 'MoveResult', 'player=ann', '--request', '{"to": "nowhere"}'],
    ['invoke', 'MoveResult', 'player=ann', '--request', '{"to": "broken"}'],
    ['wait', 'Station', 'station=s2', '--until', 'stock=5'],
    ['wait', 'Station', 'station=s2', '--until', 'stock=99', '--timeout', '0.3'],
    ['listen', 'Station', 'station=s2', '--count', '1'],
    ['call', 'fetch_station', '--args', '{"station": "s1"}'],
    ['call', 'invoke_move_result', '--args', '{"player": "ann", "to": "s2"}'],
    ['call', 'wait_station', '--args', '{"station": "s2", "until": {"stock": 5}}'],
    ['call', 'listen_notice', '--args', '{"player": "ann", "timeout": 0.2}'],
]


class TestTheWire:
    """The session holds the mesh: the wire's verbs attach, and print what they would cold."""

    @pytest.mark.parametrize('argv', SAME_WIRE, ids=' '.join)
    def test_as_cold(self, wire, capsys, argv):
        cold = ran(capsys, *argv, '--policy', wire)
        with serving(wire):
            warm = ran(capsys, *attached(argv))
        assert warm == cold

    def test_the_mesh_opened_once(self, wire, capsys, monkeypatch):
        import tooled.executor

        with serving(wire) as server:
            opened = []
            real = tooled.executor.open_session
            monkeypatch.setattr(tooled.executor, 'open_session', lambda config: opened.append(config) or real(config))
            for _ in range(3):
                assert ran(capsys, *attached(['fetch', 'Station', 'station=s1']))[0] == 0
            assert (opened, server.status()['mesh'], server.status()['app']) == ([], True, False)
            mesh = server.held.mesh
        assert mesh.is_closed()

    def test_a_token_read_at_the_start_and_an_id_made_each_write(self, wire, world, capsys, monkeypatch):
        with serving(wire):
            monkeypatch.setenv('DEMO_TOKEN', 'tok-bo')
            for _ in range(2):
                assert (
                    ran(capsys, *attached(['invoke', 'MoveResult', 'player=ann', '--request', '{"to": "s2"}']))[0] == 0
                )
        first, second = world.asked[-2:]
        assert (first.token, second.token) == ('tok-ann', 'tok-ann')
        assert first.move_id != second.move_id

    def test_a_listen_beside_other_calls_and_stopped_by_its_client(self, wire, world, capsys):
        import _support_protocol as demo

        with serving(wire) as server:
            link, lines = raw(server, {'argv': ['listen', 'Notice', 'player=ann']})
            heard = threading.Event()

            def say() -> None:  # until the listen hears one: it may not be subscribed yet
                while not heard.wait(0.1):
                    demo.Notice(world='w1', player='ann', text='hello').send(session=world.session)

            with link, lines:
                assert ran(capsys, *attached(['fetch', 'Station', 'station=s1']))[0] == 0
                saying = threading.Thread(target=say, daemon=True)
                saying.start()
                got = json.loads(lines.readline())
                heard.set()
                saying.join(5)
                assert json.loads(got['out'])['text'] == 'hello'
                send(link, {'stop': 2})
                end = frames(lines)[-1]
        assert (end['end'], end['entry']['verb']) == (0, 'listen')

    def test_a_wait_stopped_by_its_client(self, wire):
        with serving(wire) as server:
            link, lines = raw(server, {'argv': ['wait', 'Station', 'station=s2', '--until', 'stock=99']})
            with link, lines:
                time.sleep(0.3)
                asked = time.monotonic()
                send(link, {'stop': 15})
                got = frames(lines)
                took = time.monotonic() - asked
        assert (got[-1]['end'], json.loads(got[0]['err'])['error']) == (7, 'timeout')
        assert took < 5  # ended by the stop, not its 30s

    def test_a_mesh_that_will_not_open(self, tmp_path, runtime, monkeypatch):
        from _support_policy import HEAD as MESH
        from _support_session import free_port

        from tooled.errors import MeshError

        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        policy = write(tmp_path, WIRE, head=MESH.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}'))
        server = Server(Where.of('t'), policy.resolve(), idle=None, tied=None)
        with pytest.raises(MeshError, match='the session would not open'):
            server.open()
        assert not server.where.socket.exists()

    def test_a_mesh_and_an_app_both_held(self, wire, capsys):
        both = Path(wire).read_text().replace('[protocol]\n', '[protocol]\n', 1) + (
            '\n[app]\nfactory = "_support_app:factory"\n\n[[allow]]\nops = ["see"]\n'
        )
        Path(wire).write_text(both)
        with serving(wire) as server:
            assert (server.status()['mesh'], server.status()['app']) == (True, True)
            assert ran(capsys, *attached(['fetch', 'Status']))[0] == 0  # Station's command shows instead of it
            assert ran(capsys, *attached(['see', 'station', 's1', 'show']))[0] == 0

    def test_a_managed_mesh_held(self, wire, capsys):
        Path(wire).write_text(
            Path(wire).read_text().replace('mode = "client"\n', 'mode = "client"\nauto_reconnect = true\n')
        )
        cold = ran(capsys, 'fetch', 'Station', '--policy', wire)
        with serving(wire) as server:
            assert type(server.held.mesh).__name__ == 'ManagedSession'
            assert ran(capsys, *attached(['fetch', 'Station'])) == cold
