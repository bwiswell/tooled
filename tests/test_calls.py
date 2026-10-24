import pytest
from _support_policy import write

from tooled.calls import arguments
from tooled.catalog import Catalog
from tooled.errors import InvalidRequestError
from tooled.executor import Condition
from tooled.policy import Rules, load
from tooled.tools import TIMEOUT, find

POLICY = """
[bind]
world = "w1"

[inject.token]
env = "DEMO_TOKEN"

[[allow]]
ops = ["read", "query", "invoke"]
keys = ["demo/w1/**"]

[[deny]]
ops = ["query"]
variants = ["dev-*"]

[[deny]]
ops = ["invoke"]
fields = ["move_id"]
"""


@pytest.fixture(scope='module')
def catalog():
    return Catalog.load(['_support_protocol'])


@pytest.fixture
def rules(tmp_path, catalog):
    return Rules(load(write(tmp_path, POLICY)), catalog)


def mapped(rules, name, **given):
    return arguments(find(rules, name), given, rules)


def refused(rules, name, **given):
    with pytest.raises(InvalidRequestError) as exc:
        mapped(rules, name, **given)
    return exc.value


class TestSlots:
    def test_each_given_as_slot_equals_value(self, rules):
        assert mapped(rules, 'fetch_station', station='s1').slots == {'station': 's1'}
        assert mapped(rules, 'fetch_station').slots == {}

    def test_a_bound_slot_passes_through_for_the_verb_to_judge(self, rules):
        assert mapped(rules, 'fetch_station', world='w2').slots == {'world': 'w2'}  # the policy refuses it, exit 3

    def test_a_slot_is_one_value_of_its_type(self, rules):
        error = refused(rules, 'fetch_station', station=['s1', 's2'])
        assert (error.message, error.path) == ("fetch_station: 'station' is a string, not an array", ('station',))
        assert (
            refused(rules, 'fetch_station', station=5).message == "fetch_station: 'station' is a string, not an integer"
        )

    def test_a_required_slot_is_named(self, rules):
        error = refused(rules, 'invoke_move_result', to='dock')
        assert (error.message, error.path) == ("invoke_move_result needs 'player'", ('player',))
        assert refused(rules, 'invoke_move_result').message == "invoke_move_result needs 'player' and 'to'"

    def test_what_the_tool_does_not_take(self, rules):
        error = refused(rules, 'fetch_station', station='s1', stock=3)
        assert (error.message, error.path) == ("fetch_station takes no 'stock'; it takes 'station'", ('stock',))
        assert refused(rules, 'fetch_status', up=True).message == "fetch_status takes no 'up'; it takes none"


class TestRequests:
    def test_the_rest_is_the_request(self, rules):
        found = mapped(rules, 'invoke_move_result', player='ann', to='dock')
        assert (found.slots, found.request) == ({'player': 'ann'}, {'to': 'dock'})

    def test_a_variant_s_tag_is_added(self, rules):
        found = mapped(rules, 'query_look_reply_pong', times=2)
        assert found.request == {'times': 2, 'kind': 'pong'}
        assert mapped(rules, 'query_look_reply_ping', station='s1', kind='ping').request['kind'] == 'ping'

    def test_another_tag_is_refused(self, rules):
        error = refused(rules, 'query_look_reply_ping', station='s1', kind='pong')
        assert error.message == "query_look_reply_ping is variant 'ping', so its 'kind' is 'ping'; leave it out"
        assert error.path == ('kind',)

    def test_another_variant_s_field_is_not_taken(self, rules):
        error = refused(rules, 'query_look_reply_ping', station='s1', times=2)
        assert error.message == "query_look_reply_ping takes no 'times'; it takes 'station', 'player' and 'ask_id'"

    @pytest.mark.parametrize(('name', 'field'), [('invoke_move_result', 'token'), ('invoke_move_result', 'move_id')])
    def test_injected_and_hidden_fields_reach_the_policy(self, rules, name, field):
        found = mapped(rules, name, player='ann', to='dock', **{field: 'x'})
        assert found.request == {'to': 'dock', field: 'x'}  # prepare refuses it with the policy's reason

    def test_seared_judges_the_request_s_values(self, rules):
        assert mapped(rules, 'invoke_move_result', player='ann', to=5).request == {'to': 5}


