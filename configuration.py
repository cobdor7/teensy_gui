"""Validate criteria and resolve common, channel, and frequency overrides."""
from copy import deepcopy
import math

CRITERIA = ('min_amplitude', 'min_snr_db', 'max_baseline_noise',
            'max_response_amplitude', 'clip_min', 'clip_max')
OPTIONAL = set(CRITERIA) - {'min_amplitude', 'min_snr_db'}
DEFAULT_SETTINGS = {
    'default_frequency_hz': 10, 'test_frequencies_hz': [],
    'baseline_settle_s': 0.5, 'baseline_capture_s': 2.0,
    'excite_settle_s': 0.5, 'initial_excite_extra_settle_s': 0.5, 'excite_capture_s': 2.0,
    'min_amplitude': 100.0, 'min_snr_db': 15.0,
    'max_baseline_noise': None, 'max_response_amplitude': None,
    'clip_min': None, 'clip_max': None,
    'channel_criteria': {}, 'frequency_criteria': {}, 'frequency_timings': {},
    'nominal_sample_rate_hz': 100,
    'min_receive_rate_hz': 60.0, 'max_receive_rate_hz': 200.0,
    'max_sample_gap_s': 0.1,
}


def number(value, name, lower=None, upper=None, integer=False):
    if isinstance(value, bool):
        raise ValueError(f'Invalid {name}')
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'Invalid {name}') from None
    if (not math.isfinite(value) or (lower is not None and value < lower)
            or (upper is not None and value > upper) or (integer and not value.is_integer())):
        raise ValueError(f'Invalid {name}')
    return int(value) if integer else value


def normalize_criteria(data, where):
    if not isinstance(data, dict):
        raise ValueError(f'{where} must be an object')
    output = {}
    for key, value in data.items():
        if key not in CRITERIA:
            raise ValueError(f'Unknown criterion {where}.{key}')
        if value is None and key in OPTIONAL:
            output[key] = None
        else:
            output[key] = number(value, f'{where}.{key}',
                                 0 if key in ('min_amplitude', 'max_response_amplitude',
                                              'max_baseline_noise') else None)
    return output


def channel_overrides(data, where):
    if not isinstance(data, dict):
        raise ValueError(f'{where} must be an object')
    result = {}
    for key, value in data.items():
        if not isinstance(key, str) or key not in {str(i) for i in range(1, 9)}:
            raise ValueError(f'Invalid channel {key} in {where}')
        result[key] = normalize_criteria(value, f'{where}.{key}')
    return result


def resolve_limits(settings, frequency, channel):
    limits = {key: settings[key] for key in CRITERIA}
    limits.update(settings['channel_criteria'].get(str(channel), {}))
    frequency_criteria = settings['frequency_criteria'].get(str(frequency), {})
    limits.update({key: value for key, value in frequency_criteria.items() if key != 'channels'})
    limits.update(frequency_criteria.get('channels', {}).get(str(channel), {}))
    if (limits['max_response_amplitude'] is not None
            and limits['min_amplitude'] > limits['max_response_amplitude']):
        raise ValueError(f'Channel {channel} at {frequency} Hz: minimum amplitude exceeds maximum')
    if (limits['clip_min'] is not None and limits['clip_max'] is not None
            and limits['clip_min'] >= limits['clip_max']):
        raise ValueError(f'Channel {channel} at {frequency} Hz: invalid signal limits')
    return limits


def resolve_timing(settings, frequency):
    timing = {key: settings[key] for key in ('excite_settle_s', 'excite_capture_s')}
    timing.update(settings['frequency_timings'].get(str(frequency), {}))
    return timing


def normalize_settings(data):
    if not isinstance(data, dict):
        raise ValueError('Settings must be an object')
    unknown = set(data) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f'Unknown settings: {", ".join(sorted(unknown))}')
    settings = deepcopy(DEFAULT_SETTINGS)
    settings.update(data)
    for key in ('baseline_settle_s', 'baseline_capture_s', 'excite_settle_s', 'excite_capture_s'):
        settings[key] = number(settings[key], key, 0.1, 30)
    settings['initial_excite_extra_settle_s'] = number(
        settings['initial_excite_extra_settle_s'], 'initial_excite_extra_settle_s', 0, 30)
    settings['default_frequency_hz'] = number(settings['default_frequency_hz'], 'default_frequency_hz', 1, 25, True)
    settings['nominal_sample_rate_hz'] = number(settings['nominal_sample_rate_hz'], 'nominal_sample_rate_hz', 10, 10_000, True)
    settings['min_receive_rate_hz'] = number(settings['min_receive_rate_hz'], 'min_receive_rate_hz', 1, 10_000)
    if settings['max_receive_rate_hz'] is not None:
        settings['max_receive_rate_hz'] = number(settings['max_receive_rate_hz'], 'max_receive_rate_hz', settings['min_receive_rate_hz'], 10_000)
    settings['max_sample_gap_s'] = number(settings['max_sample_gap_s'], 'max_sample_gap_s', 0.001, 30)
    if not isinstance(settings['test_frequencies_hz'], list):
        raise ValueError('test_frequencies_hz must be an array')
    frequencies = [number(f, 'test_frequencies_hz', 1, 25, True) for f in settings['test_frequencies_hz']]
    if len(frequencies) != len(set(frequencies)):
        raise ValueError('test_frequencies_hz contains duplicate frequencies')
    settings['test_frequencies_hz'] = frequencies
    settings.update(normalize_criteria({key: settings[key] for key in CRITERIA}, 'common'))
    if settings['min_amplitude'] > 1_000_000 or not -50 <= settings['min_snr_db'] <= 100:
        raise ValueError('Common amplitude or SNR exceeds the GUI control range')
    settings['channel_criteria'] = channel_overrides(settings['channel_criteria'], 'channel_criteria')
    freq_overrides = settings['frequency_criteria']
    if not isinstance(freq_overrides, dict):
        raise ValueError('frequency_criteria must be an object')
    normalized = {}
    for key, values in freq_overrides.items():
        if not isinstance(key, str) or key not in {str(i) for i in range(1, 26)} or not isinstance(values, dict):
            raise ValueError(f'Invalid frequency criterion {key}')
        normalized[key] = normalize_criteria({k: v for k, v in values.items() if k != 'channels'}, f'frequency_criteria.{key}')
        normalized[key]['channels'] = channel_overrides(values.get('channels', {}), f'frequency_criteria.{key}.channels')
    settings['frequency_criteria'] = normalized
    timings = settings['frequency_timings']
    if not isinstance(timings, dict):
        raise ValueError('frequency_timings must be an object')
    normalized_timings = {}
    for frequency, values in timings.items():
        if (not isinstance(frequency, str) or frequency not in {str(i) for i in range(1, 26)}
                or not isinstance(values, dict)):
            raise ValueError(f'Invalid frequency timing {frequency}')
        if set(values) - {'excite_settle_s', 'excite_capture_s'}:
            raise ValueError(f'Unknown timing for {frequency} Hz')
        normalized_timings[frequency] = {
            key: number(value, f'frequency_timings.{frequency}.{key}', 0.1, 30)
            for key, value in values.items()
        }
    settings['frequency_timings'] = normalized_timings
    for frequency in range(1, 26):
        for channel in range(1, 9):
            resolve_limits(settings, frequency, channel)
    return settings
