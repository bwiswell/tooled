import base64
import hashlib

import pytest
import seared as s

from parsed.errors import TooLargeError, UsageError
from parsed.output import (
    DEFAULT_MAX_BYTES,
    MAX_BYTES_ENV,
    as_json,
    check_size,
    dump,
    max_bytes,
    parse_paths,
    parts,
    pick,
    select,
    summarize_bytes,
    write_out,
)


@s.seared
class Thumb(s.Seared):
    image: bytes = s.Bytes(required=True)


@s.seared
class Album(s.Seared):
    title: str = s.Str(required=True)
    cover: bytes | None = s.Bytes(default=None)
    pages: list[bytes] = s.Bytes(many=True, default_factory=list)
    named: dict[str, bytes] = s.Bytes(keyed=True, default_factory=dict)
    thumbs: list[Thumb] = s.T(Thumb, many=True, default_factory=list)


def digest(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


class TestBytes:
    def test_every_bytes_field_shows_its_length_and_hash(self):
        album = Album(title='t', cover=b'abc', pages=[b'1', b'22'], named={'a': b'x'}, thumbs=[Thumb(image=b'img')])
        assert dump(Album, album) == {
            'title': 't',
            'cover': digest(b'abc'),
            'pages': [digest(b'1'), digest(b'22')],
            'named': {'a': digest(b'x')},
            'thumbs': [{'image': digest(b'img')}],
        }

    def test_an_absent_field_and_a_non_seared_class_are_left_alone(self):
        assert dump(Album, Album(title='t')) == {'title': 't', 'pages': [], 'named': {}, 'thumbs': []}
        assert summarize_bytes(int, {'a': 1}) == {'a': 1}

    def test_a_value_that_is_not_base64_is_left_alone(self):
        assert summarize_bytes(Album, {'title': 't', 'cover': 'not base64!'})['cover'] == 'not base64!'
        assert summarize_bytes(Album, {'title': 't', 'cover': base64.b64encode(b'ok').decode()})['cover'] == digest(
            b'ok'
        )


class TestPaths:
    def test_dotted_and_mapped(self):
        assert parse_paths('a.b, list[].key,c') == [
            [('a', False), ('b', False)],
            [('list', True), ('key', False)],
            [('c', False)],
        ]

    @pytest.mark.parametrize('text', ['', 'a..b', 'a[', 'a[0]', 'a.[].b', '[]'])
    def test_malformed_paths(self, text):
        with pytest.raises(UsageError, match='dotted paths'):
            parse_paths(text)


DOC = {'name': 'm', 'size': {'w': 3, 'h': 4}, 'systems': [{'key': 'a', 'owner': 'x'}, {'key': 'b'}], 'n': [1, 2]}


class TestSelect:
    def test_keeps_parts_with_their_structure(self):
        assert select(DOC, parse_paths('name,size.w')) == {'name': 'm', 'size': {'w': 3}}

    def test_maps_over_a_list(self):
        assert select(DOC, parse_paths('systems[].key')) == {'systems': [{'key': 'a'}, {'key': 'b'}]}

    def test_an_element_without_the_rest_is_left_empty(self):
        assert select(DOC, parse_paths('systems[].owner')) == {'systems': [{'owner': 'x'}, {}]}

    def test_paths_merge(self):
        got = select(DOC, parse_paths('systems[].key,systems[].owner,size.h'))
        assert got == {'systems': [{'key': 'a', 'owner': 'x'}, {'key': 'b'}], 'size': {'h': 4}}

    def test_a_whole_list(self):
        assert select(DOC, parse_paths('n[]')) == {'n': [1, 2]}

    def test_each_item_of_a_list_result(self):
        rows = [{'a': 1, 'b': 2}, {'a': 3}]
        assert select(rows, parse_paths('a')) == [{'a': 1}, {'a': 3}]

    @pytest.mark.parametrize('text', ['nope', 'name.inner', 'size[].w', 'systems[].nope'])
    def test_a_path_that_matches_nothing_is_refused(self, text):
        with pytest.raises(UsageError, match='matches nothing in the result'):
            select(DOC, parse_paths(text))

    def test_an_empty_result_refuses_nothing(self):
        assert select([], parse_paths('nope')) == []

    def test_pick_refuses_nothing_and_says_what_matched(self):
        assert pick(DOC, parse_paths('name,nope')) == ({'name': 'm'}, [True, False])


class TestCap:
    def test_the_cap_comes_from_the_flag_the_environment_or_the_default(self, monkeypatch):
        monkeypatch.delenv(MAX_BYTES_ENV, raising=False)
        assert max_bytes(None) == DEFAULT_MAX_BYTES == 28_000
        assert max_bytes(100) == 100
        monkeypatch.setenv(MAX_BYTES_ENV, '500')
        assert max_bytes(None) == 500
        assert max_bytes(0) == 0

    @pytest.mark.parametrize('value', ['lots', '-1'])
    def test_a_bad_environment_value(self, monkeypatch, value):
        monkeypatch.setenv(MAX_BYTES_ENV, value)
        with pytest.raises(UsageError, match=MAX_BYTES_ENV):
            max_bytes(None)

    def test_over_the_cap_is_refused_with_each_part_s_size(self):
        value = {'small': 'x', 'big': 'y' * 200}
        with pytest.raises(TooLargeError) as exc:
            check_size(as_json(value, indent=None), value, cap=100)
        assert exc.value.exit_code == 6
        assert exc.value.to_json()['parts'] == {'bytes': {'big': 202, 'small': 3}}
        assert exc.value.to_json()['cap'] == 100

    def test_under_the_cap_or_no_cap(self):
        check_size('x' * 100, None, cap=100)
        check_size('x' * 10_000, None, cap=0)

    def test_the_cap_counts_bytes_not_characters(self):
        with pytest.raises(TooLargeError):
            check_size('é' * 60, None, cap=100)  # 120 bytes

    def test_parts_of_a_list_sum_each_key(self):
        assert parts([{'a': 'xx', 'b': 1}, {'a': 'yyyy'}, 3]) == {'items': 3, 'bytes': {'a': 10, 'b': 1}}


class TestOut:
    def test_writes_the_whole_text(self, tmp_path):
        target = tmp_path / 'map.json'
        assert write_out(str(target), '{"a": 1}') == {'written': str(target.resolve()), 'bytes': 9}
        assert target.read_text() == '{"a": 1}\n'

    def test_a_place_it_cannot_write(self, tmp_path):
        with pytest.raises(UsageError, match='could not write'):
            write_out(str(tmp_path / 'no' / 'such' / 'dir' / 'x.json'), '{}')
