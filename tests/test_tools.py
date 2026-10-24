import enum
import json
import math
from typing import ClassVar

import pytest
import zeared
from _support_policy import write

from tooled.catalog import Catalog
from tooled.errors import PolicyFileError, UsageError
from tooled.output import as_json
from tooled.policy import Rules, load
from tooled.tools import FORMATS, NAME, find, interactive, sizes, spec, tools, untooled

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
"""

#: A policy that shows everything a local catalog has.
EVERYTHING = '[[allow]]\nops = ["read", "query", "invoke"]\n'


@zeared.zeared
class Spot(zeared.Zeared):
    """Where, exactly."""

    x: int = zeared.Int(required=True, ge=0, doc='across')
    y: int = zeared.Int(required=True, doc='down')


@zeared.zeared
class Go(zeared.Zeared):
    """Go somewhere."""

    to: Spot = zeared.T(Spot, required=True, doc='where to')


@zeared.zeared
class Stay(zeared.Zeared):
    """Stay put."""

    note: str | None = zeared.Str(default=None, doc='why')


@zeared.zeared
class Step(zeared.Zeared):
    """One step of a chain."""

    act: object = zeared.Union(variants={'go': Go, 'stay': Stay}, tag_key='verb', doc='the act')


@zeared.zeared
class PlanRequest(zeared.Zeared):
    """A plan: its stops on the way, and its step, nested under `args`."""

    via: list[Spot] = zeared.T(Spot, many=True, default_factory=list, doc='the stops on the way')
    step: object = zeared.Union(variants={'go': Go, 'stay': Stay}, tag_key='verb', payload_key='args', doc='the step')


@zeared.zeared
class PlanResult(zeared.Message):
    """What came of a plan: a write."""

    TOPIC: ClassVar[str] = 'plan/{crew}'
    EFFECT = zeared.Effect.WRITE
    REQUEST: ClassVar[type | None] = PlanRequest

    crew: str = zeared.Str(required=True, doc="the crew's key")


@zeared.zeared
class RouteRequest(zeared.Zeared):
    """A route: a step, then a chain of more."""

    step: object = zeared.Union(variants={'go': Go, 'stay': Stay}, tag_key='verb', doc='the step')
    then: list[Step] = zeared.T(Step, many=True, default_factory=list, doc='the steps after it')


@zeared.zeared
class RouteResult(zeared.Message):
    """What came of a route: a write."""

    TOPIC: ClassVar[str] = 'route/{crew}'
    EFFECT = zeared.Effect.WRITE
    REQUEST: ClassVar[type | None] = RouteRequest

    crew: str = zeared.Str(required=True, doc="the crew's key")


@zeared.zeared
class TryRequest(zeared.Zeared):
    """Steps to try, saving nothing."""

    tries: list[Step] = zeared.T(Step, many=True, required=True, doc='the steps')


@zeared.zeared
class TryReply(zeared.Message):
    """How the steps would go: a read."""

    TOPIC: ClassVar[str] = 'try/{crew}'
    EFFECT = zeared.Effect.READ
    REQUEST: ClassVar[type | None] = TryRequest

    crew: str = zeared.Str(required=True, doc="the crew's key")


@zeared.zeared
class CrewRequest(zeared.Zeared):
    """Ask after a crew."""

    crew: str = zeared.Str(required=True, doc="the crew's key, again")
    detail: bool = zeared.Bool(default=False, doc='whether to say more')


@zeared.zeared
class CrewReply(zeared.Message):
    """A crew, as it is: a read."""

    TOPIC: ClassVar[str] = 'crew/{crew}'
    EFFECT = zeared.Effect.READ
    REQUEST: ClassVar[type | None] = CrewRequest

    crew: str = zeared.Str(required=True, doc="the crew's key")


@zeared.zeared
class TwoRequest(zeared.Zeared):
    """Two unions at once."""

    first: object = zeared.Union(variants={'go': Go}, tag_key='one', doc='the first')
    second: object = zeared.Union(variants={'stay': Stay}, tag_key='two', payload_key='args', doc='the second')


@zeared.zeared
class TwoResult(zeared.Message):
    """Two unions' result."""

    TOPIC: ClassVar[str] = 'two'
    EFFECT = zeared.Effect.WRITE
    REQUEST: ClassVar[type | None] = TwoRequest


