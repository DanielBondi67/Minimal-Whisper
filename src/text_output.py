"""Normalization for text sent to keyboard-emulation backends."""


def normalize_transcription(text):
    """Keep recognized words while ensuring line breaks cannot press Enter."""
    for line_break in ('\r\n', '\r', '\n', '\u2028', '\u2029'):
        text = text.replace(line_break, ' ')
    return text.strip()
