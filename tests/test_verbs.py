import os

import pytest
import seared as s
from parsed.tree import Arity

from tooled import verbs
from tooled.cli import _LINE_VALUES, LINES, VERBS
from tooled.errors import TooledError, UsageError


def given(*argv):
    return verbs.read(list(argv))


#: A field a kind reads for: tooled's kinds read alike for any.
FLOAT = s.Float(default=None)


class TestKinds:
    @pytest.mark.parametrize(('word', 'seconds'), [('3', 3.0), ('0.5', 0.5), ('120', 120.0)])
    def test_seconds_are_a_bare_number(self, word, seconds):
        assert verbs.Seconds().read(word, FLOAT) == seconds

    @pytest.mark.parametrize('word', ['0', '-1', 'x', 'nan', 'inf', '3s'])
    def test_seconds_above_zero_alone(self, word):
        with pytest.raises(ValueError, match=f'a timeout is a number of seconds above zero, not {word}'):
            verbs.Seconds().read(word, FLOAT)

    @pytest.mark.parametrize(('word', 'seconds'), [('30', 30.0), ('30s', 30.0), ('1.5m', 90.0), ('1h', 3600.0)])
    def test_durations(self, word, seconds):
        assert verbs.Duration().read(word, FLOAT) == seconds

    @pytest.mark.parametrize('word', ['0', 'soon', 'inf', '5d'])
    def test_a_duration_above_zero(self, word):
        with pytest.raises(ValueError, match='a duration is seconds above zero, or with s, m or h'):
            verbs.Duration().read(word, FLOAT)

    @pytest.mark.parametrize('word', ['0', '0s', '0m', '0h'])
    def test_a_never_s_zero(self, word):
        assert verbs.Duration(never=True).read(word, FLOAT) == 0.0

    @pytest.mark.parametrize(
        ('kind', 'word', 'message'),
        [
            (verbs.Whole(), '0', 'a count is a whole number above zero, not 0'),
            (verbs.Whole(), 'x', 'a count is a whole number above zero, not x'),
            (verbs.Whole(least=0, what_it_is='a cap'), '-1', 'a cap is a whole number zero or more, not -1'),
            (verbs.Process(), '0', 'no process 0 is running'),
            (verbs.Selected(), 'a..b', '--select takes dotted paths'),
            (verbs.Condition(), 'stock', '--until is path=value'),
            (verbs.Picks(), 'fetch,publish', "each is a verb, or verb:GLOB, of fetch, .*; not 'publish'"),
            (verbs.Form(), 'xml', "'xml' is no form tooled writes: openai, anthropic, mcp"),
        ],
    )
    def test_what_tooled_refuses_it_says(self, kind, word, message):
        with pytest.raises(ValueError, match=message):
            kind.read(word, s.Str(default=None))

    @pytest.mark.parametrize(
        ('kind', 'word'),
        [
            (verbs.Selected(), 'a.b,list[].key'),
            (verbs.Condition(), 'numbers.fuel=40'),
            (verbs.Picks(), 'fetch, wait:Fleet*'),
            (verbs.Form(), 'mcp'),
        ],
    )
    def test_what_is_checked_is_kept_as_written(self, kind, word):
        assert kind.read(word, s.Str(default=None)) == word

    def test_a_process_running_is_its_id(self):
        assert verbs.Process().read(str(os.getpid()), s.Int(default=None)) == os.getpid()

    def test_a_cap_may_be_zero(self):
        assert verbs.Whole(least=0, what_it_is='a cap').read('0', s.Int(default=None)) == 0