@zeared.zeared
class TwinRequest(zeared.Zeared):
    """Two tags one name apart."""

    act: object = zeared.Union(variants={'go-on': Go, 'go_on': Stay}, tag_key='verb', doc='the act')


@zeared.zeared
class TwinResult(zeared.Message):
    """Twins' result."""

    TOPIC: ClassVar[str] = 'twin'
    EFFECT = zeared.Effect.WRITE
    REQUEST: ClassVar[type | None] = TwinRequest


@zeared.zeared
class AVeryLongContractNameThatGoesOnAndOnWellPastWhatAToolsNameMayHold(zeared.Message):
    """Too long to name."""

    TOPIC: ClassVar[str] = 'long'
    RETAINED: ClassVar[bool] = True


class Mode(enum.StrEnum):
    """What a ship is up to."""

    IDLE = 'idle'
    BUSY = 'busy'


@zeared.zeared
class ShipState(zeared.Message):
    """A ship, as it is."""

    TOPIC: ClassVar[str] = 'ship/{ship}'
    RETAINED: ClassVar[bool] = True

    ship: str = zeared.Str(required=True, doc="the ship's key")
    docked: bool = zeared.Bool(default=False, doc='whether it is docked')
    mode: Mode = zeared.Enum(enum=Mode, default=Mode.IDLE, doc='what it is up to')
    crew: list[str] = zeared.Str(many=True, default_factory=list, doc='who is aboard')
    at: Spot | None = zeared.T(Spot, default=None, doc='where it is')


@zeared.zeared
class Odd(zeared.Message):
    """A slot named as a wait's own parameter."""

    TOPIC: ClassVar[str] = 'odd/{until}'
    RETAINED: ClassVar[bool] = True

    until: str = zeared.Str(required=True, doc='when')


@pytest.fixture(scope='module')
def catalog():
    return Catalog.load(['_support_protocol'])


def exported(tmp_path, catalog, body=POLICY):
    return {tool.name: tool for tool in tools(Rules(load(write(tmp_path, body)), catalog))}


def local(tmp_path, *classes, body=EVERYTHING):
    found = Catalog.of(classes)
    return {tool.name: tool for tool in tools(Rules(load(write(tmp_path, body)), found))}


def keywords(node):
    """Every key in a JSON node, at any depth."""
    if isinstance(node, dict):
        return set(node) | {key for value in node.values() for key in keywords(value)}
    if isinstance(node, list):
        return {key for value in node for key in keywords(value)}
    return set()


class TestWhichTools:
    def test_one_for_each_call_and_each_allowed_variant(self, tmp_path, catalog):
        assert list(exported(tmp_path, catalog)) == [
            'fetch_station',
            'fetch_status',
            'invoke_echo_reply',
            'invoke_move_result',
            'listen_notice',
            'listen_trace',
            'query_look_reply_ping',
            'query_look_reply_pong',
            'wait_notice',
            'wait_station',
            'wait_status',
            'wait_trace',
        ]

    def test_each_names_its_call(self, tmp_path, catalog):
        found = exported(tmp_path, catalog)
        assert (found['fetch_station'].verb, found['fetch_station'].variant) == ('fetch', None)
        assert (found['query_look_reply_ping'].verb, found['query_look_reply_ping'].variant) == ('query', 'ping')
        assert found['invoke_move_result'].operation is catalog.get('MoveResult')

    def test_a_retained_contract_is_fetched_and_waited_on_and_a_stream_listened_to(self, tmp_path, catalog):
        found = exported(tmp_path, catalog)
        assert {'fetch_station', 'wait_station'} <= set(found)
        assert 'listen_station' not in found  # a listen would give what the fetch does
        assert {'listen_notice', 'wait_notice'} <= set(found)
        assert 'fetch_notice' not in found

    def test_a_presence_feed_has_none(self, tmp_path, catalog):
        r = Rules(load(write(tmp_path, POLICY)), catalog)
        assert [contract.name for contract in untooled(r)] == ['Seen']

    def test_a_policy_that_shows_nothing_exports_nothing(self, tmp_path, catalog):
        assert exported(tmp_path, catalog, '') == {}


