import copy

import pytest
import seared
from _support_policy import HEAD, write
from jsonschema import Draft202012Validator
from seared.doc import json_schema

from tooled.catalog import Catalog
from tooled.errors import RefusedError, UsageError
from tooled.policy import Rules, load
from tooled.schemas import (
    ITEMS,
    Walk,
    describe,
    describe_command,
    describe_variant,
    drop_fields,
    drop_variants,
    field_list,
    fold_unions,
    injected,
    listing,
    prune_defs,
    request_schema,
    slot_schemas,
    tag_keys,
    topic,
    variant_classes,
    verbs,
    walk,
)

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


@pytest.fixture(scope='module')
def catalog():
    return Catalog.load(['_support_protocol'])


@pytest.fixture
def rules(tmp_path, catalog):
    return Rules(load(write(tmp_path, POLICY)), catalog)


class TestVerbs:
    @pytest.mark.parametrize(
        ('name', 'expected'),
        [
            ('Station', ('fetch', 'listen', 'wait')),
            ('Status', ('fetch', 'listen', 'wait')),
            ('Notice', ('listen', 'wait')),
            ('LookReply', ('query',)),
            ('MoveResult', ('invoke',)),
            ('EchoReply', ('invoke',)),  # no effect declared: a write, to fail safe
            ('Seen', ()),  # a presence feed: no verb in this version
        ],
    )
    def test_the_verbs_that_act_on_each_contract(self, catalog, name, expected):
        assert verbs(catalog.get(name)) == expected


class TestListing:
    def test_one_line_of_each_shown_contract(self, rules):
        rows = listing(rules)
        assert [row['name'] for row in rows] == [
            'EchoReply',
            'LookReply',
            'MoveResult',
            'Notice',
            'Seen',
            'Station',
            'Status',
            'Trace',
        ]
        (station,) = [row for row in rows if row['name'] == 'Station']
        assert station == {
            'name': 'Station',
            'route': 'wire',
            'kind': 'retained',
            'verbs': ['fetch', 'listen', 'wait'],
            'effect': None,
            'topic': 'demo/w1/station/{station}',
            'slots': ['station'],
            'summary': "A station's state, retained.",
        }

    def test_only_what_the_policy_shows(self, tmp_path, catalog):
        r = Rules(load(write(tmp_path, '[[allow]]\nops = ["query"]\n')), catalog)
        assert [row['name'] for row in listing(r)] == ['LookReply']


class TestSlots:
    def test_bound_slots_show_their_value_and_fields_their_schema(self, rules, catalog):
        assert slot_schemas(catalog.get('Station'), rules) == {
            'world': {'bound': 'w1'},
            'station': {'type': 'string', 'description': "the station's key"},
        }

    def test_a_slot_that_is_no_field_is_a_string(self, rules, catalog):
        assert slot_schemas(catalog.get('Seen'), rules)['player'] == {'type': 'string'}

    def test_a_multi_slot_takes_several_chunks(self, rules, catalog):
        assert slot_schemas(catalog.get('Trace'), rules)['path']['description'] == 'one chunk or more: `a/b`'

    def test_the_topic_has_its_bound_slots_filled(self, rules, catalog):
        assert topic(catalog.get('Trace'), rules) == 'demo/w1/trace/{path**}'


