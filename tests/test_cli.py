import io
import json
import os
import select
import signal
import subprocess
import sys
import time

import pytest
import seared
import zeared
from _support_policy import HEAD, write
from _support_session import free_port

import parsed
from parsed.cli import main, version

POLICY = """
[bind]
world = "w1"

[inject.token]
env = "DEMO_TOKEN"

[[allow]]
ops = ["read", "query"]
keys = ["demo/w1/**"]

[[deny]]
ops = ["query"]
variants = ["dev-*"]
"""


def run(capsys, *argv):
    """Run the command; return its exit code, its stdout as JSON (or None), and its stderr as JSON (or None)."""
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, json.loads(out) if out else None, json.loads(err) if err else None


class TestVersion:
    def test_names_parsed_seared_and_zeared(self):
        assert version() == f'parsed {parsed.__version__} (seared {seared.__version__}, zeared {zeared.__version__})'

    def test_prints_and_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(['--version'])
        assert exc.value.code == 0
        assert capsys.readouterr().out == version() + '\n'

    def test_python_dash_m_runs_the_same_command(self):
        done = subprocess.run(
            [sys.executable, '-m', 'parsed', '--version'], capture_output=True, text=True, check=False, timeout=60
        )
        assert done.returncode == 0
        assert done.stdout == version() + '\n'