class TestPicks:
    def test_a_verb_keeps_all_of_its_tools(self, tmp_path, catalog):
        r = Rules(load(write(tmp_path, POLICY)), catalog)
        assert [tool.name for tool in tools(r, ['fetch', 'listen'])] == [
            'fetch_station',
            'fetch_status',
            'listen_notice',
            'listen_trace',
        ]

    @pytest.mark.parametrize('pick', ['wait:Station', 'wait:Statio*', 'wait:_support_protocol:Station'])
    def test_a_glob_keeps_its_operations_tools(self, tmp_path, catalog, pick):
        r = Rules(load(write(tmp_path, POLICY)), catalog)
        assert [tool.name for tool in tools(r, ['fetch', pick])] == ['fetch_station', 'fetch_status', 'wait_station']

    def test_a_name_that_names_nothing_is_refused(self, tmp_path, catalog):
        r = Rules(load(write(tmp_path, POLICY)), catalog)
        with pytest.raises(UsageError, match="no operation named 'Statoin'"):
            tools(r, ['wait:Statoin'])

    def test_a_verb_that_is_none_of_them_is_refused(self, tmp_path, catalog):
        r = Rules(load(write(tmp_path, POLICY)), catalog)
        with pytest.raises(UsageError, match="not 'publish'"):
            tools(r, ['publish'])


class TestWaitsAndListens:
    def test_a_wait_s_conditions_are_typed_from_the_value_s_fields(self, tmp_path):
        until = local(tmp_path, ShipState)['wait_ship_state'].parameters['properties']['until']
        assert until['properties'] == {
            'ship': {'type': 'string', 'description': "the ship's key"},
            'docked': {'type': 'boolean', 'description': 'whether it is docked'},
            'mode': {'type': 'string', 'enum': ['idle', 'busy'], 'description': 'what it is up to'},
        }  # crew, a list, and at, an object, hold more than one value
        assert (until['minProperties'], until['additionalProperties']) == (1, False)

    def test_a_wait_takes_its_slots_its_conditions_and_its_bound(self, tmp_path, catalog):
        parameters = exported(tmp_path, catalog)['wait_station'].parameters
        assert list(parameters['properties']) == ['until', 'station', 'timeout']
        assert parameters['required'] == ['until']
        assert parameters['properties']['timeout']['default'] == 30
        assert 'world' not in parameters['properties']['until']['properties']  # a bound slot fixes it

    def test_a_listen_takes_its_slots_a_count_and_its_bound(self, tmp_path, catalog):
        parameters = exported(tmp_path, catalog)['listen_notice'].parameters
        assert list(parameters['properties']) == ['player', 'count', 'timeout']
        assert 'required' not in parameters
        assert parameters['properties']['count'] == {
            'type': 'integer',
            'description': 'how many values to give',
            'default': 1,
            'minimum': 1,
        }

    def test_each_says_what_it_does(self, tmp_path, catalog):
        found = exported(tmp_path, catalog)
        assert found['wait_station'].description == (
            "A station's state, retained. Waits for a value that meets every condition in `until`, up to `timeout`"
            ' seconds, and gives it.'
        )
        assert found['listen_notice'].description.endswith(
            ' Gives the next `count` values as they come, up to `timeout` seconds.'
        )

    def test_a_slot_named_as_one_of_its_parameters_is_refused(self, tmp_path):
        with pytest.raises(PolicyFileError, match="Odd: 'until' is both a slot and another parameter"):
            local(tmp_path, Odd)


