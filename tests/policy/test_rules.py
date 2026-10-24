from typing import ClassVar

import pytest
import zeared
from _support_policy import HEAD, write
from _support_tree import game

from tooled.catalog import Catalog
from tooled.errors import PolicyFileError, RefusedError
from tooled.policy.config import Op, load
from tooled.policy.rules import Rules, op_of


@pytest.fixture(scope='module')
def catalog():
    return Catalog.load(['_support_protocol'])


def rules(tmp_path, catalog, body):
    return Rules(load(write(tmp_path, body)), catalog)


def shown(r):
    return [c.name for c in r.visible_contracts()]


class TestOpOf:
    @pytest.mark.parametrize(
        ('name', 'op'),
        [
            ('Station', Op.READ),
            ('Notice', Op.READ),
            ('Seen', Op.READ),
            ('LookReply', Op.QUERY),
            ('MoveResult', Op.INVOKE),
            ('EchoReply', Op.INVOKE),  # no EFFECT: counted as mutating, so the default fails safe
        ],
    )
    def test_each_kind_takes_one_operation(self, catalog, name, op):
        assert op_of(catalog.get(name)) is op


class TestVisible:
    def test_nothing_is_shown_until_a_rule_allows_it(self, tmp_path, catalog):
        assert shown(rules(tmp_path, catalog, '')) == []

    def test_an_allow_shows_what_its_operation_and_keys_reach(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, '[[allow]]\nops = ["read"]\nkeys = ["demo/**"]\n')
        assert shown(r) == ['Notice', 'Seen', 'Station', 'Status', 'Trace']

    def test_keys_that_only_intersect_still_show(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, '[[allow]]\nops = ["read"]\nkeys = ["demo/w1/station/s1"]\n')
        assert shown(r) == ['Station']

    def test_a_deny_covering_every_topic_hides(self, tmp_path, catalog):
        body = '[[allow]]\nops = ["read"]\n\n[[deny]]\nops = ["read"]\nkeys = ["demo/*/station/**"]\n'
        assert 'Station' not in shown(rules(tmp_path, catalog, body))

    def test_a_deny_covering_part_keeps_it_shown(self, tmp_path, catalog):
        body = '[[allow]]\nops = ["read"]\n\n[[deny]]\nops = ["read"]\nkeys = ["demo/w1/station/secret"]\n'
        assert 'Station' in shown(rules(tmp_path, catalog, body))

    def test_bind_narrows_what_a_contract_reaches(self, tmp_path, catalog):
        body = '[bind]\nworld = "w2"\n\n[[allow]]\nops = ["read"]\nkeys = ["demo/w1/**"]\n'
        assert shown(rules(tmp_path, catalog, body)) == []

    def test_contract_globs(self, tmp_path, catalog):
        body = '[[allow]]\nops = ["read", "query", "invoke"]\noperations = ["*Reply", "Station"]\n'
        assert shown(rules(tmp_path, catalog, body)) == ['EchoReply', 'LookReply', 'Station']

    def test_a_deny_with_variants_hides_only_those_variants(self, tmp_path, catalog):
        body = '[[allow]]\nops = ["query"]\n\n[[deny]]\nops = ["query"]\nvariants = ["dev-*"]\n'
        r = rules(tmp_path, catalog, body)
        assert shown(r) == ['LookReply']
        assert r.allowed_variants(catalog.get('LookReply')) == ('ping', 'pong')

    def test_an_allow_with_variants_shows_only_contracts_with_them(self, tmp_path, catalog):
        body = '[[allow]]\nops = ["query", "read"]\nvariants = ["p*"]\n'
        r = rules(tmp_path, catalog, body)
        assert shown(r) == ['LookReply']
        assert r.allowed_variants(catalog.get('LookReply')) == ('ping', 'pong')


