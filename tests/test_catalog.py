from dataclasses import dataclass

import parsed
import pytest
import zeared
from _support_protocol import MoveRequest, Station
from zeared import Effect

from tooled.catalog import (
    Catalog,
    Command,
    Kind,
    commands_of,
    made_app,
    read,
    render,
    top_fields,
    union_tags,
    wire_names,
)
from tooled.errors import CatalogError, UsageError


@pytest.fixture(scope='module')
def catalog():
    return Catalog.load(['_support_protocol'])


@pytest.fixture(scope='module')
def both():
    return Catalog.load(['_support_protocol', '_support_protocol_two'])


class TestKinds:
    @pytest.mark.parametrize(
        ('name', 'kind'),
        [
            ('Station', Kind.RETAINED),
            ('Status', Kind.RETAINED),  # retained and liveliness: retained wins
            ('Notice', Kind.STREAM),
            ('Trace', Kind.STREAM),
            ('Seen', Kind.LIVELINESS),
            ('LookReply', Kind.QUERYABLE),
            ('MoveResult', Kind.QUERYABLE),
            ('EchoReply', Kind.QUERYABLE),  # a REQUEST with no EFFECT is still a queryable
        ],
    )
    def test_each_contract_is_sorted_by_what_it_is(self, catalog, name, kind):
        assert catalog.get(name).kind is kind

    def test_only_message_classes_are_contracts(self, catalog):
        names = [c.name for c in catalog.contracts]
        assert 'LookRequest' not in names
        assert 'Ping' not in names
        assert names == sorted(names)

    def test_effect_is_as_declared_or_none(self, catalog):
        assert catalog.get('LookReply').effect is Effect.READ
        assert catalog.get('MoveResult').effect is Effect.WRITE
        assert catalog.get('EchoReply').effect is None

    def test_an_effect_that_is_no_effect_is_refused(self):
        with pytest.raises(CatalogError, match='sideways'):
            Catalog.load(['_support_protocol_broken'])


class TestContract:
    def test_templates_slots_and_request(self, catalog):
        look = catalog.get('LookReply')
        assert look.templates == ('demo/{world}/look/{player}',)
        assert look.slot_names == ('world', 'player')
        assert look.request is not None
        assert look.request.__name__ == 'LookRequest'
        assert look.qualname == '_support_protocol:LookReply'

    def test_a_trailing_multi_slot_is_marked(self, catalog):
        (slot,) = [s for s in catalog.get('Trace').slots if s.name == 'path']
        assert slot.multi

    def test_variants_are_the_root_unions_tags(self, catalog):
        assert catalog.get('LookReply').variants == ('ping', 'pong', 'dev-peek')
        assert catalog.get('MoveResult').variants == ()
        assert catalog.get('Station').variants == ()

    def test_read_takes_one_class(self, catalog):
        assert read(catalog.get('Station').cls) == catalog.get('Station')


class TestNames:
    def test_by_class_name_or_qualname(self, catalog):
        assert catalog.get('_support_protocol:Station') is catalog.get('Station')

    def test_an_unknown_name_is_a_usage_error(self, catalog):
        with pytest.raises(UsageError, match="no operation named 'Stations'"):
            catalog.get('Stations')
        with pytest.raises(UsageError):
            catalog.get('nowhere:Station')

    def test_a_shared_name_is_ambiguous_but_qualnames_work(self, both):
        with pytest.raises(UsageError, match='ambiguous') as exc:
            both.get('Station')
        assert '_support_protocol:Station' in exc.value.message
        assert '_support_protocol_two:Station' in exc.value.message
        assert both.get('_support_protocol_two:Station').templates == ('other/{world}/station/{station}',)

    def test_labels_are_bare_unless_ambiguous(self, catalog, both):
        assert catalog.label(catalog.get('Station')) == 'Station'
        two = both.get('_support_protocol_two:Station')
        assert both.label(two) == '_support_protocol_two:Station'
        assert both.label(both.get('Notice')) == 'Notice'

    def test_globs_match_names_and_qualnames(self, catalog):
        assert [c.name for c in catalog.matching('*Reply')] == ['EchoReply', 'LookReply']
        assert [c.name for c in catalog.matching('_support_protocol:S*')] == ['Seen', 'Station', 'Status']
        assert catalog.matching('Nothing*') == []

    def test_slot_names_and_request_fields(self, catalog):
        assert catalog.slot_names == {'world', 'station', 'player', 'path'}
        assert catalog.request_fields == {'token', 'ask_id', 'to', 'move_id', 'text'}  # a flat union adds no wire name


