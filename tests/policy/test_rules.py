import pytest
from _support_policy import HEAD, write

from parsed.catalog import Catalog
from parsed.errors import PolicyFileError, RefusedError
from parsed.policy.config import Op, load
from parsed.policy.rules import Rules, op_of


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
        body = '[[allow]]\nops = ["read", "query", "invoke"]\ncontracts = ["*Reply", "Station"]\n'
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
            ('[[allow]]\nops = ["read"]\ncontracts = ["Stations"]\n', ('allow', 0, 'contracts', 0), 'no contract'),
            (
                '[[deny]]\nops = ["query"]\ncontracts = ["LookReply"]\nvariants = ["ping", "peek"]\n',
                ('deny', 0, 'variants', 1),
                "variant 'peek' is no tag",
            ),
            ('[bind]\nplanet = "p1"\n', ('bind', 'planet'), 'no contract has as a slot'),
            ('[inject.tokn]\nenv = "X"\n', ('inject', 'tokn'), 'no request declares'),
        ],
    )
    def test_a_name_without_a_glob_must_name_something(self, tmp_path, catalog, body, path, match):
        with pytest.raises(PolicyFileError, match=match) as exc:
            rules(tmp_path, catalog, body)
        assert exc.value.path == path

    def test_globs_may_match_nothing(self, tmp_path, catalog):
        rules(tmp_path, catalog, '[[deny]]\nops = ["invoke"]\ncontracts = ["Order*"]\nvariants = ["dev-*"]\n')

    def test_an_ambiguous_name_is_refused_naming_both(self, tmp_path):
        head = HEAD.replace('["_support_protocol"]', '["_support_protocol", "_support_protocol_two"]')
        policy = load(write(tmp_path, '[[allow]]\nops = ["read"]\ncontracts = ["Station"]\n', head=head))
        with pytest.raises(PolicyFileError, match='ambiguous') as exc:
            Rules(policy, Catalog.load(policy.protocol.packages))
        assert exc.value.path == ('allow', 0, 'contracts', 0)

    def test_a_qualname_resolves_the_ambiguity(self, tmp_path):
        head = HEAD.replace('["_support_protocol"]', '["_support_protocol", "_support_protocol_two"]')
        body = '[[allow]]\nops = ["read"]\ncontracts = ["_support_protocol_two:Station"]\n'
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

    def test_names_the_slot_a_key_fixes(self, tmp_path, catalog):
        message = self.refusal(tmp_path, catalog, '["demo/w1/**"]', 'Station', 'demo/*/station/*')
        assert message == (
            "no allow rule covers read on Station at demo/*/station/*: slot 'world' is open; give world=…, or bind it"
            ' in [bind]'
        )

    def test_names_every_slot_the_keys_fix(self, tmp_path, catalog):
        message = self.refusal(tmp_path, catalog, '["demo/w1/station/s1"]', 'Station', 'demo/*/station/*')
        assert message.endswith(
            ": slots 'world' and 'station' are open; give world=… station=…, or bind them in [bind]"
        )

    def test_a_trailing_multi_chunk_slot(self, tmp_path, catalog):
        message = self.refusal(tmp_path, catalog, '["demo/w1/trace/a/b"]', 'Trace', 'demo/*/trace/*/**')
        assert message.endswith(": slots 'world' and 'path' are open; give world=… path=…, or bind them in [bind]")

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
