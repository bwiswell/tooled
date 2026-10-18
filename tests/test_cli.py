import json
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