class TestCheck:
    @pytest.fixture
    def r(self, tmp_path, catalog):
        return rules(
            tmp_path,
            catalog,
            """
            [[allow]]
            ops = ["read", "query"]
            keys = ["demo/w1/**"]

            [[deny]]
            ops = ["read"]
            keys = ["demo/w1/station/secret"]

            [[deny]]
            ops = ["query"]
            variants = ["dev-*"]
            """,
        )

    def test_allowed_when_an_allow_includes_the_whole_selector(self, r, catalog):
        r.check(catalog.get('Station'), 'demo/w1/station/s1')
        r.check(catalog.get('Notice'), 'demo/w1/notice/*')

    def test_refused_when_no_allow_includes_it(self, r, catalog):
        with pytest.raises(RefusedError, match='no allow rule covers read on Station at demo/\\*/station/s1'):
            r.check(catalog.get('Station'), 'demo/*/station/s1')

    def test_refused_when_a_deny_intersects_it(self, r, catalog):
        with pytest.raises(RefusedError, match=r'denied by deny\[0\]; narrow the slots'):
            r.check(catalog.get('Station'), 'demo/w1/station/*')
        r.check(catalog.get('Station'), 'demo/w1/station/open')

    def test_a_denied_variant(self, r, catalog):
        look = catalog.get('LookReply')
        r.check(look, 'demo/w1/look/ann', variant='ping')
        with pytest.raises(RefusedError, match="variant 'dev-peek' is denied by deny\\[1\\]"):
            r.check(look, 'demo/w1/look/ann', variant='dev-peek')

    def test_an_operation_no_rule_names(self, r, catalog):
        with pytest.raises(RefusedError, match='no allow rule covers invoke on MoveResult'):
            r.check(catalog.get('MoveResult'), 'demo/w1/move/ann')

    def test_an_allow_with_variants_allows_only_those(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, '[[allow]]\nops = ["query"]\nvariants = ["ping"]\n')
        look = catalog.get('LookReply')
        r.check(look, 'demo/w1/look/ann', variant='ping')
        with pytest.raises(RefusedError):
            r.check(look, 'demo/w1/look/ann', variant='pong')
        with pytest.raises(RefusedError):
            r.check(look, 'demo/w1/look/ann')


