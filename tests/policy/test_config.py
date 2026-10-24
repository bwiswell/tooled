import re

import pytest
from _support_policy import HEAD, write
from zeared import Mode

from tooled.errors import PolicyFileError
from tooled.policy.config import DEFAULT_NAME, POLICY_ENV, Op, Route, find, load


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
        assert DEFAULT_NAME == 'tooled.toml'

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
                operations = ["LookReply"]
                variants = ["dev-*"]

                [[deny]]
                ops = ["invoke"]
                fields = ["move_id"]
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
        assert allow.operations == ['*']
        assert allow.keys == ['demo/w1/**']
        assert allow.variants is None
        assert allow.fields is None
        assert policy.deny[0].variants == ['dev-*']
        assert policy.deny[1].fields == ['move_id']

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
    def test_the_protocol_and_its_packages_mesh_are_required(self, tmp_path, table):
        head = HEAD.split('[protocol]')[0] if table == 'protocol' else '[protocol]\npackages = ["_support_protocol"]\n'
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, head=head))
        assert exc.value.path == (table,)

    def test_not_toml(self, tmp_path):
        with pytest.raises(PolicyFileError, match='not valid TOML'):
            load(write(tmp_path, '[[allow]\n'))

    def test_keepalive_is_refused_with_the_reason(self, tmp_path):
        body = '[[keepalive]]\ncontract = "X"\n'
        with pytest.raises(PolicyFileError, match='tooled serve') as exc:
            load(write(tmp_path, body))
        assert exc.value.path == ('keepalive',)

    def test_an_extension_is_refused_with_the_reason(self, tmp_path):
        head = HEAD + 'extension = "demo.agent"\n'
        with pytest.raises(PolicyFileError, match='extensions') as exc:
            load(write(tmp_path, head=head))
        assert exc.value.path == ('protocol', 'extension')

    @pytest.mark.parametrize('side', ['allow', 'deny'])
    def test_a_rule_s_contracts_is_refused_naming_operations(self, tmp_path, side):
        body = f'[[{side}]]\nops = ["read"]\n\n[[{side}]]\nops = ["read"]\ncontracts = ["Station"]\n'
        with pytest.raises(PolicyFileError, match=r'`contracts` was renamed `operations` in tooled 0\.2\.4') as exc:
            load(write(tmp_path, body))
        assert exc.value.path == (side, 1, 'contracts')


class TestChecks:
    def test_nothing_to_read_operations_from(self, tmp_path):
        head = HEAD.replace('["_support_protocol"]', '[]')
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, head=head))
        assert exc.value.message == (
            'the policy names no operations: give `[protocol] packages`, `[app] factory` or `[protocol] trees`'
        )
        assert exc.value.path == ('protocol',)

    def test_a_rule_with_no_ops(self, tmp_path):
        with pytest.raises(PolicyFileError, match='at least one op') as exc:
            load(write(tmp_path, '[[deny]]\nops = []\n'))
        assert exc.value.path == ('deny', 0, 'ops')

    def test_fields_on_an_allow_rule(self, tmp_path):
        with pytest.raises(PolicyFileError, match='only a deny rule does; move it to a') as exc:
            load(write(tmp_path, '[[allow]]\nops = ["invoke"]\nfields = ["move_id"]\n'))
        assert exc.value.path == ('allow', 0, 'fields')

    def test_fields_that_name_none(self, tmp_path):
        with pytest.raises(PolicyFileError, match='names no field, so the rule would hide nothing') as exc:
            load(write(tmp_path, '[[deny]]\nops = ["invoke"]\nfields = []\n'))
        assert exc.value.path == ('deny', 0, 'fields')

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
        from tooled.policy.config import Generator

        source = load(write(tmp_path, '[inject.order_id]\ngenerate = "uuid"\n')).inject['order_id']
        assert source.generate is Generator.UUID

    def test_an_unknown_generator_is_refused_with_its_path(self, tmp_path):
        with pytest.raises(PolicyFileError, match='not a valid Generator') as exc:
            load(write(tmp_path, '[inject.order_id]\ngenerate = "uid"\n'))
        assert exc.value.path == ('inject', 'order_id', 'generate')


class TestPathVariables:
    @pytest.mark.parametrize(
        'path',
        ['${XDG_CONFIG_HOME:-~/.config}/demo/identity.toml', '${HOME}/ids.toml', 'plain.toml', '~/ids.toml'],
    )
    def test_a_path_s_variables_load_as_written(self, tmp_path, monkeypatch, path):
        monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)  # nothing is expanded at load
        body = f'[inject.token]\ntoml = "{path}"\nkey = "token"\n'
        assert load(write(tmp_path, body)).inject['token'].toml == path

    @pytest.mark.parametrize(
        ('kind', 'path', 'match'),
        [
            ('toml', '$HOME/ids.toml', r'write a variable as \$\{HOME\}'),
            ('file', '${1X}/token', r'a variable is \$\{NAME\}'),
            ('file', '${A/token', r'a variable is \$\{NAME\}'),
            ('toml', '${A:-${B}}/ids.toml', 'a default holds no variable'),
        ],
    )
    def test_a_malformed_variable_is_refused_with_its_path(self, tmp_path, kind, path, match):
        key = '\nkey = "token"' if kind == 'toml' else ''
        with pytest.raises(PolicyFileError, match=match) as exc:
            load(write(tmp_path, f'[inject.token]\n{kind} = "{path}"{key}\n'))
        assert exc.value.path == ('inject', 'token', kind)


#: The head of a policy with no protocol packages, for the trees and apps below.
NO_PACKAGES = HEAD.replace('packages = ["_support_protocol"]\n', '')