class TestParameters:
    def test_a_fetch_takes_its_unbound_slots_each_optional(self, tmp_path, catalog):
        assert exported(tmp_path, catalog)['fetch_station'].parameters == {
            'type': 'object',
            'properties': {'station': {'type': 'string', 'description': "the station's key"}},
            'additionalProperties': False,
        }

    def test_a_write_takes_every_slot_and_its_request_less_what_is_injected(self, tmp_path, catalog):
        parameters = exported(tmp_path, catalog)['invoke_move_result'].parameters
        assert list(parameters['properties']) == ['player', 'to', 'move_id']
        assert parameters['required'] == ['player', 'to']

    def test_a_variant_s_fields_sit_beside_the_request_s_own_without_its_tag(self, tmp_path, catalog):
        parameters = exported(tmp_path, catalog)['query_look_reply_ping'].parameters
        assert list(parameters['properties']) == ['station', 'player', 'ask_id']  # required first
        assert parameters['required'] == ['station']
        assert 'kind' not in parameters['properties']

    def test_a_nested_variant_sits_under_its_payload_key(self, tmp_path):
        found = local(tmp_path, PlanResult)
        go = found['invoke_plan_result_go'].parameters
        assert list(go['properties']) == ['crew', 'args', 'via']
        assert go['required'] == ['crew', 'args']
        assert go['properties']['args'] == {
            'type': 'object',
            'description': 'Go somewhere.',
            'properties': {
                'to': {
                    'type': 'object',
                    'description': 'where to',
                    'properties': {
                        'x': {'type': 'integer', 'description': 'across', 'minimum': 0},
                        'y': {'type': 'integer', 'description': 'down'},
                    },
                    'required': ['x', 'y'],
                    'additionalProperties': False,
                }
            },
            'required': ['to'],
            'additionalProperties': False,
        }
        assert found['invoke_plan_result_stay'].parameters['required'] == ['crew']  # nothing of stay's is required

    def test_references_are_inlined_and_every_object_closed(self, tmp_path):
        parameters = local(tmp_path, PlanResult)['invoke_plan_result_go'].parameters
        assert not keywords(parameters) & {'$ref', '$defs', '$schema', 'title', 'unevaluatedProperties'}
        assert parameters['properties']['via']['items']['additionalProperties'] is False


class TestRefused:
    def test_a_union_in_a_required_field_is_named(self, tmp_path):
        with pytest.raises(PolicyFileError) as exc:
            local(tmp_path, TryReply)
        assert exc.value.message == (
            "TryReply: field 'tries[]' is a union, which a tool's schema can't say; deny TryReply in the policy"
        )

    def test_a_union_in_an_optional_field_names_the_field_to_hide(self, tmp_path):
        with pytest.raises(PolicyFileError) as exc:
            local(tmp_path, RouteResult)
        assert exc.value.message == (
            "RouteResult: field 'then[]' is a union, which a tool's schema can't say; hide 'then' in the policy, "
            'or deny RouteResult'
        )

    def test_a_field_every_variant_shares_is_named_once_with_the_others(self, tmp_path):
        with pytest.raises(PolicyFileError) as exc:
            local(tmp_path, RouteResult, TryReply)
        assert exc.value.message == (
            "2 fields can't be said in a tool's schema; hide or deny them in the policy: RouteResult 'then[]' "
            "(a union; hide 'then'), TryReply 'tries[]' (a union; deny TryReply)"
        )

    def test_denying_it_lets_the_rest_through(self, tmp_path):
        body = EVERYTHING + '\n[[deny]]\nops = ["query"]\noperations = ["TryReply"]\n'
        assert list(local(tmp_path, TryReply, PlanResult, body=body)) == [
            'invoke_plan_result_go',
            'invoke_plan_result_stay',
        ]

    def test_a_slot_that_is_also_a_request_field(self, tmp_path):
        with pytest.raises(PolicyFileError, match="CrewReply: 'crew' is both a slot and another parameter"):
            local(tmp_path, CrewReply)

    def test_binding_the_slot_settles_it(self, tmp_path):
        found = local(tmp_path, CrewReply, body='[bind]\ncrew = "c1"\n\n' + EVERYTHING)
        assert found['query_crew_reply'].parameters['required'] == ['crew']  # the request's own field now

    def test_two_unions_at_a_request_s_root(self, tmp_path):
        with pytest.raises(PolicyFileError, match='TwoResult: its request has 2 unions at its root'):
            local(tmp_path, TwoResult)


