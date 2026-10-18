import pytest
from _support_policy import HEAD, write
from zeared import Mode

from parsed.errors import PolicyFileError
from parsed.policy.config import DEFAULT_NAME, POLICY_ENV, Op, find, load


class TestFind:
    def test_policy_flag_first(self, tmp_path, monkeypatch):
        given = write(tmp_path, name='given.toml')
        monkeypatch.setenv(POLICY_ENV, str(write(tmp_path, name='env.toml')))
        monkeypatch.chdir(tmp_path)
        write(tmp_path)
        assert find(str(given)) == given.resolve()

    def test_then_the_environment(self, tmp_path, monkeypatch):
        env = write(tmp_path, name='env.toml')
        monkeypatch.setenv(POLICY_ENV, str(env))
        monkeypatch.chdir(tmp_path)
        write(tmp_path)
        assert find(None) == env.resolve()

    def test_then_the_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.delenv(POLICY_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        path = write(tmp_path)
        assert find(None) == path.resolve()
        assert DEFAULT_NAME == 'parsed.toml'

    @pytest.mark.parametrize('via', ['--policy', 'env', 'cwd'])
    def test_a_missing_file_says_where_it_looked(self, tmp_path, monkeypatch, via):
        monkeypatch.delenv(POLICY_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        given = None
        if via == '--policy':
            given = str(tmp_path / 'nope.toml')
        elif via == 'env':
            monkeypatch.setenv(POLICY_ENV, str(tmp_path / 'nope.toml'))
        with pytest.raises(PolicyFileError, match='no policy file at') as exc:
            find(given)
        assert {'--policy': '--policy', 'env': f'${POLICY_ENV}', 'cwd': 'working directory'}[via] in exc.value.message


class TestLoad:
    def test_a_whole_policy(self, tmp_path):
        policy = load(
            write(
                tmp_path,
                """
                [bind]
                world = "w1"

                [inject.token]
                env = "DEMO_TOKEN"

                [[allow]]
                ops = ["read", "query"]
                keys = ["demo/w1/**"]

                [[deny]]
                ops = ["query"]
                contracts = ["LookReply"]
                variants = ["dev-*"]
                """,
            )
        )
        assert policy.mesh.mode is Mode.CLIENT
        assert policy.mesh.router == 'tcp/127.0.0.1:7447'
        assert policy.protocol.packages == ['_support_protocol']
        assert policy.bind == {'world': 'w1'}
        assert policy.inject['token'].env == 'DEMO_TOKEN'
        (allow,) = policy.allow
        assert allow.ops == [Op.READ, Op.QUERY]
        assert allow.contracts == ['*']
        assert allow.keys == ['demo/w1/**']
        assert allow.variants is None
        assert policy.deny[0].variants == ['dev-*']

    def test_an_empty_policy_allows_nothing(self, tmp_path):
        policy = load(write(tmp_path))
        assert (policy.allow, policy.deny, policy.bind, policy.inject) == ([], [], {}, {})

    @pytest.mark.parametrize(
        ('body', 'path'),
        [
            ('[[allow]]\nops = ["read"]\nkey = ["demo/**"]\n', ('allow', 0, 'key')),
            ('[[allow]]\nops = ["reed"]\n', ('allow', 0, 'ops', 0)),
            ('[bind]\nworld = 3\n', ('bind', 'world')),
            ('[colour]\nhue = "red"\n', ('colour',)),
            ('[inject.token]\nenv = "X"\nfle = "y"\n', ('inject', 'token', 'fle')),
        ],
    )
    def test_a_typo_anywhere_is_refused_with_its_path(self, tmp_path, body, path):
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, body))
        assert exc.value.path == path

    def test_a_typo_in_mesh_is_refused_by_zeared_s_own_config(self, tmp_path):
        head = HEAD.replace('router =', 'routr =')
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, head=head))
        assert exc.value.path == ('mesh', 'routr')

    @pytest.mark.parametrize('table', ['mesh', 'protocol'])
    def test_mesh_and_protocol_are_required(self, tmp_path, table):
        head = HEAD.split('[protocol]')[0] if table == 'protocol' else '[protocol]\npackages = ["_support_protocol"]\n'
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, head=head))
        assert exc.value.path == (table,)

    def test_not_toml(self, tmp_path):
        with pytest.raises(PolicyFileError, match='not valid TOML'):
            load(write(tmp_path, '[[allow]\n'))

    def test_keepalive_is_refused_with_the_reason(self, tmp_path):
        body = '[[keepalive]]\ncontract = "X"\n'
        with pytest.raises(PolicyFileError, match='parsed serve') as exc:
            load(write(tmp_path, body))
        assert exc.value.path == ('keepalive',)

    def test_an_extension_is_refused_with_the_reason(self, tmp_path):
        head = HEAD + 'extension = "demo.agent"\n'
        with pytest.raises(PolicyFileError, match='extensions') as exc:
            load(write(tmp_path, head=head))
        assert exc.value.path == ('protocol', 'extension')


