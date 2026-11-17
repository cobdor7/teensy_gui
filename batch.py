"""Compare completed batch runs without treating rejected frequencies as sensor failures."""
from collections import defaultdict


def compare_runs(runs, frequencies):
    grouped = defaultdict(list)
    rejected = defaultdict(list)
    for run in runs:
        for frequency in run['frequency_results']:
            hz = frequency['frequency_hz']
            if frequency['status'] == 'REJECTED':
                rejected[hz].append(run['run_number'])
                continue
            for sensor in frequency['sensors']:
                grouped[(hz, sensor['channel'])].append(sensor)

    def bounds(values):
        return [min(values), max(values)] if values else None

    comparison = []
    for hz in frequencies:
        for channel in range(1, 9):
            results = grouped[(hz, channel)]
            comparison.append({
                'frequency_hz': hz,
                'channel': channel,
                'evaluated_runs': len(results),
                'pass_count': sum(result['result'] == 'PASS' for result in results),
                'fail_count': sum(result['result'] == 'FAIL' for result in results),
                'rejected_runs': rejected[hz],
                'response_amplitude_range': bounds([result['response_amplitude'] for result in results]),
                'snr_db_range': bounds([result['snr_db'] for result in results]),
                'baseline_noise_rms_range': bounds([result['baseline_noise_rms'] for result in results]),
            })
    return comparison