class TestHidden:
    ALLOW = '[[allow]]\nops = ["query", "invoke"]\n\n'

    def test_a_deny_with_fields_hides_no_contract_and_no_variant(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, self.ALLOW + '[[deny]]\nops = ["query"]\nvariants = ["pong"]\nfields = ["*"]\n')
        assert shown(r) == ['EchoReply', 'LookReply', 'MoveResult']
        assert r.allowed_variants(catalog.get('LookReply')) == ('ping', 'pong', 'dev-peek')

    def test_optional_root_fields_its_globs_match(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, self.ALLOW + '[[deny]]\nops = ["query", "invoke"]\nfields = ["*_id"]\n')
        assert r.hidden(catalog.get('MoveResult')) == ('move_id',)
        assert r.hidden(catalog.get('LookReply'), 'ping') == ('ask_id',)
        assert r.hidden(catalog.get('EchoReply')) == ()

    def test_a_glob_passes_over_required_fields(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, self.ALLOW + '[[deny]]\nops = ["query"]\nfields = ["*"]\n')
        look = catalog.get('LookReply')
        assert r.hidden(look) == ('ask_id',)  # not token, which a load requires
        assert r.hidden(look, 'pong') == ('ask_id', 'times')
        assert r.hidden(look, 'ping') == ('ask_id',)  # station is required

    def test_with_variants_only_their_calls(self, tmp_path, catalog):
        body = self.ALLOW + '[[deny]]\nops = ["query"]\nvariants = ["pong"]\nfields = ["ask_id", "times"]\n'
        r = rules(tmp_path, catalog, body)
        look = catalog.get('LookReply')
        assert r.hidden(look, 'pong') == ('ask_id', 'times')
        assert r.hidden(look, 'ping') == ()
        assert r.hidden(look) == ()

    def test_only_where_its_keys_cover_every_topic(self, tmp_path, catalog):
        body = self.ALLOW + '[[deny]]\nops = ["invoke"]\nkeys = ["demo/w1/move/ann"]\nfields = ["move_id"]\n'
        assert rules(tmp_path, catalog, body).hidden(catalog.get('MoveResult')) == ()

    def test_a_call_that_gives_one_is_refused(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, self.ALLOW + '[[deny]]\nops = ["invoke"]\nfields = ["move_id"]\n')
        move = catalog.get('MoveResult')
        r.check(move, 'demo/w1/move/ann', fields=['to'])
        with pytest.raises(RefusedError) as exc:
            r.check(move, 'demo/w1/move/ann', fields=['to', 'move_id'])
        assert exc.value.message == "'move_id' is hidden by the policy (deny[0]); leave it out of the request"
        assert exc.value.path == ('move_id',)

    def test_every_one_it_gives_is_named(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, self.ALLOW + '[[deny]]\nops = ["query"]\nfields = ["*"]\n')
        with pytest.raises(RefusedError) as exc:
            r.check(catalog.get('LookReply'), 'demo/w1/look/ann', variant='pong', fields=['kind', 'times', 'ask_id'])
        assert exc.value.message == (
            "'times' and 'ask_id' are hidden by the policy (deny[0]); leave them out of the request"
        )

    def test_a_call_refused_only_for_its_variants(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, self.ALLOW + '[[deny]]\nops = ["query"]\nvariants = ["pong"]\nfields = ["*"]\n')
        look = catalog.get('LookReply')
        r.check(look, 'demo/w1/look/ann', variant='ping', fields=['ask_id'])
        with pytest.raises(RefusedError, match="'ask_id' is hidden"):
            r.check(look, 'demo/w1/look/ann', variant='pong', fields=['ask_id'])

    def test_a_call_refused_where_its_keys_reach(self, tmp_path, catalog):
        body = self.ALLOW + '[[deny]]\nops = ["invoke"]\nkeys = ["demo/w1/move/ann"]\nfields = ["move_id"]\n'
        r = rules(tmp_path, catalog, body)
        move = catalog.get('MoveResult')
        r.check(move, 'demo/w1/move/bea', fields=['move_id'])
        with pytest.raises(RefusedError, match="'move_id' is hidden"):
            r.check(move, 'demo/w1/move/ann', fields=['move_id'])


class TestBound:
    def test_bound_slots_and_shapes(self, tmp_path, catalog):
        r = rules(tmp_path, catalog, '[bind]\nworld = "w1"\nplayer = "ann"\n')
        assert r.bound(catalog.get('Station')) == {'world': 'w1'}
        assert r.shapes(catalog.get('Notice')) == ('demo/w1/notice/ann',)
        assert r.shapes(catalog.get('Trace')) == ('demo/w1/trace/*/**',)


