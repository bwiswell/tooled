import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from _support_held import serving
from _support_policy import write

import tooled
from tooled.mcp import LEGACY, MODERN, SUPPORTED, TTL_MS, Served

TESTS = Path(__file__).parent
MODERN_META = {'_meta': {'io.modelcontextprotocol/protocolVersion': MODERN[0]}}
INFO = {'io.modelcontextprotocol/serverInfo': {'name': 'tooled', 'version': tooled.__version__}}


class Client:
    """An MCP client of the test's own, over a pipe each way: requests sent, and their responses read by id."""

    def __init__(self, writing, reading):
        self.writing, self.reading = writing, reading
        self.got: dict = {}
        self.next = 0

    def send(self, message):
        self.writing.write((json.dumps(message) + '\n').encode())
        self.writing.flush()

    def raw(self, text):
        self.writing.write(text.encode() + b'\n')
        self.writing.flush()

    def ask(self, method, params=None, *, ident=None, modern=False):
        """Send a request; return its id, to read its response by."""
        self.next += 1
        ident = self.next if ident is None else ident
        message = {'jsonrpc': '2.0', 'id': ident, 'method': method}
        if params is not None or modern:
            message['params'] = {**(params or {}), **(MODERN_META if modern else {})}
        self.send(message)
        return ident

    def tell(self, method, params=None):
        message = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        self.send(message)

    def answer(self, ident, *, within=30.0):
        """The response to ``ident``, however the responses came in."""
        deadline = time.monotonic() + within
        while ident not in self.got:
            assert time.monotonic() < deadline, f'no response to {ident!r}'
            line = self.reading.readline()
            assert line, 'the server closed its output'
            message = json.loads(line)
            self.got[message.get('id')] = message
        return self.got.pop(ident)

    def asked(self, method, params=None, **given):
        return self.answer(self.ask(method, params, **given))


@contextlib.contextmanager
def mcp(server, *, verbs=None, environ=None, cwd='/'):
    """``Served`` on ``server``'s session, in a thread, its stdin and stdout a pipe each to a ``Client``."""
    served = Served(server.where, verbs=verbs, environ=environ or {}, cwd=cwd)
    served.open()
    to_server, from_client = os.pipe()
    to_client, from_server = os.pipe()
    with (
        os.fdopen(to_server, 'rb') as reading,
        os.fdopen(from_server, 'wb') as writing,
        os.fdopen(from_client, 'wb') as client_out,
        os.fdopen(to_client, 'rb') as client_in,
    ):
        thread = threading.Thread(target=served.run, args=(reading, writing), daemon=True)
        thread.start()
        client = Client(client_out, client_in)
        try:
            yield client
        finally:
            client_out.close()  # the end of stdin: serve stops its calls, and returns
            thread.join(30)
            assert not thread.is_alive()


def text(response, at=0):
    return response['result']['content'][at]['text']


