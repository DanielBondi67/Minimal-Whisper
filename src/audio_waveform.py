"""Small helpers for extracting live waveform levels from PCM audio."""

import math
import struct


def wav_data_offset(stream):
    """Return the offset of the PCM data chunk in a RIFF/WAVE stream."""
    stream.seek(0)
    if stream.read(12)[0:4] != b'RIFF':
        return None
    stream.seek(8)
    if stream.read(4) != b'WAVE':
        return None

    offset = 12
    while True:
        stream.seek(offset)
        chunk = stream.read(8)
        if len(chunk) != 8:
            return None
        chunk_id, chunk_size = chunk[:4], struct.unpack('<I', chunk[4:])[0]
        if chunk_id == b'data':
            return offset + 8
        offset += 8 + chunk_size + (chunk_size & 1)


def pcm_waveform_levels(data, count=17):
    """Return perceptually scaled RMS levels for consecutive 16-bit PCM slices."""
    sample_count = len(data) // 2
    if sample_count == 0 or count <= 0:
        return [0.0] * max(0, count)
    samples = struct.unpack(f'<{sample_count}h', data[:sample_count * 2])
    levels = []
    for index in range(count):
        start = index * sample_count // count
        end = max(start + 1, (index + 1) * sample_count // count)
        end = min(end, sample_count)
        if start >= sample_count:
            levels.append(0.0)
            continue
        rms = math.sqrt(sum(sample * sample for sample in samples[start:end]) /
                        (end - start)) / 32768
        levels.append(round(math.sqrt(rms), 4))
    return levels