class TestChecks:
    def test_no_packages(self, tmp_path):
        head = HEAD.replace('["_support_protocol"]', '[]')
        with pytest.raises(PolicyFileError, match='names no package'):
            load(write(tmp_path, head=head))

    def test_a_rule_with_no_operations(self, tmp_path):
        with pytest.raises(PolicyFileError, match='at least one operation') as exc:
            load(write(tmp_path, '[[deny]]\nops = []\n'))
        assert exc.value.path == ('deny', 0, 'ops')

    def test_a_key_that_is_no_key_expression(self, tmp_path):
        with pytest.raises(PolicyFileError, match='empty chunks') as exc:
            load(write(tmp_path, '[[allow]]\nops = ["read"]\nkeys = ["demo/**", "demo//x"]\n'))
        assert exc.value.path == ('allow', 0, 'keys', 1)

    @pytest.mark.parametrize('value', ['a/b', '*', 'w-$*', ''])
    def test_a_bound_value_is_one_concrete_chunk(self, tmp_path, value):
        with pytest.raises(PolicyFileError, match='one chunk') as exc:
            load(write(tmp_path, f'[bind]\nworld = "{value}"\n'))
        assert exc.value.path == ('bind', 'world')

    @pytest.mark.parametrize(
        ('source', 'match'),
        [
            ('', 'exactly one of env, file, toml or generate'),
            ('env = "A"\ngenerate = "uuid"\n', 'exactly one'),
            ('generate = "uuid"\nkey = "token"\n', 'go with toml, not generate'),
            ('env = "A"\nfile = "b"\n', 'exactly one'),
            ('env = "A"\nkey = "token"\n', 'go with toml'),
            ('file = "a"\nmatch = { x = "y" }\n', 'go with toml'),
            ('toml = "a.toml"\n', 'names the `key`'),
            ('toml = "a.toml"\nkey = "token"\nmatch = { x = "y" }\n', 'needs `table`'),
        ],
    )
    def test_an_inject_source_is_one_whole_source(self, tmp_path, source, match):
        with pytest.raises(PolicyFileError, match=match) as exc:
            load(write(tmp_path, f'[inject.token]\n{source}'))
        assert exc.value.path[:2] == ('inject', 'token')

    def test_a_whole_toml_source_loads(self, tmp_path):
        body = '[inject.token]\ntoml = "ids.toml"\ntable = "players"\nmatch = { world = "w1" }\nkey = "token"\n'
        source = load(write(tmp_path, body)).inject['token']
        assert (source.toml, source.table, source.match, source.key) == (
            'ids.toml',
            'players',
            {'world': 'w1'},
            'token',
        )


class TestGenerate:
    def test_a_generated_source_loads(self, tmp_path):
        from parsed.policy.config import Generator

        source = load(write(tmp_path, '[inject.order_id]\ngenerate = "uuid"\n')).inject['order_id']
        assert source.generate is Generator.UUID

    def test_an_unknown_generator_is_refused_with_its_path(self, tmp_path):
        with pytest.raises(PolicyFileError, match='not a valid Generator') as exc:
            load(write(tmp_path, '[inject.order_id]\ngenerate = "uid"\n'))
        assert exc.value.path == ('inject', 'order_id', 'generate')
