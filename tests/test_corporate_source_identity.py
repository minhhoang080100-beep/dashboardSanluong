"""Canonical identity policy; no network, database or mutable source state."""
from copy import deepcopy
from decimal import Decimal

import pytest

from backend.corporate_api.catalog_source import source_id
from backend.corporate_api.source_identity import MODE, decode_id, encode_id, mode


PREFIXED = {'source_identity_mode': MODE}


@pytest.mark.parametrize('profile', [None, {}, {'source_identity_mode': 'native'}])
def test_missing_policy_and_explicit_native_preserve_existing_mode(profile):
    assert mode(profile) == 'native'
    assert encode_id('cua_lo', 42, profile) == '42'
    assert decode_id('42', profile) == ('42', None)
    assert decode_id('42', profile, 'ben_thuy') == ('42', 'ben_thuy')


def test_explicit_prefix_policy_does_not_add_a_separate_approval_gate():
    assert mode(PREFIXED) == MODE
    assert mode({**PREFIXED, 'approved': False}) == MODE


@pytest.mark.parametrize('profile', [
    [], 'terminal_prefixed_v1', True, 1,
    {'source_identity_mode': None}, {'source_identity_mode': False},
    {'source_identity_mode': []}, {'source_identity_mode': {}},
    {'source_identity_mode': 'terminal_prefixed_v2'},
    {'source_identity_mode': ' terminal_prefixed_v1'},
    {'source_identity_mode': 'NATIVE'},
    {'source_identity_mode': 'Password=private-config'},
])
def test_invalid_configuration_fails_closed_without_echoing_values(profile):
    for action in (lambda: mode(profile), lambda: encode_id('cua_lo', 1, profile),
                   lambda: decode_id('1', profile)):
        with pytest.raises(ValueError, match='^Invalid source identity mode$'):
            action()


@pytest.mark.parametrize('terminal,prefix', [('cua_lo', 'CL'), ('ben_thuy', 'BT')])
@pytest.mark.parametrize('value', [1, '42', Decimal('73'), 2147483647, 9223372036854775807,
                                   '9999999999999999999'])
def test_native_parity_and_prefixed_round_trip(terminal, prefix, value):
    native = source_id(terminal, value)
    assert encode_id(terminal, value) == native
    public = encode_id(terminal, value, PREFIXED)
    assert public == f'{prefix}:{native}'
    assert decode_id(public, PREFIXED) == (native, terminal)
    assert decode_id(public, PREFIXED, terminal) == (native, terminal)


def test_equal_native_ids_have_distinct_public_ids_and_no_terminal_guess():
    assert encode_id('cua_lo', 2077, PREFIXED) == 'CL:2077'
    assert encode_id('ben_thuy', 2077, PREFIXED) == 'BT:2077'
    assert decode_id('2077') == ('2077', None)


@pytest.mark.parametrize('value', [
    None, True, False, 0, -1, 1.0, Decimal('1.0'), '', '0', '01', '00',
    '+1', '-1', '1.0', '1e3', ' 1', '1 ', '1\n', '1\x00',
    '١', '１', '1_000', '10000000000000000000', 10**19,
    'CL:1', 'BT:1', [], {}, b'1', 'Password=private-value',
])
def test_encoding_rejects_every_noncanonical_or_already_prefixed_native_id(value):
    with pytest.raises(ValueError):
        source_id('cua_lo', value)
    for profile in (None, PREFIXED):
        with pytest.raises(ValueError, match='^Invalid source identity$'):
            encode_id('cua_lo', value, profile)


@pytest.mark.parametrize('terminal', [None, '', 'CL', 'BT', 'Cua_lo', 'ben_thuy ', [], {}, True])
def test_encoding_requires_known_source_terminal_in_both_modes(terminal):
    for profile in (None, PREFIXED):
        with pytest.raises(ValueError, match='^Invalid source identity$'):
            encode_id(terminal, 1, profile)


@pytest.mark.parametrize('value', [
    '1', 'cl:1', 'bt:1', 'NT:1', 'CL:', 'BT:0', 'CL:01', 'CL:-1', 'BT:+1',
    'CL:1.0', 'CL:١', 'BT:１', 'CL:10000000000000000000',
    ' CL:1', 'CL:1 ', 'BT:1\n', 'CL:BT:1', 'CL:1:2', 'CL: 1',
    None, 1, True, [], {}, b'CL:1', 'Password=private-value',
])
def test_prefixed_decoding_requires_exact_public_syntax(value):
    with pytest.raises(ValueError, match='^Invalid source identity$'):
        decode_id(value, PREFIXED)


@pytest.mark.parametrize('value', ['CL:1', 'BT:1', ' 1', '1 ', '01', '0', '', 1, True, None])
def test_native_decoding_never_accepts_prefixed_or_non_string_public_ids(value):
    with pytest.raises(ValueError, match='^Invalid source identity$'):
        decode_id(value)


@pytest.mark.parametrize('public,terminal', [('CL:1', 'ben_thuy'), ('BT:1', 'cua_lo')])
def test_terminal_hint_cannot_redirect_an_id_to_the_other_database(public, terminal):
    with pytest.raises(ValueError, match='^Source identity does not match terminal$'):
        decode_id(public, PREFIXED, terminal)


@pytest.mark.parametrize('terminal', ['', 'CL', 'cua_lo ', [], {}, True])
def test_invalid_terminal_hint_is_not_ignored(terminal):
    for public, profile in [('1', None), ('CL:1', PREFIXED)]:
        with pytest.raises(ValueError, match='^Invalid source identity$'):
            decode_id(public, profile, terminal)


def test_calls_do_not_mutate_the_profile_or_other_configuration():
    profile = {**PREFIXED, 'source_columns': {'shipDetails': {'shipIMO': 'numberIMO'}}}
    before = deepcopy(profile)
    assert encode_id('cua_lo', 1, profile) == 'CL:1'
    assert decode_id('BT:1', profile) == ('1', 'ben_thuy')
    assert profile == before