class TestReferences:
    @pytest.mark.parametrize(
        ('body', 'path', 'match'),
        [
            ('[[allow]]\nops = ["read"]\noperations = ["Stations"]\n', ('allow', 0, 'operations', 0), 'no operation'),
            (
                '[[deny]]\nops = ["query"]\noperations = ["LookReply"]\nvariants = ["ping", "peek"]\n',
                ('deny', 0, 'variants', 1),
                "variant 'peek' is no tag",
            ),
            (
                '[[deny]]\nops = ["invoke"]\nfields = ["move_id", "note"]\n',
                ('deny', 0, 'fields', 1),
                "field 'note' is no field at the root of a request this rule covers",
            ),
            (  # a variant's field, where the rule covers no variant that has it
                '[[deny]]\nops = ["query"]\nvariants = ["ping"]\nfields = ["times"]\n',
                ('deny', 0, 'fields', 0),
                "field 'times' is no field",
            ),
            (  # a field of a request the rule's ops don't cover
                '[[deny]]\nops = ["query"]\nfields = ["move_id"]\n',
                ('deny', 0, 'fields', 0),
                "field 'move_id' is no field",
            ),
            (
                '[[deny]]\nops = ["invoke"]\nfields = ["to"]\n',
                ('deny', 0, 'fields', 0),
                "hides 'to', which MoveResult requires, so a call can't leave it out; deny MoveResult instead",
            ),
            (
                '[[deny]]\nops = ["query"]\nfields = ["station"]\n',
                ('deny', 0, 'fields', 0),
                "hides 'station', which LookReply variant 'ping' requires.*; deny LookReply variant 'ping' instead",
            ),
            ('[bind]\nplanet = "p1"\n', ('bind', 'planet'), 'no contract or command has as a slot'),
            ('[inject.tokn]\nenv = "X"\n', ('inject', 'tokn'), 'no request declares'),
        ],
    )
    def test_a_name_without_a_glob_must_name_something(self, tmp_path, catalog, body, path, match):
        with pytest.raises(PolicyFileError, match=match) as exc:
            rules(tmp_path, catalog, body)
        assert exc.value.path == path

    def test_globs_may_match_nothing(self, tmp_path, catalog):
        rules(tmp_path, catalog, '[[deny]]\nops = ["invoke"]\noperations = ["Order*"]\nvariants = ["dev-*"]\n')
        rules(tmp_path, catalog, '[[deny]]\nops = ["invoke"]\nfields = ["then*"]\n')

    def test_a_variant_s_field_its_variants_cover(self, tmp_path, catalog):
        rules(tmp_path, catalog, '[[deny]]\nops = ["query"]\nvariants = ["pong"]\nfields = ["times"]\n')

    def test_an_ambiguous_name_is_refused_naming_both(self, tmp_path):
        head = HEAD.replace('["_support_protocol"]', '["_support_protocol", "_support_protocol_two"]')
        policy = load(write(tmp_path, '[[allow]]\nops = ["read"]\noperations = ["Station"]\n', head=head))
        with pytest.raises(PolicyFileError, match='ambiguous') as exc:
            Rules(policy, Catalog.load(policy.protocol.packages))
        assert exc.value.path == ('allow', 0, 'operations', 0)

    def test_a_qualname_resolves_the_ambiguity(self, tmp_path):
        head = HEAD.replace('["_support_protocol"]', '["_support_protocol", "_support_protocol_two"]')
        body = '[[allow]]\nops = ["read"]\noperations = ["_support_protocol_two:Station"]\n'
        policy = load(write(tmp_path, body, head=head))
        r = Rules(policy, Catalog.load(policy.protocol.packages))
        assert [c.qualname for c in r.visible_contracts()] == ['_support_protocol_two:Station']