class TestRequest:
    def test_injected_fields_are_taken_out(self, rules, catalog):
        move = catalog.get('MoveResult')
        assert injected(move, rules) == ['token']
        schema = request_schema(move, rules)
        assert schema is not None
        assert list(schema['properties']) == ['to', 'move_id']
        assert schema['required'] == ['to']

    def test_denied_variants_are_taken_out(self, rules, catalog):
        schema = request_schema(catalog.get('LookReply'), rules)
        assert schema is not None
        (envelope,) = schema['allOf']
        assert envelope['properties']['kind']['enum'] == ['ping', 'pong']
        assert len(envelope['allOf']) == 2
        assert set(schema['$defs']) == {'Ping', 'Pong'}

    def test_the_schema_is_valid_and_refuses_what_the_policy_would(self, rules, catalog):
        schema = request_schema(catalog.get('LookReply'), rules)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        assert validator.is_valid({'kind': 'ping', 'station': 's1'})
        assert not validator.is_valid({'kind': 'dev-peek', 'depth': 2})
        assert not validator.is_valid({'kind': 'ping', 'station': 's1', 'token': 't'})  # injected: not given
        assert not validator.is_valid({'kind': 'ping', 'station': 's1', 'extra': 1})

    def test_hidden_fields_are_taken_out(self, tmp_path, catalog):
        body = POLICY + '\n[[deny]]\nops = ["query", "invoke"]\nfields = ["*_id", "times"]\n'
        hiding = Rules(load(write(tmp_path, body)), catalog)
        schema = request_schema(catalog.get('MoveResult'), hiding)
        assert schema is not None
        assert list(schema['properties']) == ['to']
        assert 'ask_id' not in describe(catalog.get('LookReply'), hiding)['request']['properties']
        assert describe_variant(catalog.get('LookReply'), 'pong', hiding)['schema']['properties'] == {}
        assert describe_variant(catalog.get('LookReply'), 'ping', hiding)['schema']['required'] == ['station']

    def test_no_request_no_schema(self, rules, catalog):
        assert request_schema(catalog.get('Station'), rules) is None
        assert injected(catalog.get('Station'), rules) == []
        assert variant_classes(catalog.get('Station')) == []


class TestDescribe:
    def test_a_read_queryable_whole(self, rules, catalog):
        doc = describe(catalog.get('LookReply'), rules)
        assert {k: doc[k] for k in ('name', 'qualname', 'kind', 'op', 'verbs', 'effect', 'topics', 'injected')} == {
            'name': 'LookReply',
            'qualname': '_support_protocol:LookReply',
            'kind': 'queryable',
            'op': 'query',
            'verbs': ['query'],
            'effect': 'read',
            'topics': ['demo/{world}/look/{player}'],
            'injected': ['token'],
        }
        assert doc['variants'] == [
            {'tag': 'ping', 'summary': 'Ask a station how it is.'},
            {'tag': 'pong', 'summary': 'Ask for a number back.'},
        ]
        assert doc['reply'] == [
            {'name': 'world', 'type': 'str', 'doc': "the world's name"},
            {'name': 'player', 'type': 'str', 'doc': "the player's key"},
            {'name': 'seen', 'type': 'str', 'doc': 'what was seen'},
        ]
        (envelope,) = doc['request']['allOf']
        assert 'allOf' not in envelope  # the union folds to its tags
        assert envelope['properties']['kind']['enum'] == ['ping', 'pong']
        assert envelope['description'] == "what; a variant's fields: describe --variant TAG"
        assert '$defs' not in doc['request']
        assert doc['more'] == {
            'variant': "tooled describe LookReply --variant TAG: one variant's fields",
            'reply': "tooled describe LookReply --reply: the reply's whole schema",
        }

    def test_a_retained_class_has_no_request(self, rules, catalog):
        doc = describe(catalog.get('Station'), rules)
        assert 'request' not in doc
        assert 'variants' not in doc
        assert doc['effect'] is None
        assert [field['name'] for field in doc['reply']] == ['world', 'station', 'stock', 'closed']
        assert doc['more'] == {'reply': "tooled describe Station --reply: the reply's whole schema"}

    def test_reply_prints_the_whole_reply_schema(self, rules, catalog):
        doc = describe(catalog.get('Station'), rules, reply=True)
        assert doc['reply']['properties']['stock']['type'] == 'integer'
        assert 'more' not in doc

    def test_one_variant(self, rules, catalog):
        doc = describe_variant(catalog.get('LookReply'), 'ping', rules)
        assert doc['variant'] == 'ping'
        assert doc['schema']['title'] == 'Ping'
        assert doc['schema']['required'] == ['station']

    def test_a_denied_variant_is_refused(self, rules, catalog):
        with pytest.raises(RefusedError, match="denies variant 'dev-peek'"):
            describe_variant(catalog.get('LookReply'), 'dev-peek', rules)

    def test_an_unknown_variant_lists_only_the_shown_tags(self, rules, catalog):
        with pytest.raises(UsageError) as exc:
            describe_variant(catalog.get('LookReply'), 'poke', rules)
        assert exc.value.message == "LookReply has no variant 'poke'; the variants the policy shows: ping, pong"