class TestOpening:
    """Both eras: a legacy client's initialize, and a modern client's version on each request."""

    @pytest.mark.parametrize('version', [*LEGACY, '2024-01-01'])
    def test_a_legacy_client_s_initialize(self, policy, version):
        with serving(policy) as server, mcp(server) as client:
            got = client.asked(
                'initialize', {'protocolVersion': version, 'capabilities': {}, 'clientInfo': {'name': 'test'}}
            )
            client.tell('notifications/initialized')
            assert client.asked('ping')['result'] == {'resultType': 'complete', '_meta': INFO}
        result = got['result']
        assert (result['protocolVersion'], result['capabilities'], result['serverInfo']) == (
            version if version in LEGACY else LEGACY[0],
            {'tools': {}},
            INFO['io.modelcontextprotocol/serverInfo'],
        )
        assert result['instructions'].startswith('Typed tools a policy allows.')

    def test_a_modern_client_s_discover(self, policy):
        with serving(policy) as server, mcp(server) as client:
            got = client.asked('server/discover', modern=True)
        assert got['result'] == {
            'resultType': 'complete',
            'supportedVersions': list(SUPPORTED),
            'capabilities': {'tools': {}},
            'instructions': got['result']['instructions'],
            'ttlMs': TTL_MS,
            'cacheScope': 'private',
            '_meta': INFO,
        }

    def test_a_version_it_does_not_speak(self, policy):
        with serving(policy) as server, mcp(server) as client:
            got = client.asked(
                'tools/list', {'_meta': {'io.modelcontextprotocol/protocolVersion': '1999-01-01'}}, ident='v'
            )
        assert got == {
            'jsonrpc': '2.0',
            'id': 'v',
            'error': {
                'code': -32022,
                'message': 'Unsupported protocol version',
                'data': {'supported': list(SUPPORTED), 'requested': '1999-01-01'},
            },
        }

    @pytest.mark.parametrize(
        ('line', 'ident', 'code'),
        [
            ('not json', None, -32700),
            ('[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]', None, -32600),
            ('{"jsonrpc": "1.0", "id": 7, "method": "ping"}', 7, -32600),
            ('{"jsonrpc": "2.0", "id": 8, "method": "nope"}', 8, -32601),
            ('{"jsonrpc": "2.0", "id": 9, "method": "ping", "params": [1]}', 9, -32602),
        ],
    )
    def test_what_isn_t_a_request_it_answers(self, policy, line, ident, code):
        with serving(policy) as server, mcp(server) as client:
            client.raw(line)
            got = client.answer(ident)
        assert (got['id'], got['error']['code']) == (ident, code)

    def test_a_notification_has_no_response(self, policy):
        with serving(policy) as server, mcp(server) as client:
            client.tell('notifications/initialized')
            client.tell('notifications/whatever', {'x': 1})
            client.raw('{"jsonrpc": "2.0", "id": 99, "result": {}}')  # a response: serve sends no requests
            assert client.asked('ping', ident='after')['id'] == 'after'
            assert client.got == {}


class TestTools:
    def test_the_session_s_export_in_its_order(self, policy, capsys):
        from tooled.cli import main

        assert main(['tools', '--format', 'mcp', '--max-bytes', '0', '--policy', policy]) == 0
        exported = json.loads(capsys.readouterr().out)
        with serving(policy) as server, mcp(server) as client:
            first = client.asked('tools/list', modern=True)
            second = client.asked('tools/list', {'cursor': 'ignored'})
        assert first['result'] == {
            'resultType': 'complete',
            'tools': exported,
            'ttlMs': TTL_MS,
            'cacheScope': 'private',
            '_meta': INFO,
        }
        assert json.dumps(second['result']) == json.dumps(first['result'])  # byte-stable

    def test_picked(self, policy):
        with serving(policy) as server, mcp(server, verbs='see,do:Station*') as client:
            names = [tool['name'] for tool in client.asked('tools/list')['result']['tools']]
            called = client.asked('tools/call', {'name': 'do_fleet_mine', 'arguments': {}})
        assert names == ['do_station_dock', 'see_gateway_status', 'see_relay_status', 'see_station_show']
        assert json.loads(text(called))['text'] == 'f1 mining'  # a tool the policy shows, not picked, still calls


