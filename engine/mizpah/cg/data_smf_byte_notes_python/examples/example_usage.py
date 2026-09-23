import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.smf_byte_notes import read_smf


def vlq(value):
    return bytes([value & 0x7F])


track = b"".join([
    vlq(0) + bytes([0xFF, 0x58, 4, 4, 2, 24, 8]),
    vlq(0) + bytes([0xFF, 0x59, 2, 0, 0]),
    vlq(0) + bytes([0x90, 64, 70]),
    vlq(240) + bytes([0x80, 64, 0]),
    vlq(0) + bytes([0xFF, 0x2F, 0]),
])
data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + b"MTrk" + struct.pack(">I", len(track)) + track
division, meter, key, _tempos, _programs, channels, notes = read_smf(data)
print(division, meter, key, sorted(channels), notes)
