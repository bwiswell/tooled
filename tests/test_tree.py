import re
import time

import _support_app
import pytest
from _support_policy import write

from tooled.catalog import Catalog
from tooled.errors import CatalogError, MeshError, RefusedError, TimedOutError, UsageError
from tooled.policy import Op, Rules, load
from tooled.tree import GRACE, app_of, bound_of, judged, made_state, opened, ran, state_of

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


#: The head of a policy of the chained fixture app: the game, the service twice, and a fleet's orders.
CHAINED = '[protocol]\n\n[app]\nfactory = "_support_app:chained"\n'


@pytest.fixture(scope='module')
def chained():
    return Catalog.load([], factory='_support_app:chained'), _support_app.chained()


def judge(tmp_path, chained, body, text, op=Op.DO):
    catalog, app = chained
    return judged(app, Rules(load(write(tmp_path, body, head=CHAINED)), catalog), text, op=op)


EVERY = '[[allow]]\nops = ["see", "do", "confirm"]\n'


class TestJudged:
    def test_a_read_under_see(self, tmp_path, chained):
        line = judge(tmp_path, chained, EVERY, 'station s1 show', op=Op.SEE)
        assert line is not None
        assert ([c.name for c in line.commands], line.keys, line.given, line.yes) == (
            ['StationShow'],
            ('station/s1/show',),
            ('station',),
            False,
        )

    def test_a_chain_judged_step_by_step(self, tmp_path, chained):
        line = judge(tmp_path, chained, EVERY, 'orders fleet f1 jump sol then dock vesta')
        assert line is not None
        assert [c.name for c in line.commands] == ['orders/FleetJump', 'orders/FleetDock']
        assert line.keys == ('orders/fleet/f1/jump/sol', 'orders/fleet/f1/dock/vesta')

    def test_a_chain_is_refused_when_one_step_is(self, tmp_path, chained):
        body = EVERY + '\n[[deny]]\nops = ["do"]\nkeys = ["orders/fleet/*/dock/vesta"]\n'
        with pytest.raises(RefusedError, match='do on orders/FleetDock at orders/fleet/f1/dock/vesta is denied'):
            judge(tmp_path, chained, body, 'orders fleet f1 jump sol then dock vesta')

    @pytest.mark.parametrize(
        ('text', 'op', 'message'),
        [
            ('fleet f1 mine', Op.SEE, 'see does not run FleetMine, which is no read; what does: do'),
            ('station s1 show', Op.DO, 'do does not run StationShow, which is a read; what does: see'),
        ],
    )
    def test_each_verb_runs_its_own(self, tmp_path, chained, text, op, message):
        with pytest.raises(UsageError) as exc:
            judge(tmp_path, chained, EVERY, text, op=op)
        assert exc.value.message == message

    def test_one_the_policy_does_not_show(self, tmp_path, chained):
        with pytest.raises(RefusedError, match='the policy does not show FleetMine'):
            judge(tmp_path, chained, '[[allow]]\nops = ["do"]\noperations = ["StationDock"]\n', 'fleet f1 mine')

    def test_a_key_the_policy_refuses_or_can_not_hold(self, tmp_path, chained):
        with pytest.raises(RefusedError, match='no allow rule covers do on FleetMine at fleet/f9/mine'):
            judge(tmp_path, chained, '[[allow]]\nops = ["do"]\nkeys = ["fleet/f1/**"]\n', 'fleet f9 mine')
        with pytest.raises(RefusedError, match="slot 'fleet' holds 'a/b'"):
            judge(tmp_path, chained, EVERY, 'fleet a/b mine')

    def test_yes_needs_confirm_where_it_asks(self, tmp_path, chained):
        with pytest.raises(RefusedError) as exc:
            judge(tmp_path, chained, '[[allow]]\nops = ["do"]\n', 'dock ceres --yes')
        assert exc.value.message == (
            'no allow rule covers confirm on StationDock at dock/ceres; leave --yes out, and its question comes back '
            'unanswered, nothing done'
        )
        line = judge(tmp_path, chained, '[[allow]]\nops = ["do"]\n', 'dock ceres')  # unanswered: no confirm needed
        assert line is not None
        assert line.yes is False
        assert judge(tmp_path, chained, EVERY, '--yes dock ceres').yes is True
        assert judge(tmp_path, chained, '[[allow]]\nops = ["do"]\n', 'fleet f1 mine --yes') is not None  # no question

    @pytest.mark.parametrize('text', ['fleet f1 jump', 'nowhere at all', 'station s1 show --help', '--help'])
    def test_a_line_that_runs_nothing_is_parsed_s_to_answer(self, tmp_path, chained, text):
        assert judge(tmp_path, chained, EVERY, text, op=Op.SEE) is None


class TestRan:
    """A line runs with no stdin and a stdout of its own, in a worker waited on for its bound."""

    def test_a_line_s_result(self):
        result, wrote = ran(_support_app.factory(), None, 'station s1 show', bound=5)
        assert (result.ok, result.reply.stock, wrote) == (True, 5, '')

    def test_a_prompt_fails_at_once(self):
        started = time.monotonic()
        result, wrote = ran(_support_app.desk(), None, 'note edit', bound=5)
        assert time.monotonic() - started < 2
        assert (result.ok, result.error['error'], result.error['message']) == (
            False,
            'handler',
            'EOFError: EOF when reading a line',
        )
        assert wrote == 'the note: '  # its prompt, kept out of the result

    def test_a_handler_still_waiting_is_given_up_on(self):
        _support_app.RELEASED.clear()
        try:
            with pytest.raises(TimedOutError) as exc:
                ran(_support_app.desk(), None, 'screen show', bound=0.3)
        finally:
            _support_app.RELEASED.set()  # let the worker end
        assert exc.value.message == (
            "no result for 'screen show' within 0.3s: a handler still waiting, on a person perhaps (a command not "
            'marked INTERACTIVE), was given up on; --timeout gives a line longer'
        )
        assert exc.value.last is None

    def test_the_session_s_failure_comes_through(self):
        with pytest.raises(MeshError, match='the server is down'):
            ran(_support_app.factory(), _support_app.Unopenable(), 'station s1 show', bound=5)


class TestBound:
    def test_a_line_that_does_not_wait(self, tmp_path, chained):
        line = judge(tmp_path, chained, EVERY, 'station s1 show', op=Op.SEE)
        assert bound_of(line, 30) == 30
        assert bound_of(None, 30) == 30  # a line parsed answers itself

    def test_a_line_that_waits_gets_its_own_and_more(self, tmp_path, chained):
        line = judge(tmp_path, chained, EVERY, 'station s1 show --until stock=5 --timeout 60s', op=Op.SEE)
        assert bound_of(line, 30) == 60 + GRACE
        short = judge(tmp_path, chained, EVERY, 'station s1 show --until stock=5 --timeout 2s', op=Op.SEE)
        assert bound_of(short, 30) == 30

    def test_a_watch_with_no_bound_has_none(self, tmp_path, chained):
        line = judge(tmp_path, chained, EVERY, 'station s1 show --watch', op=Op.SEE)
        assert line is not None
        assert (line.wait is not None, bound_of(line, 30)) == (True, None)
