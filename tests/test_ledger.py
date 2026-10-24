import json
import re

import pytest

from tooled.errors import InvalidRequestError, TimedOutError
from tooled.ledger import LOG_ENV, Entry, append

KEYS = [
    'at',
    'verb',
    'route',
    'session',
    'operation',
    'tool',
    'variant',
    'arguments',
    'flags',
    'exit',
    'error',
    'path',
    'bytes',
    'seconds',
]


class TestEntry:
    def test_begins_with_the_verb_and_the_options_by_name(self):
        entry = Entry.begin(
            ['fetch', 'Station', 'station=s1', '--timeout=2', '--policy', 'p.toml', '--timeout', '3'], ['fetch']
        )
        assert (entry.verb, entry.flags) == ('fetch', ['--timeout', '--policy'])
        assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', entry.at)

    @pytest.mark.parametrize('argv', [['fetchh', 'Station'], ['--version'], []])
    def test_a_verb_tooled_does_not_have_is_none(self, argv):
        assert Entry.begin(argv, ['fetch']).verb is None

    def test_what_it_acted_on(self):
        entry = Entry()
        entry.acted_on('LookReply')
        entry.acted_on('LookReply', variant='ping')
        entry.acted_on('LookReply')  # a later step that knows no variant keeps it
        assert (entry.route, entry.operation, entry.variant) == ('wire', 'LookReply', 'ping')

    def test_bytes_count_utf8(self):
        entry = Entry()
        entry.wrote('[1]\n')
        entry.wrote('"✦"\n')
        assert entry.bytes == 4 + 6

    def test_the_end_with_an_error_s_kind_and_path(self):
        entry = Entry.begin(['call', 'wait_station'], ['call'])
        entry.end(4, InvalidRequestError('nope', path=('until', 'stok')))
        assert (entry.exit, entry.error, entry.path) == (4, 'validation', ['until', 'stok'])
        assert entry.seconds >= 0

    def test_an_error_with_no_path(self):
        entry = Entry()
        entry.end(7, TimedOutError('no value met', last={'secret': 'value'}))
        assert (entry.error, entry.path) == ('timeout', None)
        assert 'secret' not in json.dumps(entry.as_json())  # the last value is never kept

    def test_what_a_session_s_line_says_it_did(self):
        entry = Entry.begin(['see', '--session', 'seat-a', 'station', 's1', 'show'], ['see'])
        entry.session = 'seat-a'
        took = {'at': 'later', 'verb': 'see', 'flags': ['--session'], 'seconds': 9.0, 'exit': 4}
        took |= {'route': 'tree', 'operation': 'StationShow', 'arguments': ['station'], 'error': None, 'bytes': 120}
        entry.took(took)
        entry.end(0)
        line = entry.as_json()
        assert {key: line[key] for key in ('verb', 'route', 'session', 'operation', 'arguments', 'bytes')} == {
            'verb': 'see',
            'route': 'tree',
            'session': 'seat-a',
            'operation': 'StationShow',
            'arguments': ['station'],
            'bytes': 120,
        }
        assert (line['flags'], line['exit']) == (['--session'], 0)  # its own options, and how it ended here
        assert line['at'] != 'later'  # its own time
        assert line['seconds'] < 9

    def test_every_key_on_every_line(self):
        assert list(Entry().as_json()) == KEYS


class TestAppend:
    def test_nothing_without_the_variable(self, tmp_path):
        assert append(Entry(), environ={}) is None
        assert append(Entry(), environ={LOG_ENV: ''}) is None
        assert list(tmp_path.iterdir()) == []

    def test_one_line_a_run(self, tmp_path):
        log = tmp_path / 'calls.jsonl'
        for verb in ('fetch', 'wait'):
            entry = Entry.begin([verb], [verb])
            entry.end(0)
            assert append(entry, environ={LOG_ENV: str(log)}) is None
        lines = [json.loads(line) for line in log.read_text().splitlines()]
        assert [line['verb'] for line in lines] == ['fetch', 'wait']

    def test_a_home_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        assert append(Entry(), environ={LOG_ENV: '~/calls.jsonl'}) is None
        assert (tmp_path / 'calls.jsonl').exists()

    def test_a_log_it_can_not_write_is_a_note(self, tmp_path):
        target = tmp_path / 'missing' / 'calls.jsonl'
        note = append(Entry(), environ={LOG_ENV: str(target)})
        assert note == f'could not write the call log to {target}: No such file or directory'
