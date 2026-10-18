import asyncio
import hashlib

import _support_protocol as demo
import pytest
from _support_policy import write
from _support_session import free_port
from zeared import Mode, SessionConfig

from parsed import executor
from parsed.catalog import Catalog
from parsed.errors import InvalidRequestError, LostWriteError, MeshError, RefusedError, TimedOutError, UsageError
from parsed.executor import open_session
from parsed.policy import Rules, load


def test_opens_the_session_the_mesh_describes(listening):
    session = open_session(SessionConfig(mode=Mode.CLIENT, router=listening))
    session.close()


def test_an_unreachable_router_is_a_mesh_error_with_zenoh_s_reason():
    router = f'tcp/127.0.0.1:{free_port()}'
    with pytest.raises(MeshError, match='the session would not open: Unable to connect') as exc:
        open_session(SessionConfig(mode=Mode.CLIENT, router=router))
    assert '/root/.cargo' not in exc.value.message
    assert exc.value.exit_code == 5


# ---- reads ----------------------------------------------------------------------------------

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
        (status,) = result.items  # a retained class answers a query too
        assert status['up'] is True
        logo = b'\x89PNG-ish'
        assert status['logo'] == {'bytes': len(logo), 'sha256': hashlib.sha256(logo).hexdigest()}
        empty = executor.query(
            client, catalog.get('Notice'), rules, {'world': 'w1'}, request=None, injected={}, params={}, timeout=0.2
        )
        assert (empty.items, empty.notes) == ([], ['no replies within 0.2s'])

    def test_a_request_to_a_contract_that_takes_none(self, client, rules, catalog):
        with pytest.raises(UsageError, match='Notice takes no request'):
            executor.query(
                client, catalog.get('Notice'), rules, {'world': 'w1'}, request={}, injected={}, params={}, timeout=1
            )


# ---- writes ---------------------------------------------------------------------------------

WRITES = (
    POLICY
    + """
[[allow]]
ops = ["invoke"]
keys = ["demo/w1/**"]

[[deny]]
ops = ["invoke"]
keys = ["demo/w1/move/bo"]
"""
)


@pytest.fixture
def writes(tmp_path, catalog):
    return Rules(load(write(tmp_path, WRITES)), catalog)


class TestInvoke:
    def move(self, client, writes, catalog, *, player='ann', to='dock', move_id=None, retry=True, timeout=5):  # noqa: PLR0913
        """A move by ``player`` to ``to``; with ``move_id``, a generated id the request carries."""
        contract = catalog.get('MoveResult')
        given = {} if player is None else {'player': player}
        injected = {'token': 'tok-ann'} | ({} if move_id is None else {'move_id': move_id})
        return executor.invoke(
            client,
            contract,
            writes,
            executor.slot_values(contract, writes, given),
            request={'to': to},
            injected=injected,
            generated=[] if move_id is None else ['move_id'],
            retry=retry,
            timeout=timeout,
        )

    def test_the_request_is_built_with_the_injected_fields_and_sent(self, client, writes, catalog, world):
        result = self.move(client, writes, catalog)
        assert (result.items, result.notes) == ([{'world': 'w1', 'player': 'ann', 'ok': True}], [])
        (sent,) = world.asked
        assert (sent.token, sent.to) == ('tok-ann', 'dock')

    def test_a_refusal_by_the_server_is_a_reply(self, client, writes, catalog):
        result = self.move(client, writes, catalog, to='nowhere')
        assert (result.items, result.notes) == ([{'world': 'w1', 'player': 'ann', 'ok': False}], [])

    def test_a_queryable_that_declares_no_effect(self, client, writes, catalog):
        echo = catalog.get('EchoReply')
        result = executor.invoke(client, echo, writes, {'world': 'w1'}, request={'text': 'hi'}, injected={}, timeout=5)
        assert result.items == [{'world': 'w1', 'text': 'hi'}]

    @pytest.mark.parametrize(
        ('player', 'message'),
        [
            (None, 'a write reaches one key, and demo/w1/move/* could reach more: give player'),
            ('a$*', 'a write reaches one key, and demo/w1/move/a$* could reach more: give each slot a value'),
        ],
    )
    def test_a_write_reaches_one_key(self, writes, catalog, world, player, message):
        with pytest.raises(UsageError) as exc:
            self.move(object(), writes, catalog, player=player)  # refused before a session is used
        assert exc.value.message.startswith(message)
        assert world.asked == []

    def test_a_denied_key_is_refused_before_sending(self, client, writes, catalog, world):
        with pytest.raises(RefusedError, match='invoke on MoveResult at demo/w1/move/bo is denied by deny'):
            self.move(client, writes, catalog, player='bo')
        assert world.asked == []

    def test_a_failure_names_the_write(self, writes, catalog, monkeypatch):
        def refuse(**_kwargs):
            msg = 'no route'
            raise RuntimeError(msg)

        monkeypatch.setattr(demo.MoveResult, 'query', refuse)
        with pytest.raises(MeshError, match=r'^the write failed: no route$'):
            self.move(object(), writes, catalog)