class TestUsage:
    def test_a_verb_is_required(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2
        assert 'the following arguments are required: verb' in capsys.readouterr().err

    def test_an_unknown_flag_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(['check', '--nope'])
        assert exc.value.code == 2
        assert 'unrecognized arguments: --nope' in capsys.readouterr().err


class TestCheck:
    def test_summarizes_what_the_policy_shows(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-secret-value')
        path = write(tmp_path, POLICY)
        code, out, err = run(capsys, 'check', '--policy', str(path))
        assert (code, err) == (0, None)
        assert out == {
            'policy': str(path),
            'mesh': {'mode': 'client', 'router': 'tcp/127.0.0.1:7447', 'connect': []},
            'packages': ['_support_protocol'],
            'contracts': 8,
            'visible': {
                'read': ['Notice', 'Seen', 'Station', 'Status', 'Trace'],
                'query': ['LookReply'],
            },
            'hidden': 2,
            'bind': {'world': 'w1'},
            'inject': {'token': {'source': 'env DEMO_TOKEN', 'length': 16}},
        }

    def test_never_prints_an_injected_value(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-secret-value')
        main(['check', '--policy', str(write(tmp_path, POLICY)), '--pretty'])
        out, err = capsys.readouterr()
        assert 'tok-secret-value' not in out + err
        assert out.startswith('{\n  "policy"')

    def test_finds_the_policy_in_the_environment(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv('PARSED_POLICY', str(write(tmp_path)))
        code, out, _ = run(capsys, 'check')
        assert code == 0
        assert out['visible'] == {}
        assert out['hidden'] == 8

    def test_a_missing_policy_is_a_json_error_on_stderr(self, tmp_path, capsys):
        code, out, err = run(capsys, 'check', '--policy', str(tmp_path / 'nope.toml'))
        assert (code, out) == (2, None)
        assert err['error'] == 'policy'
        assert 'no policy file' in err['message']

    def test_a_typo_reports_its_path(self, tmp_path, capsys):
        code, _, err = run(capsys, 'check', '--policy', str(write(tmp_path, '[[allow]]\nops = ["read"]\nkey = []\n')))
        assert code == 2
        assert err['path'] == ['allow', 0, 'key']

    def test_an_unset_inject_source_fails_the_check(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv('DEMO_TOKEN', raising=False)
        code, _, err = run(capsys, 'check', '--policy', str(write(tmp_path, POLICY)))
        assert code == 2
        assert err == {
            'error': 'policy',
            'message': "inject 'token': $DEMO_TOKEN is not set",
            'path': ['inject', 'token'],
        }

    def test_a_protocol_that_will_not_import(self, tmp_path, capsys):
        head = HEAD.replace('["_support_protocol"]', '["no_such_protocol"]')
        code, _, err = run(capsys, 'check', '--policy', str(write(tmp_path, head=head)))
        assert code == 2
        assert err['error'] == 'catalog'
        assert err['path'] == ['protocol', 'packages', 0]


class TestCheckConnect:
    def test_opens_the_session(self, tmp_path, capsys, listening):
        head = HEAD.replace('tcp/127.0.0.1:7447', listening)
        code, out, _ = run(capsys, 'check', '--connect', '--policy', str(write(tmp_path, head=head)))
        assert code == 0
        assert out['connect']['ok'] is True
        assert out['connect']['seconds'] >= 0

    def test_an_unreachable_mesh_exits_five(self, tmp_path, capsys):
        head = HEAD.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}')
        code, out, err = run(capsys, 'check', '--connect', '--policy', str(write(tmp_path, head=head)))
        assert (code, out) == (5, None)
        assert err['error'] == 'mesh'


class TestContracts:
    def test_lists_what_the_policy_shows(self, tmp_path, capsys):
        code, out, err = run(capsys, 'contracts', '--policy', str(write(tmp_path, POLICY)))
        assert (code, err) == (0, None)
        assert [row['name'] for row in out] == ['LookReply', 'Notice', 'Seen', 'Station', 'Status', 'Trace']
        assert out[0]['verbs'] == ['query']
        assert out[0]['topic'] == 'demo/w1/look/{player}'

    def test_an_empty_policy_lists_nothing(self, tmp_path, capsys):
        assert run(capsys, 'contracts', '--policy', str(write(tmp_path))) == (0, [], None)


class TestDescribe:
    def test_one_contract(self, tmp_path, capsys):
        code, out, _ = run(capsys, 'describe', 'LookReply', '--policy', str(write(tmp_path, POLICY)))
        assert code == 0
        assert out['name'] == 'LookReply'
        assert [v['tag'] for v in out['variants']] == ['ping', 'pong']
        assert 'token' not in out['request']['properties']

    def test_one_variant(self, tmp_path, capsys):
        code, out, _ = run(
            capsys, 'describe', 'LookReply', '--variant', 'pong', '--policy', str(write(tmp_path, POLICY))
        )
        assert code == 0
        assert out['schema']['title'] == 'Pong'

    def test_a_denied_variant_is_refused(self, tmp_path, capsys):
        policy = str(write(tmp_path, POLICY))
        code, out, err = run(capsys, 'describe', 'LookReply', '--variant', 'dev-peek', '--policy', policy)
        assert (code, out) == (3, None)
        assert err['error'] == 'refused'

    def test_a_contract_the_policy_hides_is_refused(self, tmp_path, capsys):
        code, _, err = run(capsys, 'describe', 'MoveResult', '--policy', str(write(tmp_path, POLICY)))
        assert code == 3
        assert err == {'error': 'refused', 'message': 'the policy does not show MoveResult'}

    def test_an_unknown_contract_is_a_usage_error(self, tmp_path, capsys):
        code, _, err = run(capsys, 'describe', 'Stations', '--policy', str(write(tmp_path, POLICY)))
        assert code == 2
        assert err == {'error': 'usage', 'message': "no contract named 'Stations'"}

    def test_an_ambiguous_name_names_both(self, tmp_path, capsys):
        head = HEAD.replace('["_support_protocol"]', '["_support_protocol", "_support_protocol_two"]')
        policy = str(write(tmp_path, '[[allow]]\nops = ["read"]\n', head=head))
        code, _, err = run(capsys, 'describe', 'Station', '--policy', policy)
        assert code == 2
        assert '_support_protocol_two:Station' in err['message']
        code, out, _ = run(capsys, 'describe', '_support_protocol_two:Station', '--policy', policy)
        assert code == 0
        assert out['name'] == '_support_protocol_two:Station'


READS = """
[bind]
world = "w1"

[inject.token]
env = "DEMO_TOKEN"

[[allow]]
ops = ["read", "query"]
keys = ["demo/w1/**"]

[[deny]]
ops = ["query"]
variants = ["dev-*"]
"""


@pytest.fixture
def reads(tmp_path, world, monkeypatch):
    """A policy over the served fixture world, with its token set."""
    monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
    return str(write(tmp_path, READS, head=HEAD.replace('tcp/127.0.0.1:7447', world.endpoint)))


def lines(err):
    return [json.loads(line) for line in err.splitlines()]


class TestFetch:
    def test_every_value_sorted(self, reads, capsys):
        code, out, err = run(capsys, 'fetch', 'Station', '--policy', reads)
        assert (code, err) == (0, None)
        assert out == [{'world': 'w1', 'station': 's1', 'stock': 3}, {'world': 'w1', 'station': 's2', 'stock': 5}]

    def test_narrowed_by_a_slot(self, reads, capsys):
        assert run(capsys, 'fetch', 'Station', 'station=s2', '--policy', reads)[1] == [
            {'world': 'w1', 'station': 's2', 'stock': 5}
        ]

    def test_a_queryable_is_not_fetched(self, reads, capsys):
        code, _, err = run(capsys, 'fetch', 'LookReply', '--policy', reads)
        assert code == 2
        assert err['message'] == 'fetch does not act on LookReply, a queryable contract; what does: query'

    def test_a_write_has_no_verb_yet(self, tmp_path, world, capsys):
        head = HEAD.replace('tcp/127.0.0.1:7447', world.endpoint)
        policy = str(write(tmp_path, '[[allow]]\nops = ["invoke"]\n', head=head))
        code, _, err = run(capsys, 'query', 'MoveResult', '--policy', policy)
        assert code == 2
        assert err['message'].endswith('what does: nothing in this version of parsed')

    def test_a_hidden_contract_is_refused(self, reads, capsys):
        code, _, err = run(capsys, 'fetch', 'MoveResult', '--policy', reads)
        assert (code, err['error']) == (3, 'refused')

    @pytest.mark.parametrize(('slots', 'message'), [(['station'], 'name=value'), (['station=a', 'station=b'], 'twice')])
    def test_malformed_slot_arguments(self, reads, capsys, slots, message):
        code, _, err = run(capsys, 'fetch', 'Station', *slots, '--policy', reads)
        assert code == 2
        assert message in err['message']

    def test_an_unreachable_mesh_exits_five(self, tmp_path, capsys):
        head = HEAD.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}')
        code, _, err = run(capsys, 'fetch', 'Station', '--policy', str(write(tmp_path, READS, head=head)))
        assert (code, err['error']) == (5, 'mesh')


class TestQuery:
    def test_a_request_as_json(self, reads, capsys):
        code, out, err = run(
            capsys,
            'query',
            'LookReply',
            'player=ann',
            '--request',
            '{"kind": "ping", "station": "s1"}',
            '--policy',
            reads,
        )
        assert (code, err) == (0, None)
        assert out == [
            {
                'world': 'w1',
                'player': 'ann',
                'seen': "ping {'station': 's1'} at [('player', 'ann'), ('world', 'w1')] {}",
            }
        ]

    def test_a_request_on_stdin(self, reads, capsys, monkeypatch):
        monkeypatch.setattr('sys.stdin', io.StringIO('{"kind": "pong", "times": 2}'))
        code, out, _ = run(capsys, 'query', 'LookReply', 'player=ann', '--request', '-', '--policy', reads)
        assert code == 0
        assert out[0]['seen'].startswith("pong {'times': 2}")

    def test_a_request_from_a_file_with_params(self, reads, capsys, tmp_path):
        (tmp_path / 'look.json').write_text('{"kind": "ping", "station": "s2"}')
        argv = ['query', 'LookReply', 'player=bo', '--request', f'@{tmp_path / "look.json"}', '--param', 'since=3']
        code, out, _ = run(capsys, *argv, '--policy', reads)
        assert code == 0
        assert out[0]['seen'] == "ping {'station': 's2'} at [('player', 'bo'), ('world', 'w1')] {'since': '3'}"

    @pytest.mark.parametrize(
        ('request_text', 'code', 'kind'),
        [
            ('{"kind": "ping"', 2, 'usage'),
            ('[1, 2]', 2, 'usage'),
            ('@/no/such/file.json', 2, 'usage'),
            ('{"kind": "ping"}', 4, 'validation'),
            ('{"kind": "dev-peek"}', 3, 'refused'),
            ('{"kind": "ping", "station": "s1", "token": "mine"}', 3, 'refused'),
        ],
    )
    def test_requests_that_go_nowhere(self, reads, capsys, request_text, code, kind):
        got, out, err = run(capsys, 'query', 'LookReply', 'player=ann', '--request', request_text, '--policy', reads)
        assert (got, out, err['error']) == (code, None, kind)

    def test_no_reply_is_a_note_not_an_error(self, tmp_path, listening, capsys, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        policy = str(write(tmp_path, READS, head=HEAD.replace('tcp/127.0.0.1:7447', listening)))  # nothing served
        argv = [
            'query',
            'LookReply',
            'player=ann',
            '--request',
            '{"kind": "ping", "station": "s1"}',
            '--timeout',
            '0.3',
        ]
        assert main([*argv, '--policy', policy]) == 0
        out, err = capsys.readouterr()
        assert json.loads(out) == []
        assert lines(err) == [{'note': 'no replies within 0.3s'}]

    def test_a_timeout_above_zero(self, reads, capsys):
        with pytest.raises(SystemExit) as exc:
            main(['query', 'LookReply', '--timeout', '0', '--policy', reads])
        assert exc.value.code == 2
        assert 'above zero' in capsys.readouterr().err


class TestEndToEnd:
    def test_python_dash_m_fetches_over_tcp(self, reads):
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
        done = subprocess.run(
            [sys.executable, '-m', 'parsed', 'fetch', 'Station', 'station=s1', '--policy', reads],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env=env,
        )
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == [{'world': 'w1', 'station': 's1', 'stock': 3}]


class TestListen:
    def test_the_retained_set_one_line_each(self, reads, capsys):
        assert main(['listen', 'Station', '--count', '2', '--policy', reads]) == 0
        out, err = capsys.readouterr()
        rows = [json.loads(line) for line in out.splitlines()]
        assert sorted(row['station'] for row in rows) == ['s1', 's2']
        assert err == ''

    def test_meta_wraps_each_value(self, reads, capsys):
        assert main(['listen', 'Station', 'station=s2', '--count', '1', '--meta', '--policy', reads]) == 0
        (row,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert (row['key'], row['origin'], row['value']['stock']) == ('demo/w1/station/s2', 'replay', 5)

    def test_stops_after_a_duration_with_nothing(self, reads, capsys):
        assert main(['listen', 'Notice', '--for', '0.2', '--policy', reads]) == 0
        assert capsys.readouterr().out == ''

    def test_a_queryable_is_not_listened_to(self, reads, capsys):
        code, _, err = run(capsys, 'listen', 'LookReply', '--policy', reads)
        assert code == 2
        assert err['message'].endswith('what does: query')

    @pytest.mark.parametrize(('flag', 'value'), [('--for', '0'), ('--for', 'soon'), ('--for', '5d'), ('--count', '0')])
    def test_bad_limits_are_usage_errors(self, reads, capsys, flag, value):
        with pytest.raises(SystemExit) as exc:
            main(['listen', 'Notice', flag, value, '--policy', reads])
        assert exc.value.code == 2

    def test_runs_off_the_main_thread_without_signal_handlers(self, reads, capsys):
        import threading

        codes = []
        thread = threading.Thread(
            target=lambda: codes.append(main(['listen', 'Station', '--count', '1', '--policy', reads]))
        )
        thread.start()
        thread.join(20)
        assert codes == [0]
        assert len(capsys.readouterr().out.splitlines()) == 1

    @pytest.mark.parametrize(('text', 'seconds'), [('30', 30.0), ('30s', 30.0), ('1.5m', 90.0), ('1h', 3600.0)])
    def test_durations(self, text, seconds):
        from parsed.cli import _duration

        assert _duration(text) == seconds

    def test_a_signal_ends_it_cleanly_mid_stream(self, reads, world):
        import _support_protocol as demo

        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
        proc = subprocess.Popen(
            [sys.executable, '-m', 'parsed', 'listen', 'Notice', 'player=ann', '--policy', reads],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            got = b''
            deadline = time.monotonic() + 20
            while b'\n' not in got and time.monotonic() < deadline:
                demo.Notice(world='w1', player='ann', text='hello').send(session=world.session)
                ready, _, _ = select.select([proc.stdout], [], [], 0.05)
                if ready:
                    got += os.read(proc.stdout.fileno(), 4096)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=20)
        finally:
            proc.kill()
        assert proc.returncode == 0, err
        first = (got + out).splitlines()[0]
        assert json.loads(first) == {'world': 'w1', 'player': 'ann', 'text': 'hello'}


class TestOutput:
    def test_select_keeps_parts_of_each_reply(self, reads, capsys):
        code, out, _ = run(capsys, 'fetch', 'Station', '--select', 'station', '--policy', reads)
        assert (code, out) == (0, [{'station': 's1'}, {'station': 's2'}])

    def test_select_on_a_document(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        code, out, _ = run(capsys, 'check', '--select', 'visible.read,hidden', '--policy', str(write(tmp_path, POLICY)))
        assert code == 0
        assert out == {'visible': {'read': ['Notice', 'Seen', 'Station', 'Status', 'Trace']}, 'hidden': 2}

    def test_a_select_that_matches_nothing(self, reads, capsys):
        code, _, err = run(capsys, 'fetch', 'Station', '--select', 'stok', '--policy', reads)
        assert code == 2
        assert err['message'] == "--select 'stok' matches nothing in the result"

    def test_a_malformed_select_is_a_usage_error(self, reads, capsys):
        with pytest.raises(SystemExit) as exc:
            main(['fetch', 'Station', '--select', 'a..b', '--policy', reads])
        assert exc.value.code == 2

    def test_over_the_cap_is_refused_with_its_parts(self, reads, capsys):
        code, out, err = run(capsys, 'fetch', 'Station', '--max-bytes', '50', '--policy', reads)
        assert (code, out) == (6, None)
        assert err['error'] == 'too_large'
        assert err['cap'] == 50
        assert err['parts']['items'] == 2
        assert set(err['parts']['bytes']) == {'world', 'station', 'stock'}

    def test_the_cap_from_the_environment_and_no_cap(self, reads, capsys, monkeypatch):
        monkeypatch.setenv('PARSED_MAX_BYTES', '50')
        assert run(capsys, 'fetch', 'Station', '--policy', reads)[0] == 6
        assert run(capsys, 'fetch', 'Station', '--max-bytes', '0', '--policy', reads)[0] == 0

    def test_out_writes_the_whole_result_uncapped(self, reads, capsys, tmp_path):
        target = tmp_path / 'stations.json'
        code, out, _ = run(capsys, 'fetch', 'Station', '--max-bytes', '50', '--out', str(target), '--policy', reads)
        assert code == 0
        assert out['written'] == str(target.resolve())
        assert len(json.loads(target.read_text())) == 2

    def test_bytes_show_their_length_and_hash(self, reads, capsys):
        code, out, _ = run(capsys, 'fetch', 'Status', '--select', 'logo', '--policy', reads)
        assert code == 0
        assert set(out[0]['logo']) == {'bytes', 'sha256'}


class TestDescribeLayers:
    def test_the_reply_is_a_field_list_unless_asked(self, tmp_path, capsys):
        policy = str(write(tmp_path, POLICY))
        _, out, _ = run(capsys, 'describe', 'Station', '--policy', policy)
        assert out['reply'][2] == {'name': 'stock', 'type': 'int', 'doc': 'units in stock'}
        _, out, _ = run(capsys, 'describe', 'Station', '--reply', '--policy', policy)
        assert out['reply']['title'] == 'Station'

    def test_variant_and_reply_are_one_or_the_other(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            main(['describe', 'LookReply', '--variant', 'ping', '--reply', '--policy', str(write(tmp_path, POLICY))])
        assert exc.value.code == 2


class TestListenOutput:
    def test_select_on_each_line(self, reads, capsys):
        assert main(['listen', 'Station', '--count', '2', '--select', 'station', '--policy', reads]) == 0
        lines_out = sorted(capsys.readouterr().out.splitlines())
        assert lines_out == ['{"station": "s1"}', '{"station": "s2"}']

    def test_select_with_meta_narrows_the_value(self, reads, capsys):
        assert (
            main(['listen', 'Station', 'station=s1', '--count', '1', '--meta', '--select', 'stock', '--policy', reads])
            == 0
        )
        (row,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert row['value'] == {'stock': 3}
        assert row['key'] == 'demo/w1/station/s1'

    def test_a_line_over_the_cap_is_left_out_and_noted(self, reads, capsys):
        assert main(['listen', 'Station', '--for', '0.5', '--max-bytes', '20', '--policy', reads]) == 0
        out, err = capsys.readouterr()
        assert out == ''
        assert lines(err) == [{'note': '2 values over the 20-byte cap were left out; --select narrows them'}]

    def test_a_path_that_never_matched_is_noted(self, reads, capsys):
        assert main(['listen', 'Station', '--count', '2', '--select', 'station,stok', '--policy', reads]) == 0
        out, err = capsys.readouterr()
        assert len(out.splitlines()) == 2
        assert lines(err) == [{'note': "--select 'stok' matched nothing in the stream"}]


def test_the_help_names_every_exit_code(capsys):
    from parsed.cli import EPILOG
    from parsed.errors import ParsedError

    codes = {cls.exit_code for cls in ParsedError.__subclasses__()}
    assert codes == {2, 3, 4, 5, 6}
    for code in (0, *codes):
        assert f'\n  {code}  ' in EPILOG


class TestCheckWithoutResolving:
    def test_checks_the_policy_without_its_secrets(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv('DEMO_TOKEN', raising=False)
        code, out, err = run(capsys, 'check', '--no-resolve', '--policy', str(write(tmp_path, POLICY)))
        assert (code, err) == (0, None)
        assert out['inject'] == {'token': {'source': 'env DEMO_TOKEN', 'resolved': False}}
        assert out['visible']['query'] == ['LookReply']

    def test_a_file_that_is_not_there_is_not_read(self, tmp_path, capsys):
        body = POLICY.replace('env = "DEMO_TOKEN"', 'toml = "no-such-identity.toml"\nkey = "token"')
        code, out, _ = run(capsys, 'check', '--no-resolve', '--policy', str(write(tmp_path, body)))
        assert code == 0
        assert out['inject']['token'] == {'source': 'toml no-such-identity.toml, key token', 'resolved': False}

    def test_everything_else_is_still_checked(self, tmp_path, capsys):
        body = POLICY.replace('[inject.token]', '[inject.tokn]')
        code, _, err = run(capsys, 'check', '--no-resolve', '--policy', str(write(tmp_path, body)))
        assert code == 2
        assert err == {
            'error': 'policy',
            'message': "injects 'tokn', which no request declares",
            'path': ['inject', 'tokn'],
        }

    def test_with_connect(self, tmp_path, capsys, listening, monkeypatch):
        monkeypatch.delenv('DEMO_TOKEN', raising=False)
        head = HEAD.replace('tcp/127.0.0.1:7447', listening)
        policy = str(write(tmp_path, POLICY, head=head))
        code, out, _ = run(capsys, 'check', '--no-resolve', '--connect', '--policy', policy)
        assert code == 0
        assert out['connect']['ok'] is True
