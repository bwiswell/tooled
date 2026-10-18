import re

import pytest

from parsed.errors import PolicyFileError
from parsed.policy.config import Source
from parsed.policy.sources import Injected, describe, resolve

IDS = """
# a client's identities
[[players]]
world = "w1"
player = "ann"
token = "tok-ann"

[[players]]
world = "w1"
player = "bo"
token = "tok-bo"

[[players]]
world = "w2"
player = "ann"
token = "tok-ann-2"
"""


def source(**kwargs):
    return Source(**kwargs)


class TestEnv:
    def test_reads_the_variable(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-env')
        injected = resolve('token', source(env='DEMO_TOKEN'), base=tmp_path)
        assert injected == Injected('token', 'env DEMO_TOKEN', 'tok-env')

    def test_an_unset_variable_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.delenv('DEMO_TOKEN', raising=False)
        with pytest.raises(PolicyFileError, match=r'\$DEMO_TOKEN is not set') as exc:
            resolve('token', source(env='DEMO_TOKEN'), base=tmp_path)
        assert exc.value.path == ('inject', 'token')


class TestFile:
    def test_reads_the_whole_file_stripped_from_the_policy_s_directory(self, tmp_path):
        (tmp_path / 'token.txt').write_text('  tok-file\n')
        assert resolve('token', source(file='token.txt'), base=tmp_path).value == 'tok-file'

    def test_an_absolute_path_is_read_as_given(self, tmp_path):
        path = tmp_path / 'abs.txt'
        path.write_text('tok-abs')
        assert resolve('token', source(file=str(path)), base=tmp_path / 'elsewhere').value == 'tok-abs'

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(PolicyFileError, match='could not read'):
            resolve('token', source(file='nope.txt'), base=tmp_path)

    def test_an_empty_file_is_refused(self, tmp_path):
        (tmp_path / 'empty.txt').write_text('\n')
        with pytest.raises(PolicyFileError, match='is empty'):
            resolve('token', source(file='empty.txt'), base=tmp_path)


class TestToml:
    @pytest.fixture
    def ids(self, tmp_path):
        (tmp_path / 'ids.toml').write_text(IDS)
        return tmp_path

    def test_one_table_by_its_values(self, ids):
        found = resolve(
            'token',
            source(toml='ids.toml', table='players', match={'world': 'w1', 'player': 'bo'}, key='token'),
            base=ids,
        )
        assert found.value == 'tok-bo'

    @pytest.mark.parametrize(('match', 'count'), [({'world': 'w1'}, 2), ({'player': 'cy'}, 0)])
    def test_anything_but_one_match_is_refused(self, ids, match, count):
        with pytest.raises(PolicyFileError, match=f'{count} of \\[\\[players\\]\\]'):
            resolve('token', source(toml='ids.toml', table='players', match=match, key='token'), base=ids)

    def test_a_top_level_key(self, tmp_path):
        (tmp_path / 'one.toml').write_text('token = "tok-top"\n')
        assert resolve('token', source(toml='one.toml', key='token'), base=tmp_path).value == 'tok-top'

    def test_no_such_array_of_tables(self, ids):
        with pytest.raises(PolicyFileError, match=r'no array of tables \[\[teams\]\]'):
            resolve('token', source(toml='ids.toml', table='teams', key='token'), base=ids)

    @pytest.mark.parametrize('text', ['token = 7\n', 'other = "x"\n', 'token = ""\n'])
    def test_no_string_value(self, tmp_path, text):
        (tmp_path / 'bad.toml').write_text(text)
        with pytest.raises(PolicyFileError, match="no string 'token'"):
            resolve('token', source(toml='bad.toml', key='token'), base=tmp_path)

    def test_not_toml(self, tmp_path):
        (tmp_path / 'bad.toml').write_text('[[players]\n')
        with pytest.raises(PolicyFileError, match='not valid TOML'):
            resolve('token', source(toml='bad.toml', key='token'), base=tmp_path)


class TestSecrecy:
    def test_the_repr_leaves_the_value_out(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok-secret')
        injected = resolve('token', source(env='DEMO_TOKEN'), base=tmp_path)
        assert 'tok-secret' not in repr(injected)

    def test_no_error_names_a_value(self, tmp_path):
        (tmp_path / 'ids.toml').write_text(IDS)
        with pytest.raises(PolicyFileError) as exc:
            resolve(
                'token', source(toml='ids.toml', table='players', match={'world': 'w1'}, key='token'), base=tmp_path
            )
        assert 'tok-' not in exc.value.message

    @pytest.mark.parametrize(
        ('kwargs', 'words'),
        [
            ({'env': 'DEMO_TOKEN'}, 'env DEMO_TOKEN'),
            ({'file': '~/token'}, 'file ~/token'),
            ({'toml': 'ids.toml', 'key': 'token'}, 'toml ids.toml, key token'),
            (
                {'toml': 'ids.toml', 'table': 'players', 'match': {'world': 'w1'}, 'key': 'token'},
                "toml ids.toml [[players]] where world = 'w1', key token",
            ),
        ],
    )
    def test_describe_says_where_never_what(self, kwargs, words):
        assert describe(source(**kwargs)) == words


class TestGenerate:
    def test_a_fresh_uuid_each_call(self, tmp_path):
        source = Source(generate='uuid')
        first, second = (resolve('order_id', source, base=tmp_path) for _ in range(2))
        assert first.source == 'generate uuid'
        assert re.fullmatch('[0-9a-f]{32}', first.value)
        assert first.value != second.value

    def test_it_is_generated_and_described(self):
        from parsed.policy.sources import generated

        assert generated(Source(generate='uuid'))
        assert not generated(Source(env='A'))
        assert describe(Source(generate='uuid')) == 'generate uuid'


class TestPathVariables:
    def test_a_set_variable(self, tmp_path, monkeypatch):
        (tmp_path / 'cfg').mkdir()
        (tmp_path / 'cfg' / 'token.txt').write_text('tok-var\n')
        monkeypatch.setenv('DEMO_DIR', str(tmp_path / 'cfg'))
        found = resolve('token', source(file='${DEMO_DIR}/token.txt'), base=tmp_path / 'elsewhere')
        assert (found.value, found.path) == ('tok-var', str(tmp_path / 'cfg' / 'token.txt'))

    @pytest.mark.parametrize('value', [None, ''])  # unset or empty: the default, as in a shell
    def test_the_default(self, tmp_path, monkeypatch, value):
        (tmp_path / 'fallback.txt').write_text('tok-default')
        if value is None:
            monkeypatch.delenv('DEMO_DIR', raising=False)
        else:
            monkeypatch.setenv('DEMO_DIR', value)
        found = resolve('token', source(file='${DEMO_DIR:-.}/fallback.txt'), base=tmp_path)  # relative: the policy's
        assert (found.value, found.path) == ('tok-default', str(tmp_path / 'fallback.txt'))

    def test_a_default_with_a_tilde(self, tmp_path, monkeypatch):
        (tmp_path / '.config' / 'demo').mkdir(parents=True)
        (tmp_path / '.config' / 'demo' / 'ids.toml').write_text(IDS)
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
        given = source(
            toml='${XDG_CONFIG_HOME:-~/.config}/demo/ids.toml', table='players', match={'player': 'bo'}, key='token'
        )
        found = resolve('token', given, base=tmp_path / 'policy')
        assert (found.value, found.path) == ('tok-bo', str(tmp_path / '.config' / 'demo' / 'ids.toml'))
        assert describe(given).startswith('toml ${XDG_CONFIG_HOME:-~/.config}/demo/ids.toml')  # as written

    def test_several_in_one_path(self, tmp_path, monkeypatch):
        (tmp_path / 'a' / 'b').mkdir(parents=True)
        (tmp_path / 'a' / 'b' / 't.txt').write_text('tok-ab')
        monkeypatch.setenv('DEMO_A', 'a')
        monkeypatch.setenv('DEMO_B', 'b')
        assert resolve('token', source(file='${DEMO_A}/${DEMO_B}/t.txt'), base=tmp_path).value == 'tok-ab'

    def test_an_unset_variable_with_no_default_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.delenv('DEMO_DIR', raising=False)
        with pytest.raises(PolicyFileError) as exc:
            resolve('token', source(file='${DEMO_DIR}/token.txt'), base=tmp_path)
        assert exc.value.message == (
            "inject 'token': ${DEMO_DIR} in '${DEMO_DIR}/token.txt' is not set, and has no default (${DEMO_DIR:-…})"
        )
        assert exc.value.path == ('inject', 'token')

    def test_a_path_is_shown_only_for_a_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TOKEN', 'tok')
        assert resolve('token', source(env='DEMO_TOKEN'), base=tmp_path).path is None
        assert resolve('order_id', source(generate='uuid'), base=tmp_path).path is None
