import re

import _support_app
import pytest
from _support_policy import write

from tooled.errors import CatalogError, MeshError
from tooled.policy import load
from tooled.tree import app_of, made_state, opened, state_of

#: A policy of the fixture app, its state built from a bound slot and two injected values.
POLICY = """
[protocol]

[app]
factory = "_support_app:factory"
state = "_support_app:state"

[bind]
fleet = "f1"

[inject.ticket]
env = "DEMO_TICKET"

[inject.session_id]
generate = "uuid"

[[allow]]
ops = ["see", "do"]
"""


class TestMadeState:
    def test_given_the_policy_s_values_by_keyword(self):
        built = made_state('_support_app:state', bound={'fleet': 'f1'}, injected={'token': 't'})
        assert (built.bound, built.injected) == ({'fleet': 'f1'}, {'token': 't'})

    @pytest.mark.parametrize(
        ('target', 'message'),
        [
            ('_support_nowhere:state', "would not load: ModuleNotFoundError: No module named '_support_nowhere'"),
            ('_support_app:NOT_CALLABLE', 'is no function, but str'),
            ('_support_app:no_keywords', 'takes the keyword arguments bound and injected'),
        ],
    )
    def test_the_policy_s_mistakes(self, target, message):
        with pytest.raises(CatalogError, match=re.escape(message)) as exc:
            made_state(target, bound={}, injected={})
        assert exc.value.path == ('app', 'state')

    def test_the_app_s_failure(self):
        with pytest.raises(MeshError) as exc:
            made_state('_support_app:broken_state', bound={'fleet': 'f1'}, injected={})
        assert exc.value.message == (
            "the app's state would not build: [app] state '_support_app:broken_state' failed: ConnectionError: no "
            'link today (1 bound, 0 injected)'
        )


class TestStateOf:
    def test_built_from_every_bound_and_injected_value(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TICKET', 'tk-ann')
        path = write(tmp_path, head=POLICY)
        built = state_of(load(path), path)
        assert built.bound == {'fleet': 'f1'}
        assert built.injected['ticket'] == 'tk-ann'
        assert re.fullmatch('[0-9a-f]{32}', built.injected['session_id'])  # a generated value, made once

    def test_none_without_a_state_function(self, tmp_path):
        path = write(tmp_path, head=POLICY.replace('state = "_support_app:state"\n', ''))
        assert state_of(load(path), path) is None


class TestOpened:
    async def test_entered_with_the_session_and_left_after(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TICKET', 'tk-ann')
        path = write(tmp_path, head=POLICY)
        policy = load(path)
        built = state_of(policy, path)
        async with opened(app_of(policy), built) as session:
            assert (built.entered, built.left) == (True, False)
            said = await session.call('relay status')  # its handler sees the state as ctx.state
        assert said == "up, bound ['fleet'], injected ['session_id', 'ticket']"
        assert (built.left, built.left_with) == (True, None)

    async def test_an_error_inside_leaves_the_state_knowing_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TICKET', 'tk-ann')
        path = write(tmp_path, head=POLICY)
        policy = load(path)
        built = state_of(policy, path)

        async def inside() -> None:
            async with opened(app_of(policy), built):
                msg = 'mid-session'
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match='mid-session'):
            await inside()
        assert isinstance(built.left_with, RuntimeError)

    async def test_no_terminal_so_an_interactive_command_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv('DEMO_TICKET', 'tk-ann')
        path = write(tmp_path, head=POLICY)
        policy = load(path)
        async with opened(app_of(policy), state_of(policy, path)) as session:
            result = await session.run('station s1 map')
        assert result.ok is False
        assert result.error is not None
        assert result.error['error'] == 'refused'
        assert result.error['instead'] == 'station {station} show'  # what to say instead

    async def test_a_state_that_will_not_open(self, tmp_path):
        policy = load(write(tmp_path, head=POLICY.replace('_support_app:state', '_support_app:unopenable')))
        with pytest.raises(MeshError) as exc:
            async with opened(app_of(policy), _support_app.unopenable(bound={}, injected={})):
                pass
        assert exc.value.message == "the app's session would not open: ConnectionError: the server is down"

    def test_no_app_no_session(self, tmp_path):
        with pytest.raises(CatalogError, match='names no \\[app\\]'):
            app_of(load(write(tmp_path, head='[protocol]\ntrees = ["_support_tree.game"]\n')))