class TestRetry:
    """A write nothing answered, asked once more with the same generated id."""

    move = TestInvoke.move
    ID = 'a' * 32

    @pytest.mark.parametrize('case', [('lossy', 5), ('slow', 0.3)], ids=['dropped', 'timed-out'])
    def test_a_lost_reply_is_asked_once_more_with_the_same_id(self, client, writes, catalog, world, case):
        to, timeout = case
        result = self.move(client, writes, catalog, to=to, move_id=self.ID, timeout=timeout)
        assert result.items == [{'world': 'w1', 'player': 'ann', 'ok': True}]
        assert result.notes == [f'no reply within {timeout:g}s; asked once more with the same move_id']
        assert [sent.move_id for sent in world.asked] == [self.ID, self.ID]

    def test_still_no_reply_is_a_lost_write_with_its_id(self, client, writes, catalog, world):
        with pytest.raises(LostWriteError) as exc:
            self.move(client, writes, catalog, to='void', move_id=self.ID, timeout=0.3)
        assert exc.value.message == (
            'no reply to MoveResult within 0.3s, after one retry: it may or may not have been carried out'
        )
        assert exc.value.to_json()['generated'] == {'move_id': self.ID}
        assert len(world.asked) == 2

    def test_no_retry(self, client, writes, catalog, world):
        with pytest.raises(LostWriteError) as exc:
            self.move(client, writes, catalog, to='void', move_id=self.ID, retry=False)
        assert exc.value.message == 'no reply to MoveResult within 5s: it may or may not have been carried out'
        assert exc.value.generated == {'move_id': self.ID}
        assert len(world.asked) == 1

    def test_no_generated_id_no_retry(self, client, writes, catalog, world):
        with pytest.raises(LostWriteError) as exc:
            self.move(client, writes, catalog, to='void')
        assert 'generated' not in exc.value.to_json()
        assert len(world.asked) == 1  # a repeat with nothing to tell it by could do the work twice

    def test_an_error_reply_is_an_answer(self, client, writes, catalog, world):
        result = self.move(client, writes, catalog, to='broken', move_id=self.ID)
        assert (result.items, result.notes) == ([], ['MoveResult answered with an error: the dock is closed'])
        assert len(world.asked) == 1


class TestPrepare:
    def test_a_read_may_still_widen(self, writes, catalog):
        look = catalog.get('LookReply')
        call = executor.prepare(look, writes, {'world': 'w1'}, request={'kind': 'pong'}, injected={'token': 't'})
        assert (call.selector, call.variant) == ('demo/w1/look/*', 'pong')

    def test_the_preview_masks_what_it_is_told_to(self, writes, catalog):
        move = catalog.get('MoveResult')
        injected = {'token': 'tok-ann', 'move_id': 'f' * 32}
        call = executor.prepare(
            move, writes, {'world': 'w1', 'player': 'ann'}, request={'to': 'dock'}, injected=injected
        )
        assert executor.preview(move, call, hidden=['token']) == {
            'selector': 'demo/w1/move/ann',
            'variant': None,
            'request': {'token': '<injected>', 'to': 'dock', 'move_id': 'f' * 32},
        }

    def test_the_preview_names_the_variant(self, writes, catalog):
        look = catalog.get('LookReply')
        values = {'world': 'w1', 'player': 'ann'}
        call = executor.prepare(
            look, writes, values, request={'kind': 'ping', 'station': 's1'}, injected={'token': 't'}
        )
        preview = executor.preview(look, call, hidden=['token'])
        assert preview['variant'] == 'ping'
        assert preview['request'] == {'token': '<injected>', 'kind': 'ping', 'station': 's1'}  # a flat union


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


# ---- listen ---------------------------------------------------------------------------------


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


# ---- wait -----------------------------------------------------------------------------------