class TestHidden:
    def test_hiding_a_field_lets_its_tools_through(self, tmp_path):
        body = EVERYTHING + '\n[[deny]]\nops = ["invoke"]\nfields = ["then"]\n'
        found = local(tmp_path, RouteResult, body=body)
        assert list(found) == ['invoke_route_result_go', 'invoke_route_result_stay']
        assert list(found['invoke_route_result_go'].parameters['properties']) == ['crew', 'to']

    def test_a_variant_s_field(self, tmp_path):
        body = EVERYTHING + '\n[[deny]]\nops = ["invoke"]\nfields = ["then", "note"]\n'
        stay = local(tmp_path, RouteResult, body=body)['invoke_route_result_stay'].parameters
        assert stay == {
            'type': 'object',
            'properties': {'crew': {'type': 'string', 'description': "the crew's key"}},
            'required': ['crew'],
            'additionalProperties': False,
        }

    def test_with_variants_only_their_tools(self, tmp_path):
        body = EVERYTHING + '\n[[deny]]\nops = ["invoke"]\nvariants = ["go"]\nfields = ["via"]\n'
        found = local(tmp_path, PlanResult, body=body)
        assert 'via' not in found['invoke_plan_result_go'].parameters['properties']
        assert 'via' in found['invoke_plan_result_stay'].parameters['properties']

    def test_kept_where_its_keys_cover_only_some_of_the_topic(self, tmp_path):
        body = EVERYTHING + '\n[[deny]]\nops = ["invoke"]\nkeys = ["plan/c1"]\nfields = ["via"]\n'
        found = local(tmp_path, PlanResult, body=body)
        assert 'via' in found['invoke_plan_result_go'].parameters['properties']  # a call at plan/c1 is still refused

    def test_a_query_s_own_field(self, tmp_path, catalog):
        body = POLICY + '\n[[deny]]\nops = ["query"]\nfields = ["ask_id"]\n'
        found = exported(tmp_path, catalog, body)
        assert list(found['query_look_reply_ping'].parameters['properties']) == ['station', 'player']


class TestFind:
    def test_the_tool_tools_gives(self, tmp_path, catalog):
        rules = Rules(load(write(tmp_path, POLICY)), catalog)
        everything = {tool.name: tool for tool in tools(rules)}
        for name in ('fetch_station', 'query_look_reply_pong', 'invoke_move_result', 'wait_notice', 'listen_trace'):
            assert find(rules, name) == everything[name]

    def test_a_name_none_has(self, tmp_path, catalog):
        rules = Rules(load(write(tmp_path, POLICY)), catalog)
        with pytest.raises(UsageError) as exc:
            find(rules, 'fetch_stations')
        assert exc.value.message == (
            "no tool named 'fetch_stations' among those the policy shows; did you mean 'fetch_station' or "
            "'fetch_status' or 'wait_station'?"
        )
        with pytest.raises(UsageError, match="no tool named 'query_look_reply_dev_peek' among"):  # denied: no tool
            find(rules, 'query_look_reply_dev_peek')

    def test_only_its_own_tool_need_be_said(self, tmp_path):
        rules = Rules(load(write(tmp_path, EVERYTHING)), Catalog.of([RouteResult, PlanResult]))
        assert find(rules, 'invoke_plan_result_go').name == 'invoke_plan_result_go'
        with pytest.raises(PolicyFileError, match="RouteResult: field 'then\\[\\]' is a union"):
            find(rules, 'invoke_route_result_go')


class TestNames:
    def test_a_tag_s_dashes_become_underscores(self, tmp_path, catalog):
        body = POLICY.replace('variants = ["dev-*"]', 'variants = ["pong"]')
        assert 'query_look_reply_dev_peek' in exported(tmp_path, catalog, body)

    def test_an_ambiguous_class_is_named_by_its_module(self, tmp_path):
        both = Catalog.load(['_support_protocol', '_support_protocol_two'])
        names = [tool.name for tool in tools(Rules(load(write(tmp_path, '[[allow]]\nops = ["read"]\n')), both))]
        assert {'fetch_support_protocol_station', 'fetch_support_protocol_two_station'} <= set(names)

    def test_every_name_fits(self, tmp_path, catalog):
        assert all(NAME.fullmatch(name) for name in exported(tmp_path, catalog))

    def test_a_name_too_long_is_refused(self, tmp_path):
        with pytest.raises(PolicyFileError, match='longer than 64 characters'):
            local(tmp_path, AVeryLongContractNameThatGoesOnAndOnWellPastWhatAToolsNameMayHold)

    def test_two_tools_of_one_name_are_refused(self, tmp_path):
        with pytest.raises(PolicyFileError, match="two tools would be named 'invoke_twin_result_go_on'"):
            local(tmp_path, TwinResult)