class TestRead:
    def test_a_verb_and_its_flags(self):
        read = given('fetch', 'Station', 'station=s1', '--timeout', '2', 'world=w1', '--select', 'stock')
        assert isinstance(read.command, verbs.Fetch)
        assert (read.command.slots, read.command.timeout, read.command.select) == (
            ['station=s1', 'world=w1'],
            2.0,
            'stock',
        )

    @pytest.mark.parametrize(
        ('argv', 'timeout'),
        [
            (['fetch', 'Station'], verbs.ASKED),
            (['invoke', 'Move'], verbs.ASKED),
            (['wait', 'Station', '--until', 'stock=1'], verbs.WAITED),
            (['see', 'station', 's1', 'show'], verbs.LINE_BOUND),
        ],
    )
    def test_default_timeouts(self, argv, timeout):
        assert given(*argv).command.timeout == timeout

    @pytest.mark.parametrize(
        'argv',
        [
            ['call', 'fetch_station', '--args', '{"station": "s1", "note": "say \\"hi\\""}'],
            ['call', 'fetch_station', '--args={"station": "s1", "note": "say \\"hi\\""}'],
        ],
    )
    def test_json_arguments_read_as_given(self, argv):
        assert given(*argv).command.args == '{"station": "s1", "note": "say \\"hi\\""}'

    def test_a_flag_s_own_name_by_its_data_key(self):
        assert given('listen', 'Notice', '--for', '5m').command.duration == 300.0
        assert given('session', 'start', '--for', str(os.getpid())).command.tied == os.getpid()

    def test_dry_run_is_parsed_s_own_given_invoke(self):
        assert given('invoke', 'Move', '--dry-run').own == {'dry-run'}

    @pytest.mark.parametrize(
        ('argv', 'message'),
        [
            (['fetch', 'Station', '--yes'], 'tooled fetch takes no --yes'),
            (['query', 'LookReply', '--dry-run'], 'tooled query takes no --dry-run'),
            (['session', 'status', '--json'], 'tooled session status takes no --json'),
        ],
    )
    def test_parsed_s_own_flags_where_a_verb_takes_none(self, argv, message):
        with pytest.raises(UsageError, match=message):
            given(*argv)

    def test_the_version(self):
        assert given('--version').version

    @pytest.mark.parametrize('argv', [['--help'], ['-h']])
    def test_the_program_s_help(self, argv):
        assert given(*argv).help == verbs.program_help()

    @pytest.mark.parametrize(
        ('argv', 'begins'),
        [
            (['fetch', '--help'], 'fetch <contract>'),
            (['fetch', 'Station', '-h'], 'fetch <contract>'),
            (['see', '--help'], 'see [--policy <path>]'),
            (['session', 'run', '--help'], 'session run [--timeout <seconds>]'),
            (['session', '--help'], 'session start'),
        ],
    )
    def test_a_verb_s_help(self, argv, begins):
        assert given(*argv).help.startswith(begins)

    def test_help_for_words_that_go_nowhere_is_refused(self):
        with pytest.raises(UsageError, match="no command 'nope'"):
            given('nope', '--help')


class TestLine:
    def test_the_line_s_flags_are_its_own(self):
        read = given('see', '--policy', 'p.toml', 'clock', 'show', '--until', 'tick=3', '--timeout', '5s', '--help')
        assert (str(read.command.policy), read.command.timeout) == ('p.toml', verbs.LINE_BOUND)
        assert read.line == 'clock show --until tick=3 --timeout 5s --help'

    def test_parsed_s_own_flags_before_it_are_put_back(self):
        assert given('do', '--yes', '--dry-run', 'dock', 'vesta').line == '--yes --dry-run dock vesta'

    def test_a_line_given_as_one_argument_is_that_line(self):
        assert given('do', 'fleet f1 mine').line == 'fleet f1 mine'

    def test_a_word_with_a_space_stays_one_word(self):
        assert given('do', 'move', 'Vesta Ring').line == 'move "Vesta Ring"'

    def test_dashes_end_tooled_s_options(self):
        assert given('see', '--', '--help').line == '--help'

    def test_an_empty_line(self):
        assert given('see', '').line == ''

    def test_session_run_s_command_as_its_arguments(self):
        read = given('session', 'run', '--', 'sh', '-c', 'echo "$X"\nprintf "a b"')
        assert read.words == ['sh', '-c', 'echo "$X"\nprintf "a b"']


class TestTheVerbs:
    def test_each_is_one_of_the_cli_s(self):
        words = [verbs.verb_of(cls) for cls in verbs.COMMANDS]
        assert sorted([*words, 'session']) == sorted(VERBS)

    def test_the_line_s_verbs(self):
        assert sorted(verbs.verb_of(cls) for cls in verbs.COMMANDS if issubclass(cls, verbs.Lined)) == sorted(LINES)

    def test_the_line_s_flags_that_take_a_value(self):
        tree = verbs.app().mounts[0].tree
        for cls in (verbs.See, verbs.Do):
            flags = tree.flags_of(cls).items()
            assert {f'--{name}' for name, flag in flags if flag.arity is Arity.VALUE} == _LINE_VALUES

    def test_session_s_verbs(self):
        assert [verbs.verb_of(cls) for cls in verbs.SESSION] == [
            'session start',
            'session run',
            'session stop',
            'session status',
        ]


def test_the_help_names_every_exit_code():
    codes = {cls.exit_code for cls in TooledError.__subclasses__()}
    assert codes == {2, 3, 4, 5, 6, 7}
    for code in (0, *codes):
        assert f'\n  {code}  ' in verbs.EPILOG


def test_the_program_s_help_lists_every_verb():
    listed = verbs.program_help()
    for cls in verbs.COMMANDS:
        assert f'\n  {verbs.verb_of(cls)} ' in listed
    assert '\n  session …  ' in listed