class TestCalls:
    def test_a_read(self, policy):
        with serving(policy) as server, mcp(server) as client:
            got = client.asked('tools/call', {'name': 'see_station_show', 'arguments': {'station': 's1'}}, modern=True)
        result = got['result']
        assert (result['resultType'], result['isError'], len(result['content'])) == ('complete', False, 1)
        assert json.loads(text(got))['reply'] == {'world': 'w1', 'station': 's1', 'stock': 5}

    def test_the_same_as_tooled_call_attached(self, policy, capsys):
        from tooled.cli import main

        argv = ['call', 'do_station_dock', '--args', '{"station": "vesta", "confirm": true}', '--session', 't']
        with serving(policy) as server:
            assert main(argv) == 0
            cli = capsys.readouterr().out
            with mcp(server) as client:
                got = client.asked(
                    'tools/call', {'name': 'do_station_dock', 'arguments': {'station': 'vesta', 'confirm': True}}
                )
        assert text(got) == cli.rstrip('\n')

    @pytest.mark.parametrize(
        ('name', 'arguments', 'kind'),
        [
            ('do_station_dock', {'station': 'ceres', 'confirm': True}, 'refused'),  # exit 3
            ('see_station_show', {'station': 's1', 'stock': 3}, 'validation'),  # exit 4
            ('see_station_show', {}, 'validation'),
        ],
    )
    def test_a_refusal_is_a_tool_error_to_correct(self, policy, name, arguments, kind):
        with serving(policy) as server, mcp(server) as client:
            got = client.asked('tools/call', {'name': name, 'arguments': arguments})
        assert got['result']['isError'] is True
        assert json.loads(text(got))['error'] == kind

    def test_a_handler_that_fails(self, policy):
        with serving(policy) as server, mcp(server) as client:
            got = client.asked('tools/call', {'name': 'do_relay_run', 'arguments': {}})
        envelope = json.loads(text(got))
        assert (got['result']['isError'], envelope['ok'], envelope['error']['error']) == (True, False, 'handler')

    def test_over_the_cap(self, policy):
        with serving(policy) as server, mcp(server, environ={'TOOLED_MAX_BYTES': '40'}) as client:
            got = client.asked('tools/call', {'name': 'see_station_show', 'arguments': {'station': 's1'}})
        assert (got['result']['isError'], json.loads(text(got))['error']) == (True, 'too_large')

    def test_a_session_that_has_ended(self, policy):
        with serving(policy) as server, mcp(server) as client:
            server.end('stopped')
            deadline = time.monotonic() + 10
            while server.where.socket.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            got = client.asked('tools/call', {'name': 'see_station_show', 'arguments': {'station': 's1'}})
        error = json.loads(text(got))
        assert (got['result']['isError'], error['error']) == (True, 'mesh')  # exit 5, saying how it ended
        assert error['message'].startswith("no session 't' is open: it ended at ")

    @pytest.mark.parametrize(
        ('params', 'message'),
        [
            ({'name': 'nowhere', 'arguments': {}}, 'Unknown tool: nowhere'),
            (
                {'name': 'see_station_show', 'arguments': []},
                "Invalid params: 'name', a string, and 'arguments', an object",
            ),
            ({'arguments': {}}, "Invalid params: 'name', a string, and 'arguments', an object"),
        ],
    )
    def test_protocol_errors(self, policy, params, message):
        with serving(policy) as server, mcp(server) as client:
            got = client.asked('tools/call', params)
        assert got['error'] == {'code': -32602, 'message': message}

    def test_each_call_logged_naming_the_session(self, policy, tmp_path, monkeypatch):
        log = tmp_path / 'calls.jsonl'
        monkeypatch.setenv('TOOLED_LOG', str(log))
        with serving(policy) as server, mcp(server) as client:
            client.asked('tools/call', {'name': 'see_station_show', 'arguments': {'station': 's1'}})
            client.asked('tools/call', {'name': 'do_station_dock', 'arguments': {'station': 'ceres', 'confirm': True}})
        first, second = (json.loads(line) for line in log.read_text().splitlines())
        assert (first['verb'], first['tool'], first['session'], first['exit']) == ('call', 'see_station_show', 't', 0)
        assert (second['exit'], second['error'], second['arguments']) == (3, 'refused', ['station', 'confirm'])

    def test_side_by_side(self, desk):
        with serving(desk) as server, mcp(server) as client:
            slow = client.ask('tools/call', {'name': 'see_bell_ring', 'arguments': {}})
            quick = client.asked('tools/call', {'name': 'see_clock_show', 'arguments': {}})
            assert quick['result']['isError'] is False
            client.tell('notifications/cancelled', {'requestId': slow, 'reason': 'done waiting'})
            deadline = time.monotonic() + 10
            while server.status()['in_flight'] and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.status()['in_flight'] == 0
            assert client.asked('ping', ident='after')['id'] == 'after'
            assert slow not in client.got  # a cancelled request has no response