class TestTagKeys:
    def test_the_root_union_s_tag_key(self, catalog):
        assert tag_keys(catalog.get('LookReply').request) == ['kind']
        assert tag_keys(catalog.get('MoveResult').request) == []


SCHEMA = {
    'type': 'object',
    'properties': {'a': {'type': 'string'}, 'b': {'$ref': '#/$defs/B'}},
    'required': ['a', 'b'],
    '$defs': {'B': {'type': 'object', 'properties': {'c': {'$ref': '#/$defs/C'}}}, 'C': {}, 'Unused': {}},
}


class TestDropping:
    def test_drop_fields_clears_an_empty_required(self):
        schema = copy.deepcopy(SCHEMA)
        drop_fields(schema, ['a', 'b'])
        assert schema['properties'] == {}
        assert 'required' not in schema

    def test_prune_defs_keeps_what_is_reached_transitively(self):
        assert set(prune_defs(copy.deepcopy(SCHEMA))['$defs']) == {'B', 'C'}

    def test_drop_variants_leaves_the_input_alone(self, rules, catalog):
        from seared.doc import json_schema

        schema = json_schema(catalog.get('LookReply').request, mode='load', unknown='raise')
        before = copy.deepcopy(schema)
        dropped = drop_variants(schema, {'ping'})
        assert schema == before
        assert dropped['allOf'][0]['properties']['kind']['enum'] == ['pong', 'dev-peek']
        assert 'Ping' not in dropped['$defs']


class TestLayers:
    def test_field_list_of_a_non_seared_class_is_empty(self):
        assert field_list(int) == []

    def test_field_list_shows_what_a_field_constrains(self, catalog):
        fields = {field['name']: field for field in field_list(catalog.get('Station').cls)}
        assert fields['stock']['constraints'] == {'ge': 0}
        assert fields['closed']['constraints'] == {'max_length': 80}
        assert 'constraints' not in fields['world']

    def test_constraints_keep_seared_s_names_and_order_and_hold_for_each_value(self):
        @seared.seared
        class Gauge(seared.Seared):
            level: float = seared.Float(default=0.5, le=1.0, gt=0.0)
            name: str | None = seared.Str(default=None, pattern='^[a-z]+$', min_length=1)
            readings: list[int] = seared.Int(many=True, default_factory=list, ge=0)
            labels: dict[str, str] = seared.Str(keyed=True, default_factory=dict, max_length=8)

        assert [field.get('constraints') for field in field_list(Gauge)] == [
            {'gt': 0.0, 'le': 1.0},
            {'min_length': 1, 'pattern': '^[a-z]+$'},
            {'ge': 0},
            {'max_length': 8},
        ]

    def test_fold_unions_leaves_its_input_alone_and_keeps_the_tags(self, rules, catalog):
        schema = request_schema(catalog.get('LookReply'), rules)
        assert schema is not None
        before = copy.deepcopy(schema)
        folded = fold_unions(schema)
        assert schema == before
        assert folded['allOf'][0]['properties']['kind']['enum'] == ['ping', 'pong']
        assert 'allOf' not in folded['allOf'][0]


# ---- paths into a reply ------------------------------------------------------------------------------


@seared.seared
class Rate(seared.Seared):
    value: float = seared.Float(required=True)
    rate: float = seared.Float(default=0.0)


@seared.seared
class Start(seared.Seared):
    speed: int = seared.Int(default=1)


@seared.seared
class Stop(seared.Seared):
    why: str = seared.Str(default='')


@seared.seared
class Ship(seared.Seared):
    """Every shape the walker reads: nested, a list, maps, bytes, an optional, a nested union and a flat one."""

    name: str = seared.Str(required=True)
    fuel: Rate | None = seared.T(Rate, default=None)
    cargo: list[Rate] = seared.T(Rate, many=True, default_factory=list)
    numbers: dict[str, float] = seared.Float(keyed=True, default_factory=dict)
    byname: dict[str, Rate] = seared.T(Rate, keyed=True, default_factory=dict)
    logo: bytes | None = seared.Bytes(default=None)
    act: object = seared.Union(variants={'start': Start, 'stop': Stop}, tag_key='action', payload_key='args')
    mode: object = seared.Union(variants={'calm': Rate, 'drift': Stop}, tag_key='kind')


