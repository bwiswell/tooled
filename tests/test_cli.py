import io
import json
import os
import subprocess
import sys

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
