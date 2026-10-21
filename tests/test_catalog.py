import pytest
from zeared import Effect

from tooled.catalog import Catalog, Kind, read, render, union_tags, wire_names
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
        with pytest.raises(UsageError, match="no contract named 'Stations'"):
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
