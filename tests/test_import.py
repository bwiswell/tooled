import tomllib
from pathlib import Path

import parsed

_ROOT = Path(__file__).parent.parent


def test_the_version_is_stamped_the_same_in_both_places():
    project = tomllib.loads((_ROOT / 'pyproject.toml').read_text())
    # The release workflow tags from one and refuses to run if they differ;
    # `parsed --version` reads the other. Failing here catches it before the merge.
    assert project['project']['version'] == parsed.__version__


def test_public_surface():
    assert parsed.__all__ == ['__version__', 'main']
    assert callable(parsed.main)


def test_the_script_points_at_main():
    project = tomllib.loads((_ROOT / 'pyproject.toml').read_text())
    assert project['project']['scripts'] == {'parsed': 'parsed.cli:main'}
