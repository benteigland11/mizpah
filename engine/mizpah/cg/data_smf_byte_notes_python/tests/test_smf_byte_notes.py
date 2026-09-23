import struct

import pytest

from src.smf_byte_notes import read_smf, read_sustain


def _vlq(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _track(events):
    body = b"".join(events)
    return b"MTrk" + struct.pack(">I", len(body)) + body


def _file(tracks, division=480):
    head = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), division)
    return head + b"".join(tracks)


def _meta(delta, kind, payload):
    return _vlq(delta) + bytes([0xFF, kind]) + _vlq(len(payload)) + payload


def test_reads_meter_key_tempo_and_a_note():
    track = _track([
        _meta(0, 0x58, bytes([3, 2, 24, 8])),
        _meta(0, 0x59, bytes([0xFF, 0])),
        _meta(0, 0x51, (500000).to_bytes(3, "big")),
        _vlq(0) + bytes([0xC0, 0]),
        _vlq(0) + bytes([0x90, 60, 80]),
        _vlq(480) + bytes([0x80, 60, 0]),
        _meta(0, 0x2F, b""),
    ])
    division, meter, key, tempos, programs, channels, notes = read_smf(_file([track]))
    assert (division, meter, key, programs, channels) == (480, "3/4", "F", [(0, 0)], {0})
    assert tempos == [(0, 120.0)]
    assert notes == [(0, 60, 0, 480, 80)]


def test_running_status_keeps_the_last_voice_event():
    track = _track([
        _vlq(0) + bytes([0x91, 48, 40]),
        _vlq(100) + bytes([52, 40]),
        _vlq(200) + bytes([0x81, 48, 0]),
        _vlq(0) + bytes([52, 0]),
        _meta(0, 0x2F, b""),
    ])
    notes = read_smf(_file([track]))[-1]
    assert notes == [(1, 48, 0, 300, 40), (1, 52, 100, 300, 40)]


def test_refuses_a_file_that_is_not_midi():
    with pytest.raises(ValueError):
        read_smf(b"not midi at all")


def test_sustain_keeps_tick_channel_and_value():
    track = _track([
        _vlq(0) + bytes([0xB0, 64, 127]),
        _vlq(240) + bytes([64, 0]),
        _vlq(0) + bytes([0xB1, 64, 100]),
        _meta(0, 0x2F, b""),
    ])
    assert read_sustain(_file([track])) == [(0, 0, 127), (240, 0, 0), (240, 1, 100)]