@dataclass(frozen=True)
class Stand:
    """An operation that isn't a contract, as a command read from a tree is to be."""

    name: str
    qualname: str


class TestOperations:
    def test_every_contract_is_an_operation(self, catalog):
        assert catalog.operations == catalog.contracts

    def test_lookups_labels_and_globs_serve_any_operation(self, catalog):
        mine = Stand('Mine', 'game:Mine')
        mixed = Catalog([*catalog.contracts, mine, Stand('Station', 'game:Station')])
        assert mixed.get('Mine') is mine
        assert mixed.get('game:Mine') is mine
        assert mixed.label(mine) == 'Mine'
        assert mixed.label(mixed.get('game:Station')) == 'game:Station'  # it shares a contract's name
        assert [o.qualname for o in mixed.matching('M*')] == ['game:Mine', '_support_protocol:MoveResult']
        assert mine in mixed.operations
        assert mine not in mixed.contracts

    def test_a_contract_lookup_refuses_another_operation(self, catalog):
        mixed = Catalog([*catalog.contracts, Stand('Mine', 'game:Mine')])
        assert mixed.contract('Station') is catalog.get('Station')
        with pytest.raises(UsageError, match='Mine is not a contract'):
            mixed.contract('Mine')

    def test_each_qualname_is_kept_once(self, catalog):
        station = catalog.get('Station')
        assert Catalog([station, read(station.cls)]).operations == (station,)


class TestLoad:
    def test_a_package_that_will_not_import_is_refused_with_its_place(self):
        with pytest.raises(CatalogError) as exc:
            Catalog.load(['_support_protocol', 'no_such_protocol'])
        assert exc.value.path == ('protocol', 'packages', 1)
        assert 'no_such_protocol' in exc.value.message


class TestRender:
    @pytest.mark.parametrize(
        ('template', 'values', 'key'),
        [
            ('demo/{world}/station/{station}', {}, 'demo/*/station/*'),
            ('demo/{world}/station/{station}', {'world': 'w1'}, 'demo/w1/station/*'),
            ('demo/{world}/station/{station}', {'world': 'w1', 'station': 's2'}, 'demo/w1/station/s2'),
            ('demo/{world}/trace/{path**}', {}, 'demo/*/trace/*/**'),
            ('demo/{world}/trace/{path**}', {'path': 'a/b'}, 'demo/*/trace/a/b'),
            ('demo/*/literal/{x}', {}, 'demo/*/literal/*'),
        ],
    )
    def test_slots_fill_or_widen(self, template, values, key):
        assert render(template, values) == key


class TestHelpers:
    def test_union_tags_and_wire_names_of_a_non_seared_class_are_empty(self):
        assert union_tags(int) == ()
        assert wire_names(int) == ()
        assert top_fields(int) == {}

    def test_top_fields_own_then_each_flat_variant_s(self, catalog):
        assert top_fields(catalog.contract('LookReply').request) == {
            None: {'token': True, 'ask_id': False},  # the tag, kind, is no field
            'ping': {'station': True},
            'pong': {'times': False},
            'dev-peek': {'depth': False},
        }


@pytest.fixture(scope='module')
def app_catalog():
    return Catalog.load(['_support_protocol'], factory='_support_app:factory')


