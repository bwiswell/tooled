import pytest

from parsed.errors import CatalogError, MeshError, ParsedError, PolicyFileError, RefusedError, UsageError


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
    assert issubclass(cls, ParsedError)
    assert (cls.kind, cls.exit_code) == (kind, code)


def test_the_json_leaves_out_an_empty_path():
    assert UsageError('no contract named X').to_json() == {'error': 'usage', 'message': 'no contract named X'}


def test_the_json_carries_a_path():
    error = PolicyFileError('unknown key', path=('allow', 0, 'key'))
    assert error.to_json() == {'error': 'policy', 'message': 'unknown key', 'path': ['allow', 0, 'key']}
    assert str(error) == 'unknown key'


def test_too_large_carries_its_sizes():
    from parsed.errors import TooLargeError

    error = TooLargeError('over', size=500, cap=100, parts={'bytes': {'a': 400}})
    assert (error.kind, error.exit_code) == ('too_large', 6)
    assert error.to_json() == {
        'error': 'too_large',
        'message': 'over',
        'size': 500,
        'cap': 100,
        'parts': {'bytes': {'a': 400}},
    }
