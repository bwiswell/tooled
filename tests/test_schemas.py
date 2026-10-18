import copy

import pytest
from _support_policy import write
from jsonschema import Draft202012Validator

from parsed.catalog import Catalog
from parsed.errors import RefusedError, UsageError
from parsed.policy import Rules, load
from parsed.schemas import (
    describe,
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
            'kind': 'retained',
            'verbs': ['fetch', 'listen', 'wait'],
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
            'variant': "parsed describe LookReply --variant TAG: one variant's fields",
            'reply': "parsed describe LookReply --reply: the reply's whole schema",
        }

    def test_a_retained_class_has_no_request(self, rules, catalog):
        doc = describe(catalog.get('Station'), rules)
        assert 'request' not in doc
        assert 'variants' not in doc
        assert doc['effect'] is None
        assert [field['name'] for field in doc['reply']] == ['world', 'station', 'stock']
        assert doc['more'] == {'reply': "parsed describe Station --reply: the reply's whole schema"}

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

    def test_fold_unions_leaves_its_input_alone_and_keeps_the_tags(self, rules, catalog):
        schema = request_schema(catalog.get('LookReply'), rules)
        assert schema is not None
        before = copy.deepcopy(schema)
        folded = fold_unions(schema)
        assert schema == before
        assert folded['allOf'][0]['properties']['kind']['enum'] == ['ping', 'pong']
        assert 'allOf' not in folded['allOf'][0]
