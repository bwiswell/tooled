import io
import json
import os
import re
import select
import signal
import subprocess
import sys
import time

import parsed
import pytest
import seared
import zeared
from _support_policy import HEAD, write
from _support_session import free_port

import tooled
from tooled.cli import main, version
from tooled.errors import TooledError

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
    def test_names_tooled_seared_zeared_and_parsed(self):
        on = f'seared {seared.__version__}, zeared {zeared.__version__}, parsed {parsed.__version__}'
        assert version() == f'tooled {tooled.__version__} ({on})'

    def test_prints_and_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(['--version'])
        assert exc.value.code == 0
        assert capsys.readouterr().out == version() + '\n'

    def test_python_dash_m_runs_the_same_command(self):
        done = subprocess.run(
            [sys.executable, '-m', 'tooled', '--version'], capture_output=True, text=True, check=False, timeout=60
        )
        assert done.returncode == 0
        assert done.stdout == version() + '\n'


class TestUsage:
    """What argparse refuses is tooled's usage error: one JSON line on stderr, exit 2, no usage text."""

    @pytest.mark.parametrize(
        ('argv', 'message'),
        [
            ([], 'tooled: the following arguments are required: verb'),
            (['check', '--nope'], 'tooled: unrecognized arguments: --nope'),
            (['fetch'], 'tooled fetch: the following arguments are required: contract'),
            (['fetch', 'Station', '--select', 'a..b'], 'tooled fetch: argument --select: --select takes dotted paths'),
            (['wait', 'Station', '--until', 'nonsense'], 'tooled wait: argument --until: --until is path=value'),
            (['fetch', 'Station', '--max-bytes', 'x'], 'tooled fetch: argument --max-bytes: a cap is a whole number'),
            (['listen', 'Notice', '--count', 'x'], 'tooled listen: argument --count: a count is a whole number'),
            (['query', 'LookReply', '--timeout', 'x'], 'tooled query: argument --timeout: a timeout is a number'),
            (['query', 'LookReply', '--timeout', 'nan'], 'tooled query: argument --timeout: a timeout is a number'),
            (['listen', 'Notice', '--for', 'inf'], 'tooled listen: argument --for: a duration is seconds'),
            (['nope'], "tooled: argument verb: invalid choice: 'nope'"),
        ],
    )
    def test_is_one_json_line(self, capsys, argv, message):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (2, None)
        assert err['error'] == 'usage'
        assert err['message'].startswith(message)
        assert 'usage:' not in err['message']

    @pytest.mark.parametrize('argv', [['contracts'], ['contracts', '--policy', 'tooled.toml', '--pretty']])
    def test_a_renamed_verb_names_its_new_name(self, capsys, argv):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (2, None)
        assert err['error'] == 'usage'
        assert err['message'] == '`tooled contracts` was renamed `tooled operations` in tooled 0.2.4'

    def test_help_still_prints_and_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(['fetch', '--help'])
        assert exc.value.code == 0
        out, err = capsys.readouterr()
        assert out.startswith('usage: tooled fetch')
        assert err == ''


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
            'operations': 8,
            'visible': {
                'read': ['Notice', 'Seen', 'Station', 'Status', 'Trace'],
                'query': ['LookReply'],
            },
            'hidden': 2,
            'bind': {'world': 'w1'},
            'fixed': {},
            'inject': {'token': {'source': 'env DEMO_TOKEN', 'length': 16}},
        }

    def test_an_app_s_commands_are_counted_and_none_shown_yet(self, tmp_path, capsys):
        path = write(tmp_path, '[app]\nfactory = "_support_app:factory"\n')
        code, out, _ = run(capsys, 'check', '--no-resolve', '--policy', str(path))
        assert code == 0
        assert {key: out[key] for key in ('packages', 'app', 'commands', 'operations', 'hidden')} == {
            'packages': ['_support_protocol'],
            'app': '_support_app:factory',
            'commands': 9,
            'operations': 17,
            'hidden': 17,  # no rule allows a tree op
        }
        assert 'trees' not in out

    def test_tree_rules_show_commands_by_op(self, tmp_path, capsys):
        body = (
            '[app]\nfactory = "_support_app:factory"\n\n'
            '[[allow]]\nops = ["see"]\nkeys = ["station/s1/**", "*/status"]\n\n'
            '[[allow]]\nops = ["do"]\noperations = ["relay/*"]\n'
        )
        code, out, _ = run(capsys, 'check', '--no-resolve', '--policy', str(write(tmp_path, body)))
        assert code == 0
        assert out['visible'] == {
            'see': ['StationMap', 'StationShow', 'gateway/Status', 'relay/Status'],
            'do': ['relay/Run'],
        }
        assert out['fixed'] == {'StationMap': {'station': 's1'}, 'StationShow': {'station': 's1'}}
        assert out['hidden'] == 17 - 5

    def test_trees_with_no_app(self, tmp_path, capsys):
        head = HEAD.replace('packages = ["_support_protocol"]\n', 'trees = ["_support_tree.service"]\n')
        code, out, _ = run(capsys, 'check', '--no-resolve', '--policy', str(write(tmp_path, head=head)))
        assert code == 0
        assert {key: out[key] for key in ('packages', 'trees', 'commands', 'operations')} == {
            'packages': [],
            'trees': ['_support_tree.service'],
            'commands': 2,
            'operations': 2,
        }
        assert 'app' not in out

    def test_an_app_that_will_not_load(self, tmp_path, capsys):
        path = write(tmp_path, '[app]\nfactory = "_support_app:not_an_app"\n')
        code, _, err = run(capsys, 'check', '--no-resolve', '--policy', str(path))
        assert (code, err['error'], err['path']) == (2, 'catalog', ['app', 'factory'])

    def test_notes_each_open_slot_the_allow_rules_fix(self, tmp_path, capsys):
        body = (
            '[[allow]]\nops = ["read"]\noperations = ["Station", "Notice"]\n'
            'keys = ["demo/w1/station/*", "demo/w1/notice/ann"]\n'
        )
        code, out, _ = run(capsys, 'check', '--no-resolve', '--policy', str(write(tmp_path, body)))
        assert code == 0
        assert out['fixed'] == {'Notice': {'world': 'w1', 'player': 'ann'}, 'Station': {'world': 'w1'}}

    def test_never_prints_an_injected_value(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-secret-value')
        main(['check', '--policy', str(write(tmp_path, POLICY)), '--pretty'])
        out, err = capsys.readouterr()
        assert 'tok-secret-value' not in out + err
        assert out.startswith('{\n  "policy"')

    def test_finds_the_policy_in_the_environment(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv('TOOLED_POLICY', str(write(tmp_path)))
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


class TestOperations:
    def test_lists_what_the_policy_shows(self, tmp_path, capsys):
        code, out, err = run(capsys, 'operations', '--policy', str(write(tmp_path, POLICY)))
        assert (code, err) == (0, None)
        assert [row['name'] for row in out] == ['LookReply', 'Notice', 'Seen', 'Station', 'Status', 'Trace']
        assert out[0]['verbs'] == ['query']
        assert out[0]['topic'] == 'demo/w1/look/{player}'

    def test_an_empty_policy_lists_nothing(self, tmp_path, capsys):
        assert run(capsys, 'operations', '--policy', str(write(tmp_path))) == (0, [], None)


class TestTools:
    def test_prints_each_tool_in_the_form_asked(self, tmp_path, capsys):
        code, out, err = run(capsys, 'tools', '--format', 'anthropic', '--policy', str(write(tmp_path, POLICY)))
        assert code == 0
        assert [tool['name'] for tool in out] == [
            'fetch_station',
            'fetch_status',
            'listen_notice',
            'listen_trace',
            'query_look_reply_ping',
            'query_look_reply_pong',
            'wait_notice',
            'wait_station',
            'wait_status',
            'wait_trace',
        ]
        assert set(out[0]) == {'name', 'description', 'input_schema'}
        assert err == {'note': 'no tool for a presence feed, which no verb acts on yet: Seen'}

    def test_openai_by_default(self, tmp_path, capsys):
        _, out, _ = run(capsys, 'tools', '--policy', str(write(tmp_path, POLICY)))
        assert out[0]['type'] == 'function'
        assert out[0]['function']['name'] == 'fetch_station'

    def test_verbs_keep_some_tools(self, tmp_path, capsys):
        policy = str(write(tmp_path, POLICY))
        _, out, _ = run(capsys, 'tools', '--verbs', 'fetch,wait:Station', '--format', 'mcp', '--policy', policy)
        assert [tool['name'] for tool in out] == ['fetch_station', 'fetch_status', 'wait_station']

    def test_sizes(self, tmp_path, capsys):
        argv = [
            'tools',
            '--sizes',
            '--format',
            'mcp',
            '--verbs',
            'fetch,query',
            '--policy',
            str(write(tmp_path, POLICY)),
        ]
        code, out, _ = run(capsys, *argv)
        assert code == 0
        assert out['format'] == 'mcp'
        assert [row['name'] for row in out['tools']] == [
            'fetch_station',
            'fetch_status',
            'query_look_reply_ping',
            'query_look_reply_pong',
        ]
        assert out['total']['tools'] == 4

    @pytest.mark.parametrize(
        ('flag', 'given', 'message'),
        [
            ('--format', 'xml', "tooled tools: argument --format: invalid choice: 'xml'"),
            ('--verbs', 'fetch,publish', 'tooled tools: argument --verbs: each is a verb, or verb:GLOB, of fetch'),
            ('--verbs', 'wait:Statoin', "no operation named 'Statoin'"),
        ],
    )
    def test_a_bad_option_is_a_usage_error(self, tmp_path, capsys, flag, given, message):
        code, _, err = run(capsys, 'tools', flag, given, '--policy', str(write(tmp_path, POLICY)))
        assert (code, err['error']) == (2, 'usage')
        assert err['message'].startswith(message)


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
        assert err == {'error': 'usage', 'message': "no operation named 'Stations'"}

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

    def test_a_presence_feed_has_no_verb_yet(self, reads, capsys):
        code, _, err = run(capsys, 'fetch', 'Seen', '--policy', reads)
        assert code == 2
        assert err['message'].endswith('what does: nothing in this version of tooled')

    def test_a_hidden_contract_is_refused(self, reads, capsys):
        code, _, err = run(capsys, 'fetch', 'MoveResult', '--policy', reads)
        assert (code, err['error']) == (3, 'refused')

    def test_a_refusal_names_the_open_slot_to_fill(self, tmp_path, world, capsys):
        head = HEAD.replace('tcp/127.0.0.1:7447', world.endpoint)
        policy = str(write(tmp_path, '[[allow]]\nops = ["read"]\nkeys = ["demo/w1/**"]\n', head=head))
        code, _, err = run(capsys, 'fetch', 'Station', '--policy', policy)
        assert (code, err['error']) == (3, 'refused')
        assert err['message'].endswith(": slot 'world' is open; give world=w1, or bind it in [bind]")

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

    @pytest.mark.parametrize(('params', 'message'), [(['noequals'], 'name=value'), (['a=1', 'a=2'], 'twice')])
    def test_a_malformed_param_is_refused_before_the_session_opens(self, tmp_path, capsys, params, message):
        head = HEAD.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}')  # nothing listens: it can't open
        policy = str(write(tmp_path, READS, head=head))
        flags = [arg for param in params for arg in ('--param', param)]
        code, _, err = run(capsys, 'query', 'LookReply', 'player=ann', *flags, '--policy', policy)
        assert (code, err['error']) == (2, 'usage')
        assert message in err['message']

    @pytest.mark.parametrize(
        ('request_text', 'code', 'kind'),
        [
            ('{"kind": "ping"', 2, 'usage'),
            ('[1, 2]', 2, 'usage'),
            ('@/no/such/file.json', 2, 'usage'),
            ('{"kind": "ping"}', 4, 'validation'),
            ('{"kind": "dev-peek"}', 3, 'refused'),
            ('{"kind": "dev-peek", "depth": "deep"}', 3, 'refused'),  # refused before its fields are read
            ('{"kind": "poke"}', 4, 'validation'),
            ('{"kind": ["ping"]}', 4, 'validation'),
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
        code, _, err = run(capsys, 'query', 'LookReply', '--timeout', '0', '--policy', reads)
        assert code == 2
        assert 'above zero' in err['message']


WRITES = (
    READS
    + """
[inject.move_id]
generate = "uuid"

[[allow]]
ops = ["invoke"]
keys = ["demo/w1/**"]

[[deny]]
ops = ["invoke"]
keys = ["demo/w1/move/bo"]
"""
)


@pytest.fixture
def writes(tmp_path, world, monkeypatch):
    """A policy that also writes, over the served fixture world, with its token set and a generated move id."""
    monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
    return str(write(tmp_path, WRITES, head=HEAD.replace('tcp/127.0.0.1:7447', world.endpoint)))


class TestChecksBeforeTheSession:
    """What needs no mesh is checked before the session opens: against a router nothing listens on, a refusal is
    still a refusal (3), an invalid request still invalid (4), and a write that reaches more than one key still a
    usage error (2), never the mesh's 5."""

    @pytest.fixture
    def dead(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        head = HEAD.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}')  # nothing listens: it can't open
        return str(write(tmp_path, WRITES, head=head))

    @pytest.mark.parametrize(
        ('argv', 'code', 'kind'),
        [
            (['query', 'LookReply', 'player=ann', '--request', '{"kind": "dev-peek"}'], 3, 'refused'),
            (['query', 'LookReply', 'player=ann', '--request', '{"kind": "ping"}'], 4, 'validation'),
            (['invoke', 'MoveResult', 'player=bo', '--request', '{"to": "dock"}'], 3, 'refused'),
            (['invoke', 'MoveResult', 'player=ann', '--request', '{}'], 4, 'validation'),
            (['invoke', 'MoveResult', '--request', '{"to": "dock"}'], 2, 'usage'),
        ],
    )
    def test_told_as_itself_with_the_mesh_down(self, dead, capsys, argv, code, kind):
        got, out, err = run(capsys, *argv, '--policy', dead)
        assert (got, out, err['error']) == (code, None, kind)

    def test_a_hidden_field_refused(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        head = HEAD.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}')
        policy = str(write(tmp_path, READS + '\n[[deny]]\nops = ["query"]\nfields = ["ask_id"]\n', head=head))
        request = '{"kind": "ping", "station": "s1", "ask_id": "a1"}'
        code, out, err = run(capsys, 'query', 'LookReply', 'player=ann', '--request', request, '--policy', policy)
        assert (code, out, err['error']) == (3, None, 'refused')
        assert err['message'] == "'ask_id' is hidden by the policy (deny[1]); leave it out of the request"

    def test_a_fetch_refused(self, tmp_path, capsys):
        head = HEAD.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}')
        policy = str(write(tmp_path, '[[allow]]\nops = ["read"]\nkeys = ["demo/w1/**"]\n', head=head))
        code, out, err = run(capsys, 'fetch', 'Station', '--policy', policy)  # no world bound: it reaches w2's too
        assert (code, out, err['error']) == (3, None, 'refused')

    def test_a_call_that_passes_them_still_needs_the_mesh(self, dead, capsys):
        code, _, err = run(capsys, 'fetch', 'Station', '--policy', dead)
        assert (code, err['error']) == (5, 'mesh')


class TestInvoke:
    def test_a_write_and_its_reply(self, writes, world, capsys):
        code, out, err = run(
            capsys, 'invoke', 'MoveResult', 'player=ann', '--request', '{"to": "dock"}', '--policy', writes
        )
        assert (code, err) == (0, None)
        assert out == [{'world': 'w1', 'player': 'ann', 'ok': True}]
        (sent,) = world.asked
        assert (sent.token, sent.to) == ('tok-ann', 'dock')
        assert re.fullmatch('[0-9a-f]{32}', sent.move_id)

    def test_a_refusal_by_the_server_exits_zero(self, writes, capsys):
        argv = ['invoke', 'MoveResult', 'player=ann', '--request', '{"to": "nowhere"}', '--policy', writes]
        refused = {'world': 'w1', 'player': 'ann', 'ok': False, 'reason': 'there is nowhere to go'}
        assert run(capsys, *argv)[:2] == (0, [refused])

    def test_a_queryable_that_declares_no_effect(self, writes, capsys):
        code, out, _ = run(capsys, 'invoke', 'EchoReply', '--request', '{"text": "hi"}', '--policy', writes)
        assert (code, out) == (0, [{'world': 'w1', 'text': 'hi'}])

    def test_a_dry_run_prints_the_request_and_sends_nothing(self, writes, world, capsys):
        argv = ['invoke', 'MoveResult', 'player=ann', '--request', '{"to": "dock"}', '--dry-run', '--policy', writes]
        assert main(argv) == 0
        out, err = capsys.readouterr()
        assert 'tok-ann' not in out
        preview = json.loads(out)
        move_id = preview['request'].pop('move_id')
        assert re.fullmatch('[0-9a-f]{32}', move_id)  # a generated value is no secret
        assert preview == {
            'selector': 'demo/w1/move/ann',
            'variant': None,
            'request': {'token': '<injected>', 'to': 'dock'},
        }
        assert lines(err) == [{'note': 'a dry run: checked, and not sent'}]
        assert world.asked == []

    @pytest.mark.parametrize(
        ('argv', 'expected'),
        [
            (['MoveResult', '--request', '{"to": "dock"}'], (2, 'usage')),  # no player: it could reach every one
            (['MoveResult', 'player=bo', '--request', '{"to": "dock"}'], (3, 'refused')),
            (['MoveResult', 'player=ann', '--request', '{"to": "dock", "token": "mine"}'], (3, 'refused')),
            (['MoveResult', 'player=ann', '--request', '{"to": "dock", "move_id": "mine"}'], (3, 'refused')),
            (['MoveResult', 'player=ann', '--request', '{}'], (4, 'validation')),
            (['LookReply', 'player=ann', '--request', '{"kind": "pong"}'], (2, 'usage')),  # a read: query's
        ],
    )
    def test_writes_that_go_nowhere(self, writes, world, capsys, argv, expected):
        for dry_run in ([], ['--dry-run']):  # a dry run is checked as a write is
            code, out, err = run(capsys, 'invoke', *argv, *dry_run, '--policy', writes)
            assert (code, out, err['error']) == (expected[0], None, expected[1])
        assert world.asked == []

    def test_a_write_reaches_one_key(self, writes, capsys):
        _, _, err = run(capsys, 'invoke', 'MoveResult', '--request', '{"to": "dock"}', '--policy', writes)
        assert err['message'] == 'a write reaches one key, and demo/w1/move/* could reach more: give player'

    def test_a_read_policy_does_not_show_a_write(self, reads, capsys):
        code, _, err = run(
            capsys, 'invoke', 'MoveResult', 'player=ann', '--request', '{"to": "dock"}', '--policy', reads
        )
        assert (code, err['message']) == (3, 'the policy does not show MoveResult')

    def test_query_does_not_act_on_a_write(self, writes, capsys):
        code, _, err = run(capsys, 'query', 'MoveResult', 'player=ann', '--policy', writes)
        assert code == 2
        assert err['message'] == 'query does not act on MoveResult, a queryable contract; what does: invoke'

    def test_contracts_and_describe_list_it(self, writes, capsys):
        rows = {row['name']: row for row in run(capsys, 'operations', '--policy', writes)[1]}
        assert (rows['MoveResult']['verbs'], rows['EchoReply']['verbs']) == (['invoke'], ['invoke'])
        _, out, _ = run(capsys, 'describe', 'MoveResult', '--policy', writes)
        assert out['verbs'] == ['invoke']
        assert out['injected'] == ['token', 'move_id']
        assert list(out['request']['properties']) == ['to']

    def test_check_shows_the_writes(self, writes, capsys):
        _, out, _ = run(capsys, 'check', '--policy', writes)
        assert out['visible']['invoke'] == ['EchoReply', 'MoveResult']


class TestRefusedPaths:
    """A path that names no field is refused before the call, with what's near where it left the schema."""

    @pytest.mark.parametrize(
        ('given', 'hint'),
        [
            ('stok', "did you mean 'stock'?"),
            ('zzzz', 'the fields there: world, station, stock, closed'),
            ('station.x', "'station' has no fields"),
            ('stock[]', "'stock' is no list"),
        ],
    )
    def test_the_hint(self, reads, capsys, given, hint):
        code, _, err = run(capsys, 'fetch', 'Station', '--select', given, '--policy', reads)
        assert (code, err['message']) == (2, f'--select {given!r} names no field of Station; {hint}')

    def test_a_path_the_schema_cannot_judge_is_held_to_matching(self, reads, capsys, monkeypatch):
        import seared.doc

        monkeypatch.setattr(seared.doc, 'json_schema', lambda _cls, mode: {'properties': {'station': {}}})  # Any
        code, _, err = run(capsys, 'fetch', 'Station', '--select', 'station.zzz', '--policy', reads)
        assert (code, err['message']) == (2, "--select 'station.zzz' matches nothing in the result")

    @pytest.mark.parametrize(
        ('flag', 'parts', 'found', 'hint'),
        [
            ('--select', ['cargo', 'value'], {'at': 1, 'listed': True}, "'cargo' is a list: cargo[].value"),
            ('--until', ['cargo', 'value'], {'at': 1, 'listed': True}, "'cargo' is a list: cargo.0.value"),
            ('--select', ['fuel', 'zzz'], {'at': 1, 'names': ('value', 'rate')}, "the fields of 'fuel': value, rate"),
            (
                '--select',
                ['zzz'],
                {'at': 0, 'names': tuple('abcdefghijklm')},
                'the fields there: a, b, c, d, e, f, g, h, i, j, k, l, …',
            ),
        ],
    )
    def test_hints_on_other_shapes(self, flag, parts, found, hint):
        from tooled.cli import _refuse
        from tooled.schemas import Walk

        with pytest.raises(TooledError) as exc:
            _refuse(flag, parts, 'Ship', Walk(declared=False, **found))
        assert exc.value.message.endswith(f'; {hint}')
        assert exc.value.path == tuple(parts[: found['at'] + 1])


class TestWriteOutput:
    """Once a write is sent, nothing about the output may read as the write not having happened."""

    ARGV = ('invoke', 'MoveResult', 'player=ann', '--request', '{"to": "dock"}')

    def test_a_declared_field_absent_is_quiet(self, writes, world, capsys):
        code, out, err = run(capsys, *self.ARGV, '--select', 'ok,reason', '--policy', writes)
        assert (code, out, err) == (0, [{'ok': True}], None)  # an accepted move has no reason
        assert len(world.asked) == 1

    def test_a_path_that_names_no_field_is_refused_before_sending(self, writes, world, capsys):
        code, _, err = run(capsys, *self.ARGV, '--select', 'ok,reasn', '--policy', writes)
        assert (code, err['message']) == (2, "--select 'reasn' names no field of MoveResult; did you mean 'reason'?")
        assert world.asked == []

    def test_a_reply_over_the_cap_says_the_write_was_sent(self, writes, capsys):
        code, out, err = run(capsys, *self.ARGV, '--max-bytes', '10', '--policy', writes)
        assert (code, out, err['error']) == (6, None, 'too_large')
        assert err['message'].startswith('the write was sent and answered, but the result is')

    def test_a_dry_run_checks_select_against_the_reply_and_prints_the_preview_whole(self, writes, capsys):
        code, _, err = run(capsys, *self.ARGV, '--dry-run', '--select', 'nope', '--policy', writes)
        assert code == 2
        assert err['message'].startswith("--select 'nope' names no field of MoveResult")
        code, out, _ = run(capsys, *self.ARGV, '--dry-run', '--select', 'ok,reason', '--policy', writes)
        assert code == 0
        assert set(out) == {'selector', 'variant', 'request'}


class TestRetry:
    def invoke(self, capsys, policy, to, *extra):
        argv = ['invoke', 'MoveResult', 'player=ann', '--request', json.dumps({'to': to}), *extra, '--policy', policy]
        return run(capsys, *argv)

    def test_a_lost_reply_is_asked_once_more(self, writes, world, capsys):
        code, out, err = self.invoke(capsys, writes, 'lossy')
        assert (code, out) == (0, [{'world': 'w1', 'player': 'ann', 'ok': True}])
        assert err == {'note': 'no reply within 5s; asked once more with the same move_id'}
        first, second = world.asked
        assert first.move_id == second.move_id

    def test_a_lost_write_exits_five_with_its_id(self, writes, world, capsys):
        code, out, err = self.invoke(capsys, writes, 'void')
        assert (code, out) == (5, None)
        assert err == {
            'error': 'mesh',
            'message': 'no reply to MoveResult within 5s, after one retry: it may or may not have been carried out',
            'generated': {'move_id': world.asked[0].move_id},
        }
        assert len(world.asked) == 2

    def test_no_retry(self, writes, world, capsys):
        code, _, err = self.invoke(capsys, writes, 'void', '--no-retry')
        assert (code, err['message']) == (
            5,
            'no reply to MoveResult within 5s: it may or may not have been carried out',
        )
        assert len(world.asked) == 1

    def test_an_error_reply_exits_zero_with_a_note(self, writes, world, capsys):
        code, out, err = self.invoke(capsys, writes, 'broken')
        assert (code, out, err) == (0, [], {'note': 'MoveResult answered with an error: the dock is closed'})
        assert len(world.asked) == 1


class TestEndToEnd:
    def test_python_dash_m_fetches_over_tcp(self, reads):
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
        done = subprocess.run(
            [sys.executable, '-m', 'tooled', 'fetch', 'Station', 'station=s1', '--policy', reads],
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
        code, _, err = run(capsys, 'listen', 'Notice', flag, value, '--policy', reads)
        assert (code, err['error']) == (2, 'usage')

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
        from tooled.cli import _duration

        assert _duration(text) == seconds

    def test_a_signal_ends_it_cleanly_mid_stream(self, reads, world):
        import _support_protocol as demo

        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
        proc = subprocess.Popen(
            [sys.executable, '-m', 'tooled', 'listen', 'Notice', 'player=ann', '--policy', reads],
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


class TestWait:
    def test_a_retained_value_already_so(self, reads, capsys):
        code, out, err = run(capsys, 'wait', 'Station', 'station=s2', '--until', 'stock=5', '--policy', reads)
        assert (code, out, err) == (0, {'world': 'w1', 'station': 's2', 'stock': 5}, None)

    def test_every_until_must_hold(self, reads, capsys):
        argv = ['wait', 'Station', '--until', 'station=s1', '--until', 'stock=3', '--meta', '--policy', reads]
        code, out, _ = run(capsys, *argv)
        assert code == 0
        assert (out['key'], out['origin'], out['value']['stock']) == ('demo/w1/station/s1', 'replay', 3)

    def test_select_keeps_parts_and_an_absent_field_is_quiet(self, reads, capsys):
        argv = ['wait', 'Station', 'station=s2', '--until', 'stock=5', '--select', 'stock,closed', '--policy', reads]
        code, out, err = run(capsys, *argv)
        assert (code, out, err) == (0, {'stock': 5}, None)

    @pytest.mark.parametrize(
        ('flag', 'given', 'message'),
        [
            ('--select', 'stok', "--select 'stok' names no field of Station; did you mean 'stock'?"),
            ('--until', 'stok=5', "--until 'stok' names no field of Station; did you mean 'stock'?"),
        ],
    )
    def test_a_path_that_names_no_field_is_refused_at_once(self, reads, capsys, flag, given, message):
        argv = ['wait', 'Station', '--until', 'stock=5', flag, given, '--timeout', '30', '--policy', reads]
        started = time.monotonic()
        code, _, err = run(capsys, *argv)
        assert (code, err['message']) == (2, message)
        assert time.monotonic() - started < 5  # not after the timeout

    def test_the_timeout_exits_seven_with_the_last_value(self, reads, capsys):
        argv = ['wait', 'Station', '--until', 'stock=99', '--timeout', '0.3', '--select', 'station']
        code, out, err = run(capsys, *argv, '--policy', reads)
        assert (code, out) == (7, None)
        assert (err['error'], err['message']) == ('timeout', 'no Station value met stock=99 within 0.3s: 2 seen')
        assert err['last'] in ({'station': 's1'}, {'station': 's2'})

    def test_a_last_value_over_the_cap_is_left_out(self, reads, capsys):
        argv = ['wait', 'Station', '--until', 'stock=99', '--timeout', '0.3', '--max-bytes', '10']
        _, _, err = run(capsys, *argv, '--policy', reads)
        assert err['last'] is None
        tail = (
            r'2 seen; the last value seen is \d+ bytes, over the 10-byte cap, so it is left out: --select keeps parts'
        )
        assert re.search(tail + ' of it$', err['message'])

    def test_a_queryable_is_not_waited_on(self, reads, capsys):
        code, _, err = run(capsys, 'wait', 'LookReply', '--until', 'seen=x', '--policy', reads)
        assert (code, err['message']) == (2, 'wait does not act on LookReply, a queryable contract; what does: query')

    @pytest.mark.parametrize(
        ('argv', 'message'),
        [
            (['Station'], 'the following arguments are required: --until'),
            (['Station', '--until', 'stock'], '--until is path=value'),
        ],
    )
    def test_usage(self, reads, capsys, argv, message):
        code, _, err = run(capsys, 'wait', *argv, '--policy', reads)
        assert (code, err['error']) == (2, 'usage')
        assert message in err['message']

    def test_the_default_timeout_is_thirty_seconds(self):
        from tooled.cli import build_parser

        assert build_parser().parse_args(['wait', 'Station', '--until', 'stock=1']).timeout == 30.0

    def test_sigterm_ends_it_as_not_met(self, reads, world):
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
        argv = ['wait', 'Notice', 'player=ann', '--until', 'text=never', '--timeout', '60', '--policy', reads]
        publisher = world.session.declare_publisher('demo/w1/notice/ann')
        proc = subprocess.Popen(
            [sys.executable, '-m', 'tooled', *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
        )
        try:
            deadline = time.monotonic() + 20
            while not publisher.matching_status.matching and time.monotonic() < deadline:  # subscribed: handlers set
                time.sleep(0.05)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=20)
        finally:
            proc.kill()
            publisher.undeclare()
        assert (proc.returncode, out) == (7, b''), err
        error = json.loads(err.splitlines()[-1])
        assert (error['message'], error['last']) == ('stopped before any Notice value met text=never: 0 seen', None)


class TestCall:
    """A tool call, carried out as its verb is: its output the verb's, and its errors too."""

    def call(self, capsys, policy, tool, given=None):
        argv = ['call', tool, '--policy', policy] + (['--args', json.dumps(given)] if given is not None else [])
        return run(capsys, *argv)

    def test_a_fetch(self, reads, capsys):
        code, out, err = self.call(capsys, reads, 'fetch_station', {'station': 's2'})
        assert (code, out, err) == (0, [{'world': 'w1', 'station': 's2', 'stock': 5}], None)

    def test_a_query_with_its_variant_s_tag(self, reads, capsys):
        code, out, _ = self.call(capsys, reads, 'query_look_reply_ping', {'player': 'ann', 'station': 's1'})
        assert code == 0
        assert out[0]['seen'] == "ping {'station': 's1'} at [('player', 'ann'), ('world', 'w1')] {}"

    def test_a_write(self, writes, world, capsys):
        code, out, _ = self.call(capsys, writes, 'invoke_move_result', {'player': 'ann', 'to': 'dock'})
        assert (code, out) == (0, [{'world': 'w1', 'player': 'ann', 'ok': True}])
        assert len(world.asked) == 1

    def test_a_wait(self, reads, capsys):
        code, out, _ = self.call(capsys, reads, 'wait_station', {'until': {'stock': 5}})
        assert (code, out) == (0, {'world': 'w1', 'station': 's2', 'stock': 5})

    def test_a_wait_past_its_timeout(self, reads, capsys):
        code, out, err = self.call(capsys, reads, 'wait_station', {'until': {'stock': 99}, 'timeout': 0.3})
        assert (code, out, err['error']) == (7, None, 'timeout')
        assert err['message'] == 'no Station value met stock=99 within 0.3s: 2 seen'

    def test_a_listen_gives_its_values_as_one_list(self, reads, world, capsys):
        import threading

        import _support_protocol as demo

        done = threading.Event()

        def publish():
            while not done.wait(0.05):
                demo.Notice(world='w1', player='ann', text='hello').send(session=world.session)

        publisher = threading.Thread(target=publish)
        publisher.start()
        try:
            code, out, err = self.call(capsys, reads, 'listen_notice', {'player': 'ann', 'count': 2, 'timeout': 20})
        finally:
            done.set()
            publisher.join()
        assert (code, err) == (0, None)
        assert out == [{'world': 'w1', 'player': 'ann', 'text': 'hello'}] * 2

    def test_a_listen_short_of_its_count(self, reads, capsys):
        code, out, err = self.call(capsys, reads, 'listen_notice', {'timeout': 0.2})
        assert (code, out) == (0, [])
        assert err == {'note': '0 of the 1 values asked for came within 0.2s'}

    @pytest.mark.parametrize(
        ('tool', 'given', 'expected'),
        [
            ('fetch_stations', {}, (2, 'usage', None)),
            ('fetch_station', {'stock': 3}, (4, 'validation', ['stock'])),
            ('wait_station', {'until': {'stock': 'five'}}, (4, 'validation', ['until', 'stock'])),
            ('query_look_reply_ping', {'player': 'ann'}, (4, 'validation', ['station'])),  # the tool requires it
            ('query_look_reply_pong', {'times': 'two'}, (4, 'validation', ['times'])),  # seared's, from the request
            ('query_look_reply_ping', {'station': 's1', 'token': 't'}, (3, 'refused', ['token'])),  # injected
            ('fetch_station', {'world': 'w2'}, (3, 'refused', ['world'])),  # bound
        ],
    )
    def test_errors_are_the_verb_s_and_the_tool_s(self, reads, capsys, tool, given, expected):
        code, out, err = self.call(capsys, reads, tool, given)
        assert (code, err['error'], err.get('path')) == expected
        assert out is None

    def test_a_hidden_field(self, tmp_path, world, monkeypatch, capsys):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        body = POLICY + '\n[[deny]]\nops = ["query"]\nfields = ["ask_id"]\n'
        policy = str(write(tmp_path, body, head=HEAD.replace('tcp/127.0.0.1:7447', world.endpoint)))
        given = {'player': 'ann', 'station': 's1', 'ask_id': 'a1'}
        code, _, err = self.call(capsys, policy, 'query_look_reply_ping', given)
        assert (code, err['message']) == (3, "'ask_id' is hidden by the policy (deny[1]); leave it out of the request")

    @pytest.mark.parametrize(('text', 'message'), [('{', '--args is not JSON'), ('[]', '--args is a JSON object')])
    def test_arguments_that_are_no_object(self, reads, capsys, text, message):
        code, _, err = run(capsys, 'call', 'fetch_station', '--args', text, '--policy', reads)
        assert (code, err['error']) == (2, 'usage')
        assert err['message'].startswith(message)

    def test_a_result_over_the_cap(self, reads, capsys):
        code, _, err = run(capsys, 'call', 'fetch_station', '--max-bytes', '10', '--policy', reads)
        assert (code, err['error']) == (6, 'too_large')

    def test_the_mesh_down(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        policy = str(write(tmp_path, POLICY, head=HEAD.replace('tcp/127.0.0.1:7447', f'tcp/127.0.0.1:{free_port()}')))
        code, _, err = self.call(capsys, policy, 'fetch_station', {'station': 's1'})
        assert (code, err['error']) == (5, 'mesh')

    def test_no_arguments_are_none(self, reads, capsys):
        code, out, _ = run(capsys, 'call', 'fetch_station', '--policy', reads)
        assert (code, len(out)) == (0, 2)


class TestCallLog:
    """With TOOLED_LOG set, each run appends one line: what it did, by name, and how it ended; never a value."""

    @pytest.fixture
    def log(self, tmp_path, monkeypatch):
        path = tmp_path / 'calls.jsonl'
        monkeypatch.setenv('TOOLED_LOG', str(path))
        return path

    @staticmethod
    def lines(log):
        return [json.loads(line) for line in log.read_text().splitlines()]

    @staticmethod
    def part(line, *keys):
        return {key: line[key] for key in keys}

    def test_a_fetch(self, reads, log, capsys):
        assert main(['fetch', 'Station', 'station=s2', '--policy', reads]) == 0
        out = capsys.readouterr().out
        (line,) = self.lines(log)
        assert {key: value for key, value in line.items() if key not in {'at', 'seconds'}} == {
            'verb': 'fetch',
            'route': 'wire',
            'operation': 'Station',
            'tool': None,
            'variant': None,
            'arguments': ['station'],
            'flags': ['--policy'],
            'exit': 0,
            'error': None,
            'path': None,
            'bytes': len(out.encode()),
        }
        assert 's2' not in log.read_text()  # a slot's value is never kept

    def test_a_call(self, reads, log, capsys):
        given = json.dumps({'player': 'ann', 'station': 's1'})
        assert main(['call', 'query_look_reply_ping', '--args', given, '--policy', reads]) == 0
        (line,) = self.lines(log)
        assert self.part(line, 'verb', 'operation', 'tool', 'variant', 'arguments', 'flags', 'exit') == {
            'verb': 'call',
            'operation': 'LookReply',
            'tool': 'query_look_reply_ping',
            'variant': 'ping',
            'arguments': ['player', 'station'],
            'flags': ['--args', '--policy'],
            'exit': 0,
        }
        assert 'ann' not in log.read_text()

    def test_a_query_s_variant_is_read_from_its_request(self, reads, log, capsys):
        main(['query', 'LookReply', 'player=ann', '--request', '{"kind": "pong"}', '--policy', reads])
        assert self.lines(log)[0]['variant'] == 'pong'

    def test_a_refused_call_with_its_kind_and_path(self, reads, log, capsys):
        assert main(['call', 'wait_station', '--args', '{"until": {"stok": 5}}', '--policy', reads]) == 4
        (line,) = self.lines(log)
        assert self.part(line, 'operation', 'tool', 'exit', 'error', 'path', 'bytes') == {
            'operation': 'Station',
            'tool': 'wait_station',
            'exit': 4,
            'error': 'validation',
            'path': ['until', 'stok'],
            'bytes': 0,
        }

    def test_a_tool_none_has_is_named_as_asked(self, reads, log, capsys):
        assert main(['call', 'fetch_stations', '--policy', reads]) == 2
        line = self.lines(log)[0]
        assert self.part(line, 'tool', 'operation', 'error') == {
            'tool': 'fetch_stations',
            'operation': None,
            'error': 'usage',
        }

    def test_a_wait_past_its_timeout_keeps_no_last_value(self, reads, log, capsys):
        main(['wait', 'Station', '--until', 'stock=99', '--timeout', '0.2', '--policy', reads])
        (line,) = self.lines(log)
        assert self.part(line, 'exit', 'error') == {'exit': 7, 'error': 'timeout'}
        assert line['seconds'] >= 0.2
        assert 'last' not in line

    def test_a_listen_counts_every_line(self, reads, log, capsys):
        assert main(['listen', 'Station', '--count', '2', '--policy', reads]) == 0
        assert self.lines(log)[0]['bytes'] == len(capsys.readouterr().out.encode())

    def test_a_verb_tooled_does_not_have(self, log, capsys):
        assert main(['fetchh', 'Station']) == 2
        assert self.part(self.lines(log)[0], 'verb', 'exit', 'error') == {'verb': None, 'exit': 2, 'error': 'usage'}

    def test_runs_share_the_file(self, reads, log, capsys):
        main(['operations', '--policy', reads])
        main(['describe', 'Station', '--policy', reads])
        assert [(line['verb'], line['operation']) for line in self.lines(log)] == [
            ('operations', None),
            ('describe', 'Station'),
        ]

    def test_a_log_it_can_not_write_leaves_the_run_alone(self, reads, tmp_path, monkeypatch, capsys):
        target = tmp_path / 'missing' / 'calls.jsonl'
        monkeypatch.setenv('TOOLED_LOG', str(target))
        assert main(['fetch', 'Station', '--policy', reads]) == 0
        assert lines(capsys.readouterr().err) == [
            {'note': f'could not write the call log to {target}: No such file or directory'}
        ]

    def test_its_verbs_are_the_parser_s(self):
        import argparse

        from tooled.cli import VERBS, build_parser

        (verbs,) = [action for action in build_parser()._actions if isinstance(action, argparse._SubParsersAction)]
        assert list(verbs.choices) == list(VERBS)


class TestOutput:
    def test_select_keeps_parts_of_each_reply(self, reads, capsys):
        code, out, _ = run(capsys, 'fetch', 'Station', '--select', 'station', '--policy', reads)
        assert (code, out) == (0, [{'station': 's1'}, {'station': 's2'}])

    def test_select_on_a_document(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        code, out, _ = run(capsys, 'check', '--select', 'visible.read,hidden', '--policy', str(write(tmp_path, POLICY)))
        assert code == 0
        assert out == {'visible': {'read': ['Notice', 'Seen', 'Station', 'Status', 'Trace']}, 'hidden': 2}

    def test_a_select_that_names_no_field(self, reads, capsys):
        code, _, err = run(capsys, 'fetch', 'Station', '--select', 'stok', '--policy', reads)
        assert code == 2
        assert err['message'] == "--select 'stok' names no field of Station; did you mean 'stock'?"
        assert err['path'] == ['stok']

    def test_a_declared_field_absent_everywhere_is_quiet(self, reads, capsys):
        code, out, err = run(capsys, 'fetch', 'Station', '--select', 'station,closed', '--policy', reads)
        assert (code, out, err) == (0, [{'station': 's1'}, {'station': 's2'}], None)

    def test_a_malformed_select_is_a_usage_error(self, reads, capsys):
        code, _, err = run(capsys, 'fetch', 'Station', '--select', 'a..b', '--policy', reads)
        assert (code, err['error']) == (2, 'usage')

    def test_over_the_cap_is_refused_with_its_parts(self, reads, capsys):
        code, out, err = run(capsys, 'fetch', 'Station', '--max-bytes', '50', '--policy', reads)
        assert (code, out) == (6, None)
        assert err['error'] == 'too_large'
        assert err['cap'] == 50
        assert err['parts']['items'] == 2
        assert set(err['parts']['bytes']) == {'world', 'station', 'stock'}

    def test_the_cap_from_the_environment_and_no_cap(self, reads, capsys, monkeypatch):
        monkeypatch.setenv('TOOLED_MAX_BYTES', '50')
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
        assert out['reply'][2] == {'name': 'stock', 'type': 'int', 'doc': 'units in stock', 'constraints': {'ge': 0}}
        _, out, _ = run(capsys, 'describe', 'Station', '--reply', '--policy', policy)
        assert out['reply']['title'] == 'Station'

    def test_variant_and_reply_are_one_or_the_other(self, tmp_path, capsys):
        policy = str(write(tmp_path, POLICY))
        code, _, err = run(capsys, 'describe', 'LookReply', '--variant', 'ping', '--reply', '--policy', policy)
        assert (code, err['message']) == (2, 'tooled describe: argument --reply: not allowed with argument --variant')


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

    def test_a_path_that_names_no_field_is_refused_before_subscribing(self, reads, capsys):
        code, out, err = run(capsys, 'listen', 'Station', '--count', '2', '--select', 'station,stok', '--policy', reads)
        assert (code, out, err['message']) == (
            2,
            None,
            "--select 'stok' names no field of Station; did you mean 'stock'?",
        )

    def test_a_declared_path_that_never_matched_is_quiet(self, reads, capsys):
        assert main(['listen', 'Station', '--count', '2', '--select', 'station,closed', '--policy', reads]) == 0
        out, err = capsys.readouterr()
        assert [json.loads(line) for line in out.splitlines()] in (
            [{'station': 's1'}, {'station': 's2'}],
            [{'station': 's2'}, {'station': 's1'}],
        )
        assert err == ''


def test_the_help_names_every_exit_code(capsys):
    from tooled.cli import EPILOG
    from tooled.errors import TooledError

    codes = {cls.exit_code for cls in TooledError.__subclasses__()}
    assert codes == {2, 3, 4, 5, 6, 7}
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


class TestPathVariables:
    BODY = """
[inject.token]
toml = "${DEMO_CONFIG:-~/.config}/demo/ids.toml"
key = "token"

[[allow]]
ops = ["read"]
keys = ["demo/w1/**"]
"""

    def test_check_shows_the_path_it_read(self, tmp_path, capsys, monkeypatch):
        (tmp_path / 'cfg' / 'demo').mkdir(parents=True)
        (tmp_path / 'cfg' / 'demo' / 'ids.toml').write_text('token = "tok-from-cfg"\n')
        monkeypatch.setenv('DEMO_CONFIG', str(tmp_path / 'cfg'))
        code, out, _ = run(capsys, 'check', '--policy', str(write(tmp_path, self.BODY)))
        assert code == 0
        assert out['inject']['token'] == {
            'source': 'toml ${DEMO_CONFIG:-~/.config}/demo/ids.toml, key token',
            'length': len('tok-from-cfg'),
            'path': str(tmp_path / 'cfg' / 'demo' / 'ids.toml'),
        }

    def test_no_resolve_expands_nothing(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv('DEMO_CONFIG', raising=False)
        body = self.BODY.replace('${DEMO_CONFIG:-~/.config}', '${DEMO_CONFIG}')  # no default: unset is an error
        policy = str(write(tmp_path, body))
        code, out, _ = run(capsys, 'check', '--no-resolve', '--policy', policy)
        assert (code, out['inject']['token']['resolved']) == (0, False)
        code, _, err = run(capsys, 'check', '--policy', policy)
        assert (code, err['error']) == (2, 'policy')
        assert '${DEMO_CONFIG}' in err['message']


class TestGenerated:
    GENERATE = READS + '\n[inject.ask_id]\ngenerate = "uuid"\n'

    @pytest.fixture
    def policy(self, tmp_path, world, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-ann')
        return str(write(tmp_path, self.GENERATE, head=HEAD.replace('tcp/127.0.0.1:7447', world.endpoint)))

    def test_each_call_sends_a_fresh_value(self, policy, world, capsys):
        argv = [
            'query',
            'LookReply',
            'player=ann',
            '--request',
            '{"kind": "ping", "station": "s1"}',
            '--policy',
            policy,
        ]
        assert run(capsys, *argv)[0] == 0
        assert run(capsys, *argv)[0] == 0
        first, second = (request.ask_id for request in world.asked)
        assert re.fullmatch('[0-9a-f]{32}', first)
        assert first != second
        assert {request.token for request in world.asked} == {'tok-ann'}

    def test_a_given_value_is_refused(self, policy, world, capsys):
        request = '{"kind": "ping", "station": "s1", "ask_id": "mine"}'
        code, _, err = run(capsys, 'query', 'LookReply', 'player=ann', '--request', request, '--policy', policy)
        assert (code, err['error']) == (3, 'refused')
        assert world.asked == []

    def test_describe_leaves_it_out(self, policy, capsys):
        _, out, _ = run(capsys, 'describe', 'LookReply', '--policy', policy)
        assert out['injected'] == ['token', 'ask_id']
        assert out['request']['properties'] == {}

    def test_check_shows_its_source_and_makes_none(self, policy, capsys):
        for extra in ([], ['--no-resolve']):
            _, out, _ = run(capsys, 'check', *extra, '--policy', policy)
            assert out['inject']['ask_id'] == {'source': 'generate uuid'}