class TestDescriptions:
    @pytest.mark.parametrize(
        ('name', 'description'),
        [
            ('query_look_reply_ping', 'Ask a station how it is.'),  # a variant's
            ('invoke_move_result', "A move, with the player's token."),  # a write's request's
            ('fetch_station', "A station's state, retained."),  # the contract's
        ],
    )
    def test_the_docstring_that_says_most(self, tmp_path, catalog, name, description):
        assert exported(tmp_path, catalog)[name].description == description

    def test_a_read_says_what_it_gives(self, tmp_path):
        found = local(tmp_path, CrewReply, body='[bind]\ncrew = "c1"\n\n' + EVERYTHING)
        assert found['query_crew_reply'].description == 'A crew, as it is: a read.'


class TestForms:
    def test_each_form(self, tmp_path, catalog):
        tool = exported(tmp_path, catalog)['fetch_status']
        empty = {'type': 'object', 'properties': {}, 'additionalProperties': False}
        about = "The world's status, retained, and its presence."
        assert spec(tool, 'openai') == {
            'type': 'function',
            'function': {'name': 'fetch_status', 'description': about, 'parameters': empty},
        }
        assert spec(tool, 'anthropic') == {'name': 'fetch_status', 'description': about, 'input_schema': empty}
        assert spec(tool, 'mcp') == {'name': 'fetch_status', 'description': about, 'inputSchema': empty}

    @pytest.mark.parametrize('form', FORMATS)
    def test_byte_stable(self, tmp_path, catalog, form):
        first = as_json([spec(tool, form) for tool in exported(tmp_path, catalog).values()], indent=None)
        again = as_json([spec(tool, form) for tool in exported(tmp_path, catalog).values()], indent=None)
        assert first == again
        assert [tool['name'] for tool in json.loads(first) if 'name' in tool] == sorted(
            tool['name'] for tool in json.loads(first) if 'name' in tool
        )


class TestSizes:
    @pytest.mark.parametrize('form', FORMATS)
    def test_bytes_and_tokens_as_printed(self, tmp_path, catalog, form):
        found = list(exported(tmp_path, catalog).values())
        out = sizes(found, form)
        assert out['format'] == form
        for tool, row in zip(found, out['tools'], strict=True):
            size = len(as_json(spec(tool, form), indent=None).encode())
            assert row == {'name': tool.name, 'bytes': size, 'tokens': math.ceil(size / 4)}
        assert out['total'] == {
            'tools': len(found),
            'bytes': sum(row['bytes'] for row in out['tools']),
            'tokens': sum(row['tokens'] for row in out['tools']),
        }


#: A policy over the chained fixture app: every tree op, a slot bound, the gateway's writes denied by key.
TREE_POLICY = """
[protocol]

[app]
factory = "_support_app:chained"

[bind]
fleet = "f1"

[[allow]]
ops = ["see", "do", "confirm"]

[[deny]]
ops = ["do"]
keys = ["gateway/**"]
"""


def tree_tools(tmp_path, body=TREE_POLICY):
    rules = Rules(load(write(tmp_path, head=body)), Catalog.load([], factory='_support_app:chained'))
    return rules, {tool.name: tool for tool in tools(rules)}


