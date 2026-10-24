import pytest

from tooled.errors import (
    CatalogError,
    MeshError,
    PolicyFileError,
    RefusedError,
    TooledError,
    UsageError,
    relayed,
)


@pytest.mark.parametrize(
    ('cls', 'kind', 'code'),
    [
        (UsageError, 'usage', 2),
        (PolicyFileError, 'policy', 2),
        (CatalogError, 'catalog', 2),
        (RefusedError, 'refused', 3),
        (MeshError, 'mesh', 5),
    ],
)
def test_each_kind_has_its_exit_code(cls, kind, code):
    assert issubclass(cls, TooledError)
    assert (cls.kind, cls.exit_code) == (kind, code)


def test_the_json_leaves_out_an_empty_path():
    assert UsageError('no contract named X').to_json() == {'error': 'usage', 'message': 'no contract named X'}


def test_the_json_carries_a_path():
    error = PolicyFileError('unknown key', path=('allow', 0, 'key'))
    assert error.to_json() == {'error': 'policy', 'message': 'unknown key', 'path': ['allow', 0, 'key']}
    assert str(error) == 'unknown key'


def test_too_large_carries_its_sizes():
    from tooled.errors import TooLargeError

    error = TooLargeError('over', size=500, cap=100, parts={'bytes': {'a': 400}})
    assert (error.kind, error.exit_code) == ('too_large', 6)
    assert error.to_json() == {
        'error': 'too_large',
        'message': 'over',
        'size': 500,
        'cap': 100,
        'parts': {'bytes': {'a': 400}},
    }


def test_a_lost_write_carries_its_generated_values():
    from tooled.errors import LostWriteError

    error = LostWriteError('no reply', generated={'move_id': 'abc'})
    assert (error.kind, error.exit_code) == ('mesh', 5)
    assert isinstance(error, MeshError)
    assert error.to_json() == {'error': 'mesh', 'message': 'no reply', 'generated': {'move_id': 'abc'}}
    assert LostWriteError('no reply', generated={}).to_json() == {'error': 'mesh', 'message': 'no reply'}


def test_a_timeout_carries_the_last_value_seen():
    from tooled.errors import TimedOutError

    error = TimedOutError('not met', last={'stock': 3})
    assert (error.kind, error.exit_code) == ('timeout', 7)
    assert error.to_json() == {'error': 'timeout', 'message': 'not met', 'last': {'stock': 3}}
    assert TimedOutError('not met', last=None).to_json()['last'] is None


def test_relayed_as_another_process_wrote_it():
    found = {'error': 'mesh', 'message': "the app's session would not open", 'path': ['app'], 'extra': 1}
    error = relayed(found, 5)
    assert isinstance(error, TooledError)
    assert (error.kind, error.exit_code, error.message, error.path, error.to_json()) == (
        'mesh',
        5,
        "the app's session would not open",
        ('app',),
        found,
    )
