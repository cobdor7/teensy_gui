"""Wire protocol for the preflashed Teensy; never uploads firmware."""
from __future__ import annotations


def parse_sample(line: str) -> tuple[float, ...] | None:
    """Parse eight CSV floats, including the firmware's trailing comma.

    Command acknowledgements and partial/garbled lines are ignored.
    """
    try:
        fields = [item.strip() for item in line.strip().rstrip(',').split(',')]
        if len(fields) != 8:
            return None
        return tuple(float(item) for item in fields)
    except (ValueError, OverflowError):
        return None


def excitation_command(frequency_hz: int) -> str:
    if not 1 <= frequency_hz <= 25:
        raise ValueError('Excitation must be between 1 and 25 Hz')
    return f'response:{frequency_hz}'
