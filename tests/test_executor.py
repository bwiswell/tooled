import pytest
from _support_session import free_port
from zeared import Mode, SessionConfig

from parsed.errors import MeshError
from parsed.executor import open_session


def test_opens_the_session_the_mesh_describes(listening):
    session = open_session(SessionConfig(mode=Mode.CLIENT, router=listening))
    session.close()


def test_an_unreachable_router_is_a_mesh_error_with_zenoh_s_reason():
    router = f'tcp/127.0.0.1:{free_port()}'
    with pytest.raises(MeshError, match='the session would not open: Unable to connect') as exc:
        open_session(SessionConfig(mode=Mode.CLIENT, router=router))
    assert '/root/.cargo' not in exc.value.message
    assert exc.value.exit_code == 5


# ---- reads -------------------------------------------------------------------------------------

from _support_policy import write  # noqa: E402 - the read tests' own imports

from parsed import executor  # noqa: E402
from parsed.catalog import Catalog  # noqa: E402
from parsed.errors import InvalidRequestError, RefusedError, UsageError  # noqa: E402
from parsed.policy import Rules, load  # noqa: E402

POLICY = """
[bind]
world = "w1"

[inject.token]
env = "DEMO_TOKEN"

[[allow]]
ops = ["read", "query"]
keys = ["demo/w1/**"]

[[deny]]
ops = ["read"]
keys = ["demo/w1/station/secret"]

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


@pytest.fixture
def client(world):
    session = open_session(SessionConfig(mode=Mode.CLIENT, router=world.endpoint))
    try:
        yield session
    finally:
        session.close()


class TestSlots:
    def test_given_slots_merge_with_bound_ones(self, rules, catalog):
        assert executor.slot_values(catalog.get('Station'), rules, {'station': 's1'}) == {
            'station': 's1',
            'world': 'w1',
        }

    def test_a_slot_the_contract_lacks(self, rules, catalog):
        with pytest.raises(UsageError, match="Station has no slot 'statoin'; its slots: world, station"):
            executor.slot_values(catalog.get('Station'), rules, {'statoin': 's1'})

    def test_a_bound_slot_given_another_value(self, rules, catalog):
        executor.slot_values(catalog.get('Station'), rules, {'world': 'w1'})
        with pytest.raises(RefusedError, match="binds 'world' to 'w1'"):
            executor.slot_values(catalog.get('Station'), rules, {'world': 'w2'})

    @pytest.mark.parametrize('value', ['', 'a/b', '/a'])
    def test_a_slot_takes_one_chunk(self, rules, catalog, value):
        with pytest.raises(UsageError, match='takes one chunk'):
            executor.slot_values(catalog.get('Station'), rules, {'station': value})

    def test_a_multi_slot_takes_several(self, rules, catalog):
        assert executor.slot_values(catalog.get('Trace'), rules, {'path': 'a/b'})['path'] == 'a/b'

    def test_a_value_that_makes_no_key_expression(self):
        with pytest.raises(UsageError, match='no key expression'):
            executor.selector('demo/{world}/station/{station}', {'world': 'w1', 'station': 'a**b'})


class TestFetch:
    def test_every_value_the_bound_slots_select_sorted(self, client, tmp_path, catalog):
        open_rules = Rules(load(write(tmp_path, '[bind]\nworld = "w1"\n\n[[allow]]\nops = ["read"]\n')), catalog)
        result = executor.fetch(client, catalog.get('Station'), open_rules, {'world': 'w1'}, timeout=5)
        assert result.items == [
            {'world': 'w1', 'station': 's1', 'stock': 3},
            {'world': 'w1', 'station': 's2', 'stock': 5},
        ]
        assert result.notes == []

    def test_narrowed_by_a_slot(self, client, rules, catalog):
        values = executor.slot_values(catalog.get('Station'), rules, {'station': 's2'})
        result = executor.fetch(client, catalog.get('Station'), rules, values, timeout=5)
        assert result.items == [{'world': 'w1', 'station': 's2', 'stock': 5}]

    def test_a_selector_that_could_reach_a_denied_key_is_refused(self, client, rules, catalog):
        for station in ('s$*', '*'):
            with pytest.raises(RefusedError, match=r'deny\[0\]; narrow the slots'):
                executor.fetch(client, catalog.get('Station'), rules, {'world': 'w1', 'station': station}, timeout=5)

    def test_a_selector_no_allow_covers_is_refused(self, client, tmp_path, catalog):
        r = Rules(load(write(tmp_path, '[[allow]]\nops = ["read"]\nkeys = ["demo/w1/**"]\n')), catalog)
        with pytest.raises(RefusedError, match='no allow rule covers read on Station at demo/\\*/station/\\*'):
            executor.fetch(client, catalog.get('Station'), r, {}, timeout=5)


class TestQuery:
    def query(self, client, rules, catalog, *, request=None, params=None):
        contract = catalog.get('LookReply')
        return executor.query(
            client,
            contract,
            rules,
            executor.slot_values(contract, rules, {'player': 'ann'}),
            request=request,
            injected={'token': 'tok-ann'},
            params=params or {},
            timeout=5,
        )

    def test_the_request_is_built_with_the_injected_fields_and_sent(self, client, rules, catalog):
        result = self.query(client, rules, catalog, request={'kind': 'ping', 'station': 's1'}, params={'since': '3'})
        (reply,) = result.items
        assert reply['seen'] == "ping {'station': 's1'} at [('player', 'ann'), ('world', 'w1')] {'since': '3'}"

    def test_a_value_for_an_injected_field_is_refused(self, client, rules, catalog):
        with pytest.raises(RefusedError, match="'token' is injected by the policy"):
            self.query(client, rules, catalog, request={'kind': 'ping', 'station': 's1', 'token': 'mine'})

    def test_a_request_that_does_not_load_names_the_field(self, client, rules, catalog):
        with pytest.raises(InvalidRequestError) as exc:
            self.query(client, rules, catalog, request={'kind': 'ping'})
        assert exc.value.path == ('station',)
        assert exc.value.exit_code == 4

    def test_an_unknown_key_is_refused(self, client, rules, catalog):
        with pytest.raises(InvalidRequestError, match='unknown key'):
            self.query(client, rules, catalog, request={'kind': 'ping', 'station': 's1', 'extra': 1})

    def test_a_denied_variant_is_refused(self, client, rules, catalog):
        with pytest.raises(RefusedError, match="variant 'dev-peek' is denied"):
            self.query(client, rules, catalog, request={'kind': 'dev-peek', 'depth': 2})

    def test_no_reply_is_noted(self, client, rules, catalog):
        result = executor.query(
            client,
            catalog.get('Status'),
            rules,
            {'world': 'w1'},
            request=None,
            injected={},
            params={},
            timeout=0.2,
        )
        assert result.items == [{'world': 'w1', 'up': True}]  # a retained class answers a query too
        empty = executor.query(
            client, catalog.get('Notice'), rules, {'world': 'w1'}, request=None, injected={}, params={}, timeout=0.2
        )
        assert (empty.items, empty.notes) == ([], ['no replies within 0.2s'])

    def test_a_request_to_a_contract_that_takes_none(self, client, rules, catalog):
        with pytest.raises(UsageError, match='Notice takes no request'):
            executor.query(
                client, catalog.get('Notice'), rules, {'world': 'w1'}, request={}, injected={}, params={}, timeout=1
            )


class TestHelpers:
    def test_variant_of(self, catalog):
        look = catalog.get('LookReply')
        request = executor.build_request(look.request, {'kind': 'pong', 'times': 2}, {'token': 't'})
        assert executor.variant_of(look, request) == 'pong'
        assert executor.variant_of(catalog.get('Station'), object()) is None

    def test_an_injected_value_that_does_not_load_is_not_shown(self, catalog):
        move = catalog.get('MoveResult')
        with pytest.raises(InvalidRequestError) as exc:
            executor.build_request(move.request, {'to': 'x'}, {'token': 12345})  # an int: no str
        assert '12345' not in exc.value.message
        assert exc.value.path == ('token',)


# ---- listen ------------------------------------------------------------------------------------

import asyncio  # noqa: E402

import _support_protocol as demo  # noqa: E402


async def publish_until(task, *messages, session, every=0.05, limit=200):
    """Publish ``messages`` on ``session`` every ``every`` seconds until ``task`` is done."""
    for _ in range(limit):
        if task.done():
            return
        for message in messages:
            message.send(session=session)
        await asyncio.sleep(every)


class TestListen:
    async def test_replays_the_retained_set_then_hands_on_live_values(self, client, rules, catalog, world):
        got = []
        station = catalog.get('Station')
        task = asyncio.create_task(
            executor.listen(client, station, rules, {'world': 'w1'}, each=got.append, meta=True, count=3)
        )
        await asyncio.sleep(0.3)  # the replay is the set as it stands when the subscription is declared
        live = [
            demo.Station(world='w1', station='secret', stock=1),  # a denied key: left out, counted
            demo.Station(world='w2', station='s9', stock=1),  # another world: not asked for
            demo.Station(world='w1', station='s3', stock=9),
        ]
        await publish_until(task, *live, session=world.session)
        result = await task
        assert [g['origin'] for g in got] == ['replay', 'replay', 'live']
        assert {g['value']['station'] for g in got[:2]} == {'s1', 's2'}
        assert got[2]['key'] == 'demo/w1/station/s3'
        assert got[2]['value'] == {'world': 'w1', 'station': 's3', 'stock': 9}
        assert got[2]['at'] is not None
        (note,) = result.notes
        assert note.endswith('samples on keys the policy denies were left out')

    async def test_a_stream_narrowed_by_a_slot(self, client, rules, catalog, world):
        got = []
        notice = catalog.get('Notice')
        values = executor.slot_values(notice, rules, {'player': 'ann'})
        task = asyncio.create_task(executor.listen(client, notice, rules, values, each=got.append, count=2))
        await asyncio.sleep(0.2)
        await publish_until(
            task,
            demo.Notice(world='w1', player='bo', text='not for ann'),
            demo.Notice(world='w1', player='ann', text='hello'),
            session=world.session,
        )
        assert (await task).notes == []
        assert got == [{'world': 'w1', 'player': 'ann', 'text': 'hello'}] * 2

    async def test_stops_after_a_duration(self, client, rules, catalog, world):
        got = []
        result = await executor.listen(
            client, catalog.get('Notice'), rules, {'world': 'w1'}, each=got.append, duration=0.2
        )
        assert (got, result.notes) == ([], [])

    async def test_stops_when_told(self, client, rules, catalog, world):
        stop = asyncio.Event()
        task = asyncio.create_task(
            executor.listen(client, catalog.get('Notice'), rules, {'world': 'w1'}, each=print, stop=stop)
        )
        await asyncio.sleep(0.1)
        stop.set()
        assert (await asyncio.wait_for(task, 5)).notes == []

    async def test_what_the_consumer_raises_is_raised(self, client, rules, catalog, world):
        def boom(_value):
            msg = 'consumer failed'
            raise RuntimeError(msg)

        task = asyncio.create_task(executor.listen(client, catalog.get('Station'), rules, {'world': 'w1'}, each=boom))
        with pytest.raises(RuntimeError, match='consumer failed'):
            await asyncio.wait_for(task, 5)