SHIP = json_schema(Ship, mode='dump')


class TestWalk:
    @pytest.mark.parametrize(
        'parts',
        [
            ['name'],
            ['fuel', 'rate'],
            ['cargo', ITEMS, 'value'],
            ['numbers', 'any-key'],  # a map takes any key
            ['byname', 'any-key', 'value'],
            ['logo', 'bytes'],  # the summary tooled prints for bytes
            ['logo', 'sha256'],
            ['action'],  # a union's tag
            ['args', 'speed'],  # a nested envelope: each variant's payload under the same key
            ['args', 'why'],
            ['kind'],
            ['value'],  # a flat envelope: a variant's fields at the root
            ['why'],
        ],
    )
    def test_declared(self, parts):
        assert walk(SHIP, parts).declared is True

    @pytest.mark.parametrize(
        ('parts', 'at', 'names'),
        [
            (['nmae'], 0, {'name', 'fuel', 'cargo', 'numbers', 'byname', 'logo', 'action', 'args', 'kind'}),
            (['fuel', 'rat'], 1, {'value', 'rate'}),
            (['byname', 'x', 'vlue'], 2, {'value', 'rate'}),
            (['args', 'nope'], 1, {'speed', 'why'}),
            (['name', 'deeper'], 1, set()),  # a scalar has nothing further
            (['name', ITEMS], 1, set()),  # nor is it a list
            (['logo', 'size'], 1, {'bytes', 'sha256'}),
        ],
    )
    def test_undeclared(self, parts, at, names):
        found = walk(SHIP, parts)
        assert (found.declared, found.at) == (False, at)
        assert names <= set(found.names)

    def test_a_list_without_its_step_says_it_is_one(self):
        found = walk(SHIP, ['cargo', 'value'])
        assert (found.declared, found.at, found.listed) == (False, 1, True)

    def test_a_whole_number_steps_into_a_list_where_indexes_are_paths(self):
        assert walk(SHIP, ['cargo', '0', 'value'], indexes=True).declared is True
        assert walk(SHIP, ['cargo', '0', 'value']).declared is False

    @pytest.mark.parametrize(
        'schema',
        [
            {'properties': {'x': {}}},  # Any: anything at all
            {'properties': {'x': {'type': 'array', 'prefixItems': [{'type': 'string'}]}}},  # a tuple
        ],
    )
    def test_a_shape_it_cannot_read_is_let_through(self, schema):
        assert walk(schema, ['x', 'y']) == Walk(declared=None, at=1)

    def test_an_optional_written_as_any_of_null(self):
        schema = {
            'properties': {'x': {'anyOf': [{'$ref': '#/$defs/R'}, {'type': 'null'}]}},
            '$defs': {'R': {'properties': {'v': {'type': 'number'}}}},
        }
        assert walk(schema, ['x', 'v']).declared is True
        assert walk(schema, ['x', 'w']).names == ('v',)

    def test_a_real_reply(self, catalog):
        schema = json_schema(catalog.get('Status').cls, mode='dump')
        assert walk(schema, ['logo', 'sha256']).declared is True
        assert walk(schema, ['up']).declared is True


#: A policy over the fixture app: every command's op, the service mounted twice, a slot bound.
TREE = """
[app]
factory = "_support_app:factory"

[bind]
fleet = "f1"

[[allow]]
ops = ["see", "do", "confirm"]
"""


@pytest.fixture(scope='module')
def app_catalog():
    return Catalog.load(['_support_protocol'], factory='_support_app:factory')


@pytest.fixture
def tree(tmp_path, app_catalog):
    return Rules(load(write(tmp_path, TREE)), app_catalog)