class TestWaits:
    def test_until_is_conditions_and_timeout_the_bound(self, rules):
        found = mapped(rules, 'wait_station', station='s1', until={'stock': 5, 'closed': 'storm'}, timeout=2)
        assert found.slots == {'station': 's1'}
        assert found.until == (Condition(('stock',), 5, 'stock=5'), Condition(('closed',), 'storm', 'closed=storm'))
        assert found.timeout == 2.0

    def test_the_timeout_defaults(self, rules):
        assert mapped(rules, 'wait_station', until={'stock': 5}).timeout == TIMEOUT

    def test_until_is_required_and_holds_one_condition_at_least(self, rules):
        assert refused(rules, 'wait_station').message == "wait_station needs 'until'"
        error = refused(rules, 'wait_station', until={})
        assert error.message == (
            "wait_station: 'until' is an object of one condition or more, each a field and the value it must equal "
            '({"station": "value"}), not empty'
        )
        assert refused(rules, 'wait_station', until=['stock']).message.endswith('not an array')

    def test_a_field_until_can_not_compare(self, rules):
        error = refused(rules, 'wait_station', until={'stok': 5})
        assert error.message == "wait_station can't wait on 'stok'; 'until' takes 'station', 'stock' and 'closed'"
        assert error.path == ('until', 'stok')

    def test_a_value_of_another_type(self, rules):
        error = refused(rules, 'wait_station', until={'stock': '5'})
        assert (error.message, error.path) == (
            "wait_station: 'until.stock' is an integer, not a string",
            ('until', 'stock'),
        )
        assert mapped(rules, 'wait_station', until={'stock': 5.0}).until[0].value == 5.0  # a whole number is one

    @pytest.mark.parametrize('timeout', [0, -1, 'soon', True, None])
    def test_a_timeout_out_of_range(self, rules, timeout):
        error = refused(rules, 'wait_station', until={'stock': 5}, timeout=timeout)
        assert error.path == ('timeout',)
        assert error.message.startswith("'timeout' is a number of seconds above zero, not ")


class TestListens:
    def test_count_and_timeout_default(self, rules):
        found = mapped(rules, 'listen_notice', player='ann')
        assert (found.slots, found.count, found.timeout) == ({'player': 'ann'}, 1, TIMEOUT)
        assert mapped(rules, 'listen_notice', count=3, timeout=0.5).count == 3

    @pytest.mark.parametrize('count', [0, 1.5, True, '2'])
    def test_a_count_out_of_range(self, rules, count):
        error = refused(rules, 'listen_notice', count=count)
        assert (error.path, error.message.startswith("'count' is a whole number above zero")) == (('count',), True)


class TestEnums:
    def test_one_of_its_values(self, tmp_path):
        import enum
        from typing import ClassVar

        import zeared

        class Mood(enum.StrEnum):
            CALM = 'calm'
            STORMY = 'stormy'

        @zeared.zeared
        class Sky(zeared.Message):
            """The sky, retained."""

            TOPIC: ClassVar[str] = 'sky'
            RETAINED: ClassVar[bool] = True

            mood: Mood = zeared.Enum(enum=Mood, default=Mood.CALM, doc='its mood')

        rules = Rules(load(write(tmp_path, '[[allow]]\nops = ["read"]\n')), Catalog.of([Sky]))
        assert mapped(rules, 'wait_sky', until={'mood': 'stormy'}).until[0].value == 'stormy'
        error = refused(rules, 'wait_sky', until={'mood': 'windy'})
        assert error.message == 'wait_sky: \'until.mood\' is one of "calm", "stormy"; not "windy"'
