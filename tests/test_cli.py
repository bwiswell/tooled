import subprocess
import sys

import pytest
import seared
import zeared

import parsed
from parsed.cli import main, version


def test_version_names_parsed_seared_and_zeared():
    assert version() == f'parsed {parsed.__version__} (seared {seared.__version__}, zeared {zeared.__version__})'


def test_version_prints_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(['--version'])
    assert exc.value.code == 0
    assert capsys.readouterr().out == version() + '\n'


def test_no_arguments_prints_the_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith('usage: parsed')
    assert '--version' in out


def test_an_unknown_flag_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(['--nope'])
    assert exc.value.code == 2
    assert 'unrecognized arguments: --nope' in capsys.readouterr().err


def test_python_dash_m_runs_the_same_command():
    done = subprocess.run(
        [sys.executable, '-m', 'parsed', '--version'], capture_output=True, text=True, check=False, timeout=60
    )
    assert done.returncode == 0
    assert done.stdout == version() + '\n'