class TestCommandSources:
    def test_an_app_s_factory(self, tmp_path):
        policy = load(write(tmp_path, '[app]\nfactory = "_support_app:factory"\n', head=NO_PACKAGES))
        assert policy.app is not None
        assert (policy.app.factory, policy.protocol.packages, policy.protocol.trees) == ('_support_app:factory', [], [])

    def test_trees_with_no_app(self, tmp_path):
        policy = load(write(tmp_path, head=NO_PACKAGES + 'trees = ["_support_tree.game"]\n'))
        assert (policy.app, policy.protocol.trees) == (None, ['_support_tree.game'])

    def test_trees_and_an_app_are_one_too_many(self, tmp_path):
        head = HEAD + 'trees = ["_support_tree.game"]\n'
        with pytest.raises(PolicyFileError, match=re.escape("is for a policy with no [app]: the app's mounts")) as exc:
            load(write(tmp_path, '[app]\nfactory = "_support_app:factory"\n', head=head))
        assert exc.value.path == ('protocol', 'trees')

    @pytest.mark.parametrize(
        'factory', ['_support_app', '_support_app:', ':factory', 'a-b:c', '_support_app:factory()']
    )
    def test_a_factory_is_a_module_and_a_name(self, tmp_path, factory):
        with pytest.raises(PolicyFileError, match=re.escape('`[app] factory` is pkg.module:function')) as exc:
            load(write(tmp_path, f'[app]\nfactory = "{factory}"\n'))
        assert exc.value.path == ('app', 'factory')

    def test_an_app_s_state(self, tmp_path):
        policy = load(write(tmp_path, '[app]\nfactory = "_support_app:factory"\nstate = "_support_app:state"\n'))
        assert policy.app is not None
        assert policy.app.state == '_support_app:state'
        assert load(write(tmp_path, '[app]\nfactory = "_support_app:factory"\n')).app.state is None

    def test_a_state_is_a_module_and_a_name(self, tmp_path):
        with pytest.raises(PolicyFileError, match=re.escape('`[app] state` is pkg.module:function')) as exc:
            load(write(tmp_path, '[app]\nfactory = "_support_app:factory"\nstate = "state"\n'))
        assert exc.value.path == ('app', 'state')

    def test_an_app_needs_its_factory(self, tmp_path):
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, '[app]\n'))
        assert exc.value.path == ('app', 'factory')


class TestTreeOps:
    def test_see_do_and_confirm_load(self, tmp_path):
        policy = load(write(tmp_path, '[[allow]]\nops = ["see", "do", "confirm"]\nkeys = ["relay/**"]\n'))
        assert policy.allow[0].ops == [Op.SEE, Op.DO, Op.CONFIRM]

    def test_a_rule_is_one_route_s(self, tmp_path):
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, '[[deny]]\nops = ["read", "see"]\n'))
        assert exc.value.message == (
            "a rule's ops are the wire's or the tree's, so its keys are read in one space; this one has 'read' and "
            "'see': split it in two"
        )
        assert exc.value.path == ('deny', 0, 'ops')

    @pytest.mark.parametrize(('key', 'value'), [('variants', '["ping"]'), ('fields', '["note"]')])
    def test_a_tree_rule_has_no_request(self, tmp_path, key, value):
        with pytest.raises(PolicyFileError, match="is about a contract's request, which the tree's ops never") as exc:
            load(write(tmp_path, f'[[deny]]\nops = ["do"]\n{key} = {value}\n'))
        assert exc.value.path == ('deny', 0, key)


#: The head of a policy of commands alone: no mesh, no protocol packages, the fixture app.
COMMANDS_ONLY = '[protocol]\n\n[app]\nfactory = "_support_app:factory"\n'


class TestWithoutAMesh:
    def test_commands_alone_need_none(self, tmp_path):
        policy = load(write(tmp_path, '[[allow]]\nops = ["see", "do", "confirm"]\n', head=COMMANDS_ONLY))
        assert policy.mesh is None

    def test_trees_alone_need_none(self, tmp_path):
        assert load(write(tmp_path, head='[protocol]\ntrees = ["_support_tree.game"]\n')).mesh is None

    def test_a_protocol_s_contracts_need_one(self, tmp_path):
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, head='[protocol]\npackages = ["_support_protocol"]\n'))
        assert (
            exc.value.message == "`[mesh]` is missing, and a protocol's contracts are reached through it: name the mesh"
        )
        assert exc.value.path == ('mesh',)

    @pytest.mark.parametrize('op', ['read', 'query', 'invoke', 'publish', 'unretain'])
    def test_a_wire_op_reaches_no_wire(self, tmp_path, op):
        body = f'[[allow]]\nops = ["see"]\n\n[[deny]]\nops = ["{op}"]\n'
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, body, head=COMMANDS_ONLY))
        assert exc.value.message == (
            f"'{op}' is a wire op, and a policy with no [mesh] reaches no wire: use the tree's ops (see, do, confirm), "
            'or name the mesh'
        )
        assert exc.value.path == ('deny', 0, 'ops')


class TestRoute:
    def test_the_tree_by_default(self, tmp_path):
        assert load(write(tmp_path)).protocol.route is Route.TREE

    def test_the_wire(self, tmp_path):
        assert load(write(tmp_path, head=HEAD + 'route = "wire"\n')).protocol.route is Route.WIRE

    def test_a_route_that_is_neither(self, tmp_path):
        with pytest.raises(PolicyFileError) as exc:
            load(write(tmp_path, head=HEAD + 'route = "both"\n'))
        assert exc.value.path == ('protocol', 'route')
