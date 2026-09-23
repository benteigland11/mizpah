"""Parse a Standard MIDI file from its bytes, with no MIDI library."""

import struct
from typing import Dict, List, Optional, Set, Tuple

Note = Tuple[int, int, int, int, int]
Pedal = Tuple[int, int, int]
Reading = Tuple[int, Optional[str], Optional[str], List[Tuple[int, float]], List[Tuple[int, int]], Set[int], List[Note]]


def _vlq(data: bytes, index: int) -> Tuple[int, int]:
    value = 0
    while True:
        byte = data[index]
        index += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index


def _key_name(sharps: int, minor: int) -> str:
    order = ["Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#"]
    major = order[sharps + 7]
    if not minor:
        return major
    relative = {"C": "A", "G": "E", "D": "B", "A": "F#", "E": "C#", "B": "G#", "F": "D",
                "Bb": "G", "Eb": "C", "Ab": "F", "Db": "Bb", "Gb": "Eb", "Cb": "Ab",
                "F#": "D#", "C#": "A#"}
    return relative[major] + "m"


def _tracks(data: bytes):
    header, length, _format, count, division = struct.unpack(">4sIHHH", data[:14])
    if header != b"MThd" or length < 6:
        raise ValueError("not a Standard MIDI file")
    offset = 8 + length
    for _ in range(count):
        mark, size = struct.unpack(">4sI", data[offset:offset + 8])
        if mark != b"MTrk":
            raise ValueError("track chunk missing")
        yield division, data[offset + 8:offset + 8 + size]
        offset += 8 + size


def read_smf(data: bytes) -> Reading:
    """Division, meter, key, tempo changes, programs, channels, sounding notes.

    Each program is ``(channel, program)``, in the order the file wrote them.
    """
    meter: Optional[str] = None
    key: Optional[str] = None
    tempos: List[Tuple[int, float]] = []
    programs: List[Tuple[int, int]] = []
    notes: List[Note] = []
    pending: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    division = 0
    for division, body in _tracks(data):
        index, tick, status = 0, 0, 0
        while index < len(body):
            delta, index = _vlq(body, index)
            tick += delta
            if body[index] & 0x80:
                status = body[index]
                index += 1
            kind = status & 0xF0
            if status == 0xFF:
                meta = body[index]
                size, index = _vlq(body, index + 1)
                meter, key = _meta(body, index, meta, meter, key)
                if meta == 0x51:
                    tempos.append((tick, round(60_000_000 / int.from_bytes(body[index:index + 3], "big"), 4)))
                index += size
            elif kind == 0xC0:
                programs.append((status & 0x0F, body[index]))
                index += 1
            elif kind == 0xD0:
                index += 1
            elif kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                _note(notes, pending, status, body[index], body[index + 1], tick)
                index += 2
            else:
                raise ValueError("unreadable status byte")
    return division, meter, key, tempos, programs, {note[0] for note in notes}, notes


def _meta(body, index, meta, meter, key):
    if meta == 0x58 and meter is None:
        meter = f"{body[index]}/{2 ** body[index + 1]}"
    elif meta == 0x59 and key is None:
        key = _key_name(int.from_bytes(body[index:index + 1], "big", signed=True), body[index + 1])
    return meter, key


def _note(notes, pending, status, pitch, velocity, tick):
    channel = status & 0x0F
    if status & 0xF0 == 0x90 and velocity:
        pending.setdefault((channel, pitch), []).append((tick, velocity))
    elif status & 0xF0 in (0x80, 0x90) and pending.get((channel, pitch)):
        start, vel = pending[(channel, pitch)].pop(0)
        notes.append((channel, pitch, start, tick, vel))


def read_sustain(data: bytes, controller: int = 64) -> List[Pedal]:
    """Sustain-pedal events as (tick, channel, value), in file order.

    ``controller`` selects the control-change number. Notes and meta events
    are walked only so the tick and running status stay honest.
    """
    events: List[Pedal] = []
    for _division, body in _tracks(data):
        index, tick, status = 0, 0, 0
        while index < len(body):
            delta, index = _vlq(body, index)
            tick += delta
            if body[index] & 0x80:
                status = body[index]
                index += 1
            kind = status & 0xF0
            if status == 0xFF:
                size, index = _vlq(body, index + 1)
                index += size
            elif kind == 0xC0 or kind == 0xD0:
                index += 1
            elif kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                if kind == 0xB0 and body[index] == controller:
                    events.append((tick, status & 0x0F, body[index + 1]))
                index += 2
            else:
                raise ValueError("unreadable status byte")
    return events