class TestFillHint:
    """A refusal over a slot left open names it, when an allow rule's keys fix that slot."""

    def refusal(self, tmp_path, catalog, keys, name, selector):
        r = rules(tmp_path, catalog, f'[[allow]]\nops = ["read"]\nkeys = {keys}\n')
        with pytest.raises(RefusedError) as exc:
            r.check(catalog.get(name), selector)
        return exc.value.message

    def test_names_the_slot_a_key_fixes_and_its_value(self, tmp_path, catalog):
        message = self.refusal(tmp_path, catalog, '["demo/w1/**"]', 'Station', 'demo/*/station/*')
        assert message == (
            "no allow rule covers read on Station at demo/*/station/*: slot 'world' is open; give world=w1, or bind"
            ' it in [bind]'
        )

    def test_names_every_slot_the_keys_fix(self, tmp_path, catalog):
        message = self.refusal(tmp_path, catalog, '["demo/w1/station/s1"]', 'Station', 'demo/*/station/*')
        assert message.endswith(
            ": slots 'world' and 'station' are open; give world=w1 station=s1, or bind them in [bind]"
        )

    def test_a_trailing_multi_chunk_slot(self, tmp_path, catalog):
        message = self.refusal(tmp_path, catalog, '["demo/w1/trace/a/b"]', 'Trace', 'demo/*/trace/*/**')
        assert message.endswith(": slots 'world' and 'path' are open; give world=w1 path=a/b, or bind them in [bind]")

    @pytest.mark.parametrize(
        ('keys', 'target', 'give'),
        [
            ('["demo/w1/**", "demo/w2/**"]', ('Station', 'demo/*/station/*'), 'world=…'),  # two values fit
            ('["demo/w1/**", "demo/*/station/s9"]', ('Station', 'demo/*/station/*'), 'world=… station=…'),  # or s9
            ('["demo/w1/trace/a/**"]', ('Trace', 'demo/w1/trace/*/**'), 'path=…'),  # it begins a/, then any
        ],
    )
    def test_names_no_value_where_more_than_one_fits(self, tmp_path, catalog, keys, target, give):
        message = self.refusal(tmp_path, catalog, keys, *target)
        assert f'; give {give}, or bind' in message

    @pytest.mark.parametrize(
        ('keys', 'selector'),
        [
            ('["other/**"]', 'demo/*/station/*'),  # no key reaches it: filling a slot wouldn't help
            ('["demo/w1/**"]', 'demo/w2/station/*'),  # the open slot is wild in every key; the given one is wrong
            ('["demo/*/station/s1"]', 'demo/*/station/s2'),  # nothing open
        ],
    )
    def test_names_nothing_a_value_would_not_fix(self, tmp_path, catalog, keys, selector):
        message = self.refusal(tmp_path, catalog, keys, 'Station', selector)
        assert message == f'no allow rule covers read on Station at {selector}'


@zeared.zeared
class Dock(zeared.Message):
    """A dock, retained under two topics."""

    TOPIC: ClassVar[str] = 'east/{site}/dock/{dock}'
    EXTRA_TOPICS: ClassVar[tuple[str, ...]] = ('west/{site}/dock/{dock}',)
    RETAINED: ClassVar[bool] = True

    site: str = zeared.Str(required=True, doc="the site's key")
    dock: str = zeared.Str(required=True, doc="the dock's key")


class TestFixed:
    """The slots a contract leaves open that the allow rules let only one value fill: ``check``'s ``fixed``."""

    @pytest.mark.parametrize(
        ('body', 'name', 'fixed'),
        [
            ('[[allow]]\nops = ["read"]\nkeys = ["demo/w1/**"]\n', 'Station', {'world': 'w1'}),
            ('[[allow]]\nops = ["read"]\nkeys = ["demo/w1/station/s1"]\n', 'Station', {'world': 'w1', 'station': 's1'}),
            ('[[allow]]\nops = ["read"]\nkeys = ["demo/w1/trace/a/b"]\n', 'Trace', {'world': 'w1', 'path': 'a/b'}),
            ('[bind]\nworld = "w1"\n\n[[allow]]\nops = ["read"]\nkeys = ["demo/w1/**"]\n', 'Station', {}),
            ('[[allow]]\nops = ["read"]\nkeys = ["demo/w1/**", "demo/w2/**"]\n', 'Station', {}),
            ('[[allow]]\nops = ["read"]\nkeys = ["demo/**"]\n', 'Station', {}),
        ],
    )
    def test_reads_the_allow_rules_keys(self, tmp_path, catalog, body, name, fixed):
        assert rules(tmp_path, catalog, body).fixed(catalog.get(name)) == fixed

    def test_counts_only_the_rules_that_cover_the_contract(self, tmp_path, catalog):
        body = (
            '[[allow]]\nops = ["read"]\noperations = ["Station"]\nkeys = ["demo/w1/**"]\n\n'
            '[[allow]]\nops = ["query"]\nkeys = ["demo/w2/**"]\n'
        )
        r = rules(tmp_path, catalog, body)
        assert r.fixed(catalog.get('Station')) == {'world': 'w1'}
        assert r.fixed(catalog.get('LookReply')) == {'world': 'w2'}

    @pytest.mark.parametrize(
        ('keys', 'fixed'), [('["east/x/**", "west/x/**"]', {'site': 'x'}), ('["east/x/**", "west/y/**"]', {})]
    )
    def test_every_topic_must_agree(self, tmp_path, keys, fixed):
        docks = Catalog.of([Dock])
        r = Rules(load(write(tmp_path, f'[[allow]]\nops = ["read"]\nkeys = {keys}\n')), docks)
        assert r.fixed(docks.get('Dock')) == fixed