class TestCondition:
    @pytest.mark.parametrize(
        ('text', 'path', 'value'),
        [
            ('stock=5', ('stock',), 5),
            ('up=true', ('up',), True),
            ('logo=null', ('logo',), None),
            ('fuel=40.5', ('fuel',), 40.5),
            ('activity=idle', ('activity',), 'idle'),
            ('mark="30%"', ('mark',), '30%'),
            ('key=-4,-1.0', ('key',), '-4,-1.0'),  # not JSON: a string
            ('code=007', ('code',), '007'),  # nor this
            ('note=a=b', ('note',), 'a=b'),  # the first = splits
            ('numbers.fuel=3', ('numbers', 'fuel'), 3),
        ],
    )
    def test_parse(self, text, path, value):
        condition = executor.Condition.parse(text)
        assert (condition.path, condition.value, condition.text) == (path, value, text)

    @pytest.mark.parametrize('text', ['stock', '=5', 'a..b=1', 'a.=1'])
    def test_a_malformed_condition(self, text):
        with pytest.raises(UsageError, match='--until is path=value'):
            executor.Condition.parse(text)

    def test_met(self):
        value = {'numbers': {'fuel': 40.0, 'up': True}, 'ships': [{'name': 'a'}, {'name': 'b'}], 'tags': ['x']}
        met = [
            'numbers.fuel=40',  # a whole number equals its float
            'numbers.up=true',
            'ships.1.name=b',  # a whole number indexes a list
            'tags=["x"]',
            'numbers={"fuel": 40, "up": true}',
        ]
        unmet = [
            'numbers.up=1',  # a number is no boolean
            'numbers.fuel="40"',
            'ships.2.name=b',
            'ships.x.name=b',
            'nope=1',
            'numbers.fuel.deeper=1',
        ]
        assert [executor.Condition.parse(text).met(value) for text in met] == [True] * len(met)
        assert [executor.Condition.parse(text).met(value) for text in unmet] == [False] * len(unmet)


class TestWait:
    def until(self, *texts):
        return [executor.Condition.parse(text) for text in texts]

    async def test_a_retained_value_already_so_answers_from_the_replay(self, client, rules, catalog, world):
        result = await executor.wait(
            client, catalog.get('Station'), rules, {'world': 'w1'}, until=self.until('stock=5'), within=5
        )
        assert (result.items, result.notes) == ([{'world': 'w1', 'station': 's2', 'stock': 5}], [])

    async def test_every_condition_must_hold(self, client, rules, catalog, world):
        station, values = catalog.get('Station'), {'world': 'w1'}
        met = await executor.wait(client, station, rules, values, until=self.until('station=s1', 'stock=3'), within=5)
        assert met.items == [{'world': 'w1', 'station': 's1', 'stock': 3}]
        with pytest.raises(TimedOutError):
            await executor.wait(client, station, rules, values, until=self.until('station=s1', 'stock=5'), within=0.3)

    async def test_a_live_value(self, client, rules, catalog, world):
        notice = catalog.get('Notice')
        values = executor.slot_values(notice, rules, {'player': 'ann'})
        task = asyncio.create_task(
            executor.wait(client, notice, rules, values, until=self.until('text=hello'), meta=True, within=5)
        )
        await asyncio.sleep(0.2)
        await publish_until(
            task,
            demo.Notice(world='w1', player='ann', text='not yet'),
            demo.Notice(world='w1', player='bo', text='hello'),  # another player: not asked for
            demo.Notice(world='w1', player='ann', text='hello'),
            session=world.session,
        )
        (got,) = (await task).items
        assert (got['key'], got['origin'], got['value']['text']) == ('demo/w1/notice/ann', 'live', 'hello')

    async def test_the_timeout_carries_the_last_value_seen(self, client, rules, catalog, world):
        with pytest.raises(TimedOutError) as exc:
            await executor.wait(
                client, catalog.get('Station'), rules, {'world': 'w1'}, until=self.until('stock=99'), within=0.3
            )
        assert exc.value.message == 'no Station value met stock=99 within 0.3s: 2 seen'
        assert exc.value.last in (
            {'world': 'w1', 'station': 's1', 'stock': 3},
            {'world': 'w1', 'station': 's2', 'stock': 5},
        )
        assert (exc.value.kind, exc.value.exit_code) == ('timeout', 7)

    async def test_a_path_no_value_had_is_named(self, client, rules, catalog, world):
        with pytest.raises(TimedOutError, match=r'2 seen, none with stok$'):
            await executor.wait(
                client, catalog.get('Station'), rules, {'world': 'w1'}, until=self.until('stok=5'), within=0.3
            )

    async def test_nothing_seen(self, client, rules, catalog, world):
        with pytest.raises(TimedOutError) as exc:
            await executor.wait(
                client, catalog.get('Notice'), rules, {'world': 'w1'}, until=self.until('text=hi'), within=0.2
            )
        assert (exc.value.message, exc.value.last) == ('no Notice value met text=hi within 0.2s: 0 seen', None)

    async def test_stopped(self, client, rules, catalog, world):
        stop = asyncio.Event()
        task = asyncio.create_task(
            executor.wait(
                client, catalog.get('Notice'), rules, {'world': 'w1'}, until=self.until('text=hi'), within=30, stop=stop
            )
        )
        await asyncio.sleep(0.1)
        stop.set()
        with pytest.raises(TimedOutError, match=r'^stopped before any Notice value met text=hi: 0 seen$'):
            await asyncio.wait_for(task, 5)