class TestTheWire:
    def test_a_fetch_a_write_a_wait_and_a_listen(self, wire):
        with serving(wire) as server, mcp(server) as client:
            fetched = client.asked('tools/call', {'name': 'fetch_station', 'arguments': {'station': 's2'}})
            moved = client.asked(
                'tools/call', {'name': 'invoke_move_result', 'arguments': {'player': 'ann', 'to': 's2'}}
            )
            waited = client.asked(
                'tools/call',
                {'name': 'wait_station', 'arguments': {'station': 's2', 'until': {'stock': 99}, 'timeout': 0.3}},
            )
            heard = client.asked(
                'tools/call', {'name': 'listen_notice', 'arguments': {'player': 'ann', 'timeout': 0.2}}
            )
        assert json.loads(text(fetched))[0]['stock'] == 5
        assert json.loads(text(moved))[0]['ok'] is True
        assert (waited['result']['isError'], json.loads(text(waited))['error']) == (True, 'timeout')  # exit 7
        assert (heard['result']['isError'], json.loads(text(heard))) == (False, [])
        assert json.loads(text(heard, 1))['note'] == '0 of the 1 values asked for came within 0.2s'


def serve(*argv, stdin=b'', env=None, timeout=60):
    """``python -m tooled serve``, as a harness launches it: its exit code, stdout's messages and stderr."""
    environ = {**os.environ, 'PYTHONPATH': str(TESTS), **(env or {})}
    done = subprocess.run(
        [sys.executable, '-m', 'tooled', 'serve', *argv],
        input=stdin,
        capture_output=True,
        env=environ,
        timeout=timeout,
        check=False,
    )
    messages = [json.loads(line) for line in done.stdout.decode().splitlines()]
    return done.returncode, messages, done.stderr.decode()


OPENING = (
    b'\n'.join(
        json.dumps(message).encode()
        for message in (
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {'protocolVersion': LEGACY[0], 'capabilities': {}},
            },
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {
                'jsonrpc': '2.0',
                'id': 2,
                'method': 'tools/call',
                'params': {'name': 'see_station_show', 'arguments': {'station': 's1'}},
            },
        )
    )
    + b'\n'
)


class TestServe:
    """``tooled serve`` as a process: attached to a session named, or to one of its own."""

    def test_attached_to_a_session(self, policy, runtime):
        with serving(policy, name='seat'):
            code, messages, _ = serve('--session', 'seat', stdin=OPENING)
        assert (code, [message['id'] for message in messages]) == (0, [1, 2])
        assert json.loads(messages[1]['result']['content'][0]['text'])['reply']['stock'] == 5

    def test_a_session_of_its_own(self, policy, runtime, monkeypatch):
        monkeypatch.setenv('DEMO_TICKET', 'tk-1')
        code, messages, _ = serve('--policy', policy, '--name', 'own', stdin=OPENING)
        assert (code, messages[1]['result']['isError']) == (0, False)
        from tooled.sessions import Where

        assert Where.of('own').record()['reason'] == 'stopped'  # closed as serve ended

    def test_a_session_that_is_not_open(self, runtime):
        code, messages, err = serve('--session', 'nowhere')
        assert (code, messages, json.loads(err)['error']) == (5, [], 'mesh')

    def test_another_policy_than_the_session_s(self, policy, runtime, tmp_path):
        other = write(tmp_path, '[protocol]\n\n[app]\nfactory = "_support_app:factory"\n', head='', name='other.toml')
        with serving(policy, name='seat'):
            code, _, err = serve('--session', 'seat', '--policy', str(other))
        assert (code, json.loads(err)['error']) == (2, 'usage')

    def test_picks_it_can_not_read(self, runtime):
        code, messages, err = serve('--session', 'seat', '--verbs', 'nope')
        assert (code, messages, json.loads(err)['error']) == (2, [], 'usage')