class TestCommandListing:
    def test_commands_beside_contracts_each_with_its_route(self, tree):
        rows = {row['name']: row for row in listing(tree)}
        assert list(rows) == [
            'FleetMine',
            'PlayerMove',
            'StationDock',
            'StationMap',
            'StationShow',
            'gateway/Run',
            'gateway/Status',
            'relay/Run',
            'relay/Status',
        ]
        assert rows['relay/Run'] == {
            'name': 'relay/Run',
            'route': 'tree',
            'verbs': ['do'],
            'effect': 'action',
            'phrase': 'relay run',
            'slots': [],
            'summary': "Run the service until it's stopped.",
        }
        assert (rows['FleetMine']['phrase'], rows['FleetMine']['slots'], rows['FleetMine']['effect']) == (
            'fleet {fleet} mine',
            [],  # bound
            None,
        )

    def test_an_interactive_command_names_the_one_to_say_instead(self, tree):
        (shown,) = [row for row in listing(tree) if row['name'] == 'StationMap']
        assert (shown['interactive'], shown['instead']) == (True, 'StationShow')
        assert 'interactive' not in next(row for row in listing(tree) if row['name'] == 'StationShow')


class TestDescribeCommand:
    def test_a_command_whole(self, tree, app_catalog):
        doc = describe_command(app_catalog.get('StationDock'), tree)
        assert {k: doc[k] for k in ('name', 'qualname', 'route', 'op', 'verbs', 'effect', 'phrases', 'keys')} == {
            'name': 'StationDock',
            'qualname': '_support_tree.game:StationDock',
            'route': 'tree',
            'op': 'do',
            'verbs': ['do'],
            'effect': 'action',
            'phrases': ['dock {station}'],
            'keys': ['dock/{station}'],
        }
        assert (doc['asks'], doc['interactive'], doc['instead'], doc['overlaps'], doc['reply']) == (
            True,
            False,
            None,
            [],
            None,
        )
        assert doc['slots'] == {'station': {'type': 'string', 'description': "the station's key", 'x-kind': 'station'}}
        assert doc['arguments']['x-asks'] is True
        assert doc['arguments']['unevaluatedProperties'] is False

    def test_a_bound_slot_is_filled_and_out_of_its_arguments(self, tree, app_catalog):
        doc = describe_command(app_catalog.get('FleetMine'), tree)
        assert doc['slots'] == {'fleet': {'bound': 'f1'}}
        assert doc['phrases'] == ['fleet {fleet} mine', 'mine with {fleet}']
        assert 'fleet' not in doc['arguments']['properties']
        assert 'required' not in doc['arguments']

    def test_its_reply_and_its_contract(self, tree, app_catalog):
        doc = describe_command(app_catalog.get('StationShow'), tree)
        assert doc['overlaps'] == ['Station']
        assert [field['name'] for field in doc['reply']] == ['world', 'station', 'stock', 'closed']
        assert doc['more'] == {'reply': "tooled describe StationShow --reply: the reply's whole schema"}
        whole = describe_command(app_catalog.get('StationShow'), tree, reply=True)
        assert whole['reply']['properties']['stock']['type'] == 'integer'
        assert 'more' not in whole

    def test_a_command_inheriting_a_request(self, tree, app_catalog):
        doc = describe_command(app_catalog.get('PlayerMove'), tree)
        assert doc['overlaps'] == ['MoveResult']
        assert list(doc['arguments']['properties']) == ['token', 'to', 'move_id']

    def test_under_its_mount_s_prefix(self, tree, app_catalog):
        doc = describe_command(app_catalog.get('gateway/Status'), tree)
        assert (doc['phrases'], doc['keys'], doc['op']) == (['gateway status'], ['gateway/status'], 'see')

    def test_no_reply_to_describe_whole(self, tree, app_catalog):
        with pytest.raises(UsageError, match='StationDock declares no REPLY'):
            describe_command(app_catalog.get('StationDock'), tree, reply=True)

    def test_a_contract_names_the_commands_on_it(self, tmp_path, app_catalog):
        head = HEAD + 'route = "wire"\n'
        body = '[[allow]]\nops = ["read"]\n\n[[allow]]\nops = ["see"]\n'
        r = Rules(load(write(tmp_path, body, head=head)), app_catalog)
        assert describe(app_catalog.get('Station'), r)['overlaps'] == ['StationShow']
        assert 'overlaps' not in describe(app_catalog.get('Notice'), r)
