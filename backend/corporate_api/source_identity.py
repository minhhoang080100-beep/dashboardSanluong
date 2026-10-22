"""Opt-in source-qualified S API IDs; no source queries or resource mappings.

Native mode preserves the existing positive 1-to-19-digit source ID contract.
The prefixed mode gives each terminal a distinct public namespace while keeping
the native ID intact. Decoding never infers a terminal for an unqualified ID.
"""
import re


MODE = 'terminal_prefixed_v1'
NATIVE_MODE = 'native'
_PREFIXES = {'cua_lo': 'CL', 'ben_thuy': 'BT'}
_TERMINALS = {prefix: terminal for terminal, prefix in _PREFIXES.items()}
_NATIVE = re.compile(r'[1-9][0-9]{0,18}')
_QUALIFIED = re.compile(r'(CL|BT):([1-9][0-9]{0,18})')


def mode(profile=None):
    """Return the explicit policy, defaulting only an absent policy to native."""
    if profile is None:
        return NATIVE_MODE
    if not isinstance(profile, dict):
        raise ValueError('Invalid source identity mode')
    selected = profile.get('source_identity_mode', NATIVE_MODE)
    if not isinstance(selected, str) or selected not in {NATIVE_MODE, MODE}:
        raise ValueError('Invalid source identity mode')
    return selected


def _prefix(terminal):
    if not isinstance(terminal, str) or terminal not in _PREFIXES:
        raise ValueError('Invalid source identity')
    return _PREFIXES[terminal]


def _native_id(value):
    # Match the established source_id serialization exactly: no trimming,
    # floats, signs, zero padding, zero, or booleans masquerading as integers.
    if isinstance(value, bool):
        raise ValueError('Invalid source identity')
    native = str(value)
    if _NATIVE.fullmatch(native) is None:
        raise ValueError('Invalid source identity')
    return native


def encode_id(terminal, value, profile=None):
    """Encode a native source ID; already-qualified IDs are never re-encoded."""
    selected = mode(profile)
    prefix = _prefix(terminal)
    native = _native_id(value)
    return f'{prefix}:{native}' if selected == MODE else native


def decode_id(value, profile=None, terminal=None):
    """Return ``(native_id, source_terminal)`` for a canonical public ID.

    In native mode the terminal is the caller's optional validated hint, or
    None. Prefixed mode always returns the encoded terminal and rejects a hint
    that disagrees with it. ``value`` must be a string in either mode.
    """
    selected = mode(profile)
    if terminal is not None:
        _prefix(terminal)
    if not isinstance(value, str):
        raise ValueError('Invalid source identity')
    if selected == NATIVE_MODE:
        return _native_id(value), terminal
    matched = _QUALIFIED.fullmatch(value)
    if matched is None:
        raise ValueError('Invalid source identity')
    source_terminal = _TERMINALS[matched.group(1)]
    if terminal is not None and terminal != source_terminal:
        raise ValueError('Source identity does not match terminal')
    return matched.group(2), source_terminal