@pytest.fixture(scope='module')
def app_catalog():
    return Catalog.load(['_support_protocol'], factory='_support_app:factory')


def tree_rules(tmp_path, app_catalog, body):
    return Rules(load(write(tmp_path, body)), app_catalog)


class TestTree:
    def test_each_command_takes_see_or_do(self, app_catalog):
        ops = {command.name: op_of(command) for command in app_catalog.commands}
        assert ops['StationShow'] is Op.SEE
        assert ops['relay/Status'] is Op.SEE
        assert ops['PlayerMove'] is Op.DO
        assert ops['StationDock'] is Op.DO
        assert ops['FleetMine'] is Op.DO  # no EFFECT: never a read

    def test_shown_by_its_keys(self, tmp_path, app_catalog):
        r = tree_rules(
            tmp_path, app_catalog, '[[allow]]\nops = ["see"]\n\n[[allow]]\nops = ["do"]\nkeys = ["relay/**"]\n'
        )
        assert [o.name for o in r.visible_operations()] == [
            'StationMap',
            'StationShow',
            'gateway/Status',
            'relay/Run',
            'relay/Status',
        ]
        assert r.visible_contracts() == []  # a tree rule never shows a contract

    def test_by_mount_with_globs(self, tmp_path, app_catalog):
        body = (
            '[[allow]]\nops = ["see", "do"]\noperations = ["gateway/*"]\n\n'
            '[[deny]]\nops = ["do"]\noperations = ["*/Run"]\n'
        )
        assert [o.name for o in tree_rules(tmp_path, app_catalog, body).visible_operations()] == ['gateway/Status']

    def test_a_deny_covering_every_key_hides(self, tmp_path, app_catalog):
        body = '[[allow]]\nops = ["do"]\n\n[[deny]]\nops = ["do"]\nkeys = ["fleet/*/mine", "mine/with/*"]\n'
        assert 'FleetMine' not in [o.name for o in tree_rules(tmp_path, app_catalog, body).visible_operations()]
        body = '[[allow]]\nops = ["do"]\n\n[[deny]]\nops = ["do"]\nkeys = ["fleet/*/mine"]\n'  # one phrase of two
        assert 'FleetMine' in [o.name for o in tree_rules(tmp_path, app_catalog, body).visible_operations()]

    def test_a_call_at_its_key(self, tmp_path, app_catalog):
        body = '[[allow]]\nops = ["do"]\nkeys = ["fleet/**"]\n\n[[deny]]\nops = ["do"]\nkeys = ["fleet/f9/**"]\n'
        r = tree_rules(tmp_path, app_catalog, body)
        mine = app_catalog.get('FleetMine')
        r.check(mine, 'fleet/f1/mine')
        with pytest.raises(RefusedError, match=r'do on FleetMine at fleet/f9/mine is denied by deny\[0\]'):
            r.check(mine, 'fleet/f9/mine')
        with pytest.raises(RefusedError, match='no allow rule covers do on FleetMine at mine/with/f1'):
            r.check(mine, 'mine/with/f1')

    def test_answering_yes_needs_confirm(self, tmp_path, app_catalog):
        dock = app_catalog.get('StationDock')
        r = tree_rules(tmp_path, app_catalog, '[[allow]]\nops = ["do"]\n')
        r.check(dock, 'dock/vesta')
        with pytest.raises(RefusedError, match='no allow rule covers confirm on StationDock at dock/vesta'):
            r.check(dock, 'dock/vesta', confirm=True)
        body = '[[allow]]\nops = ["do", "confirm"]\n\n[[deny]]\nops = ["confirm"]\nkeys = ["dock/ceres"]\n'
        r = tree_rules(tmp_path, app_catalog, body)
        r.check(dock, 'dock/vesta', confirm=True)
        with pytest.raises(RefusedError, match=r'confirm on StationDock at dock/ceres is denied by deny\[0\]'):
            r.check(dock, 'dock/ceres', confirm=True)

    def test_a_bound_slot_narrows_its_keys(self, tmp_path, app_catalog):
        r = tree_rules(
            tmp_path, app_catalog, '[bind]\nfleet = "f1"\n\n[[allow]]\nops = ["do"]\nkeys = ["fleet/f1/**"]\n'
        )
        mine = app_catalog.get('FleetMine')
        assert r.shapes(mine) == ('fleet/f1/mine', 'mine/with/f1')
        assert r.key(mine, game.FleetMine(fleet='f1')) == 'fleet/f1/mine'
        with pytest.raises(RefusedError) as exc:
            r.key(mine, game.FleetMine(fleet='f2'))
        assert (exc.value.message, exc.value.path) == ("the policy binds 'fleet' to 'f1'", ('fleet',))

    def test_the_one_value_an_allow_rule_fixes(self, tmp_path, app_catalog):
        r = tree_rules(tmp_path, app_catalog, '[[allow]]\nops = ["see"]\nkeys = ["station/s1/show"]\n')
        assert r.fixed(app_catalog.get('StationShow')) == {'station': 's1'}

    @pytest.mark.parametrize('value', ['a/b', 'a*', 'a$b', 'a?', '#a'])
    def test_a_key_holds_no_separator_or_wildcard(self, tmp_path, app_catalog, value):
        r = tree_rules(tmp_path, app_catalog, '[[allow]]\nops = ["do"]\n')
        with pytest.raises(RefusedError) as exc:
            r.key(app_catalog.get('FleetMine'), game.FleetMine(fleet=value))
        assert exc.value.message.startswith(f"FleetMine: slot 'fleet' holds {value!r}, and a key can't hold ")
        assert exc.value.message.endswith('read the slot as a name, whose resolver maps it to a key')
        assert exc.value.path == ('fleet',)

    def test_a_name_s_kind_says_which_resolver(self, tmp_path, app_catalog):
        r = tree_rules(tmp_path, app_catalog, '[[allow]]\nops = ["do"]\n')
        with pytest.raises(RefusedError) as exc:
            r.key(app_catalog.get('StationDock'), game.StationDock(station="Vruba'a Belt/1"))
        assert exc.value.message == (
            "StationDock: slot 'station' holds \"Vruba'a Belt/1\", and a key can't hold '/': map it to a key in the "
            "resolver of 'station'"
        )

    def test_a_key_under_a_prefix(self, tmp_path, app_catalog):
        from _support_tree import service

        r = tree_rules(tmp_path, app_catalog, '[[allow]]\nops = ["do"]\n')
        assert r.key(app_catalog.get('relay/Run'), service.Run()) == 'relay/run'  # parsed's key, led by the prefix
        assert r.key(app_catalog.get('gateway/Run'), service.Run()) == 'gateway/run'

    @pytest.mark.parametrize(
        ('body', 'message'),
        [
            (
                '[[allow]]\nops = ["see"]\noperations = ["Station"]\n',
                "Station is a contract, which the tree's ops never",
            ),
            ('[[allow]]\nops = ["read"]\noperations = ["StationShow"]\n', "StationShow is a command, which the wire's"),
        ],
    )
    def test_a_name_must_be_its_route_s(self, tmp_path, app_catalog, body, message):
        with pytest.raises(PolicyFileError, match=message) as exc:
            tree_rules(tmp_path, app_catalog, body)
        assert exc.value.path == ('allow', 0, 'operations', 0)

    def test_a_command_s_slot_may_be_bound(self, tmp_path, app_catalog):
        tree_rules(tmp_path, app_catalog, '[bind]\nstation = "s1"\n')  # Station's slot too, and StationShow's