class TestCommandTools:
    def test_one_for_each_command_and_chain_the_policy_shows(self, tmp_path):
        _, found = tree_tools(tmp_path)
        assert list(found) == [
            'do_fleet_mine',
            'do_orders_fleet_order',
            'do_player_move',
            'do_relay_run',
            'do_station_dock',
            'see_gateway_status',
            'see_relay_status',
            'see_station_show',
            'wait_station_show',
        ]  # no step alone, no interactive command, no write the gateway's keys deny; a wait for a read with a reply
        assert (found['see_station_show'].verb, found['do_orders_fleet_order'].chain.__name__) == ('see', 'FleetOrder')

    def test_parsed_s_schema_in_the_dialect(self, tmp_path):
        _, found = tree_tools(tmp_path)
        assert found['see_station_show'].parameters == {
            'type': 'object',
            'properties': {'station': {'type': 'string', 'description': "the station's key"}},
            'required': ['station'],
            'additionalProperties': False,
        }
        assert found['see_station_show'].description == "Show a station: its stock, and why it's closed when it is."
        assert found['do_fleet_mine'].parameters['properties'] == {}  # its one slot, bound

    def test_asking_first_takes_confirm_and_says_so(self, tmp_path):
        _, found = tree_tools(tmp_path)
        dock = found['do_station_dock']
        assert dock.parameters['properties'] == {
            'station': {'type': 'string', 'description': "the station's key (a station)"},  # its x-kind, folded
            'confirm': {
                'type': 'boolean',
                'description': 'answer its question yes, where the policy allows; left out, the question comes back, '
                'nothing done',
                'default': False,
            },
        }
        assert dock.description.endswith('It may ask before it acts: `confirm` answers yes, where the policy allows.')
        assert not {key for key in keywords(dock.parameters) if key.startswith('x-')}

    def test_a_chain_is_one_tool_of_the_steps_shown(self, tmp_path):
        _, found = tree_tools(tmp_path)
        chain = found['do_orders_fleet_order']
        steps = chain.parameters['properties']['steps']
        assert [branch['properties']['step']['const'] for branch in steps['items']['anyOf']] == ['dock', 'jump']
        assert 'fleet' not in chain.parameters['properties']  # shared, and bound
        assert chain.description.endswith('Its `steps` go in order, each tagged by `step`: dock, jump.')
        body = TREE_POLICY + '\n[[deny]]\nops = ["do"]\nkeys = ["orders/fleet/*/dock/*"]\n'
        _, found = tree_tools(tmp_path, body)
        steps = found['do_orders_fleet_order'].parameters['properties']['steps']
        assert [branch['properties']['step']['const'] for branch in steps['items']['anyOf']] == ['jump']

    def test_a_read_with_a_reply_waits(self, tmp_path):
        _, found = tree_tools(tmp_path)
        wait = found['wait_station_show']
        assert (wait.verb, wait.operation.name) == ('wait', 'StationShow')
        assert list(wait.parameters['properties']) == ['station', 'until', 'timeout']
        assert wait.parameters['required'] == ['station', 'until']
        until = wait.parameters['properties']['until']
        assert (list(until['properties']), until['minProperties']) == (['world', 'station', 'stock', 'closed'], 1)
        assert until['properties']['stock'] == {'type': 'integer', 'minimum': 0, 'description': 'units in stock'}
        assert wait.description.endswith(
            'Waits for a value that meets every condition in `until`, up to `timeout` seconds, and gives it.'
        )
        assert 'wait_relay_status' not in found  # a read with no reply has nothing to wait on

    def test_interactive_commands_are_named_not_exported(self, tmp_path):
        rules, _ = tree_tools(tmp_path)
        assert [command.name for command in interactive(rules)] == ['StationMap']

    def test_found_by_name(self, tmp_path):
        rules, found = tree_tools(tmp_path)
        assert find(rules, 'do_orders_fleet_order') == found['do_orders_fleet_order']
        assert find(rules, 'see_relay_status').operation.name == 'relay/Status'

    def test_picks_take_the_tree_s_verbs(self, tmp_path):
        rules, _ = tree_tools(tmp_path)
        assert [tool.name for tool in tools(rules, ['see:relay/*', 'do:Station*'])] == [
            'do_station_dock',
            'see_relay_status',
        ]


class TestTaggedUnions:
    def test_a_tagged_any_of_is_said(self):
        from tooled.tools import _Dialect

        node = {
            'anyOf': [
                {'type': 'object', 'properties': {'step': {'const': 'a'}}, 'unevaluatedProperties': False},
                {'type': 'object', 'properties': {'step': {'const': 'b'}, 'to': {'type': 'string', 'x-follows': True}}},
            ]
        }
        assert _Dialect({}, 'X').portable(node, 'steps') == {
            'anyOf': [
                {'type': 'object', 'properties': {'step': {'const': 'a'}}, 'additionalProperties': False},
                {
                    'type': 'object',
                    'properties': {'step': {'const': 'b'}, 'to': {'type': 'string'}},  # its x- key left out
                    'additionalProperties': False,
                },
            ]
        }

    @pytest.mark.parametrize(
        'node',
        [
            {'anyOf': [{'type': 'string'}, {'type': 'integer'}]},  # untagged
            {'anyOf': [{'type': 'object', 'properties': {'step': {'const': 'a'}}}], 'allOf': [{}]},
        ],
    )
    def test_any_other_union_is_not(self, node):
        from tooled.tools import _Dialect, _Unsayable

        with pytest.raises(_Unsayable):
            _Dialect({}, 'X').portable(node, 'steps')