class TestCommands:
    def test_an_app_s_mounts_each_lead_their_commands(self, app_catalog):
        found = {command.name: command for command in app_catalog.commands}
        assert list(found) == [
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
        run = found['relay/Run']
        assert (run.qualname, run.under, run.keys) == ('relay/_support_tree.service:Run', 'relay', ('relay/run',))
        assert found['gateway/Run'].cls is run.cls  # one package mounted twice: one class, two operations

    def test_a_command_s_keys_and_slots(self, app_catalog):
        mine = app_catalog.get('FleetMine')
        assert isinstance(mine, Command)
        assert (mine.keys, mine.slot_names, mine.under) == (('fleet/{fleet}/mine', 'mine/with/{fleet}'), ('fleet',), '')

    def test_its_effect_in_zeared_s_terms(self, app_catalog):
        effects = {command.name: command.effect for command in app_catalog.commands}
        assert effects['StationShow'] is Effect.READ
        assert effects['PlayerMove'] is Effect.WRITE
        assert effects['relay/Run'] is Effect.ACTION
        assert effects['FleetMine'] is None  # undeclared: never inferred

    def test_what_parsed_says_of_it_is_kept(self, app_catalog):
        assert app_catalog.get('PlayerMove').doc.fields_from == (MoveRequest,)
        assert app_catalog.get('StationShow').doc.reply is Station
        shown = app_catalog.get('StationMap').doc
        assert (shown.interactive, shown.instead) == (True, app_catalog.get('StationShow').cls)

    def test_beside_the_contracts_and_named_alike(self, app_catalog):
        assert (len(app_catalog.contracts), len(app_catalog.commands)) == (8, 9)
        assert (
            app_catalog.label(app_catalog.get('Status')) == 'Status'
        )  # the contract: a command's is led by its prefix
        assert app_catalog.get('relay/_support_tree.service:Status').name == 'relay/Status'
        assert [o.name for o in app_catalog.matching('*/Run')] == ['gateway/Run', 'relay/Run']
        assert [o.name for o in app_catalog.matching('relay/*')] == ['relay/Run', 'relay/Status']

    def test_a_contract_lookup_tells_a_command_apart(self, app_catalog):
        with pytest.raises(UsageError, match='relay/Run is not a contract'):
            app_catalog.contract('relay/Run')

    def test_a_prefix_of_several_words(self):
        (run, _status) = Catalog.load([], factory='_support_app:nested').commands
        assert (run.name, run.keys, run.under) == ('mesh/relay/Run', ('mesh/relay/run',), 'mesh relay')

    def test_trees_read_at_the_root(self):
        found = Catalog.load([], trees=['_support_tree.service']).commands
        assert [(command.name, command.keys) for command in found] == [('Run', ('run',)), ('Status', ('status',))]

    def test_commands_of_a_tree(self):
        tree = parsed.Tree.collect('_support_tree.service', under='ops')
        assert [command.qualname for command in commands_of(tree)] == [
            'ops/_support_tree.service:Run',
            'ops/_support_tree.service:Status',
        ]


class TestCommandSources:
    @pytest.mark.parametrize(
        ('factory', 'message'),
        [
            ('_support_nowhere:factory', "would not load: ModuleNotFoundError: No module named '_support_nowhere'"),
            ('_support_app:missing', "would not load: AttributeError: module '_support_app' has no attribute"),
            ('_support_app:NOT_CALLABLE', 'is no function, but str'),
            ('_support_app:failing', 'failed: RuntimeError: no app today'),
            ('_support_app:not_an_app', "returned int, not parsed's App"),
        ],
    )
    def test_a_factory_that_gives_no_app(self, factory, message):
        with pytest.raises(CatalogError, match=message) as exc:
            made_app(factory)
        assert exc.value.path == ('app', 'factory')
        assert exc.value.message.startswith(f'[app] factory {factory!r} ')

    @pytest.mark.parametrize(
        ('tree', 'message'),
        [
            ('_support_nowhere', "command tree '_support_nowhere' would not load: ModuleNotFoundError"),
            ('_support_tree_clash', "command tree '_support_tree_clash' would not load: DeclarationError"),
        ],
    )
    def test_a_tree_that_does_not_load(self, tree, message):
        with pytest.raises(CatalogError, match=message) as exc:
            Catalog.load([], trees=['_support_tree.game', tree])
        assert exc.value.path == ('protocol', 'trees', 1)


def test_parsed_s_effects_are_zeared_s_value_for_value():
    """A command and a contract are judged on one scale: parsed keeps its own copy of zeared's vocabulary."""
    assert [member.value for member in parsed.Effect] == [member.value for member in zeared.Effect]


class TestLinks:
    def test_a_command_on_a_contract_s_request_or_reply(self, app_catalog):
        assert [(link.command.name, link.contract.name, link.by) for link in app_catalog.links] == [
            ('PlayerMove', 'MoveResult', ('request',)),
            ('StationShow', 'Station', ('reply',)),
        ]

    def test_none_without_the_protocol(self):
        assert Catalog.load([], factory='_support_app:factory').links == ()
