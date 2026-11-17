# Sensor test station — written report

## Sensor output evaluation

On November 16, 2026, the preflashed Teensy was tested on Windows COM6. This 10 Hz GUI run used 0.5 s settling, 2 s captures, and provisional minimum limits of 100 sensor units peak amplitude and 15 dB SNR.

| Sensor | Baseline noise RMS | Response amplitude, peak | SNR (dB) | Result |
| --- | ---: | ---: | ---: | --- |
| 1 | 28.95 | 12.17 | -10.5 | FAIL |
| 2 | 19.65 | 998.56 | 31.1 | PASS |
| 3 | 11.47 | 895.07 | 34.8 | PASS |
| 4 | 190.15 | 1202.28 | 13.0 | FAIL |
| 5 | 14.60 | 799.20 | 31.8 | PASS |
| 6 | 26.64 | 927.03 | 27.8 | PASS |
| 7 | 8.92 | 1016.11 | 38.1 | PASS |
| 8 | 23.07 | 7.42 | -12.9 | FAIL |

Channels 1 and 8 have weak responses; channel 4's high baseline noise causes its SNR failure. The same channels failed at every evaluated frequency in a separate 1–19 Hz run. The firmware rejected 20–25 Hz, despite the specified 1–25 Hz range; these are **test errors**, not sensor failures. The simulated data and exploratory limits do not establish physical faults or production acceptance. Evidence: `runs/test_20261116T213246_069583Z.json` and `runs/test_20261116T205808_625356Z.json`, with matching raw CSVs. `runs/` is local and excluded from version control.

## Assumptions, signal model, and AC coupling

Each channel is modeled as an offset plus noise and an approximately sinusoidal excited response: `x(t) = c + a sin(2πft) + b cos(2πft) + n(t)`. The plant is assumed to respond repeatably at the commanded frequency. Actuator amplitude, transfer function, and sensor sensitivity are not calibrated; a command is not independent actuator feedback.

The exercise describes an AC-coupled output, but no physical coupling circuit or cutoff is specified. The client subtracts the baseline mean for noise and fits a constant offset `c` in the response. The baseline-referenced chart also subtracts the baseline mean. This is **software DC-offset removal**, not a modeled high-pass filter. A physical circuit's cutoff and settling need characterization, especially near 1 Hz, where filtering could attenuate or phase-shift the response.

## Evaluation and timing

Baseline noise is the sample standard deviation after mean subtraction. The least-squares fit gives peak amplitude `A = sqrt(a² + b²)`. SNR is `20 log10[(A / sqrt(2)) / baseline_noise_RMS]`, comparing fitted signal RMS with baseline noise rather than excited-state residual noise. A channel passes when amplitude and SNR meet their minimums and any enabled noise, amplitude, and sample-range limits. Limits are configurable globally or per channel and frequency; optional limits default to disabled. A range violation does not prove physical clipping.

The 10 Hz run received 300 samples per phase at about 150.0 rows/s, versus the nominal 100 Hz. The app uses host `time.perf_counter()` receive timestamps for fitting and rate diagnostics. USB buffering and scheduling can distort them, so they do not establish the device sampling clock or every lost sample. Too few or malformed samples, excessive gaps, out-of-range receive rates, and captures shorter than two cycles invalidate the test. Settling and capture times are configurable by frequency.

## Sequence, threading, and traceability

```text
IDLE → request baseline → settle → capture baseline
     → [request frequency → settle → capture → evaluate] × N → PASS / FAIL
                              ↘ rejected → baseline → next frequency (final TEST ERROR)
```

Connection loss, invalid data, or write timeout stops the run with TEST ERROR; an operator abort stops it as ABORTED. The client requests `baseline` after completion, rejection, error, or abort when connected. The actuator indicator is the last command sent, not a verified physical state. Batch mode repeats the sequence with a fresh baseline and compares completed runs.

A serial `QThread` reads and writes through a thread-safe command queue. A 20 ms GUI timer advances the sequence; a 50 ms timer redraws plots. Neither controls sampling. Completed runs save timestamped CSV and JSON; batch summaries preserve completed runs if interrupted. Captured charts can be baseline-referenced and exported to PNG. Automated tests cover protocol, analysis, configuration, sequencing, logging, GUI, export, and simulated serial; real-device checks were separate.

## Limitations and next steps

Investigate the 20–25 Hz rejection without overwriting the supplied firmware, and obtain calibrated limits, actuator feedback, and device-side timestamps or sequence numbers. Add tests for distortion and phase, validate on Linux and macOS, and review the visible desktop and packaged app. Single-run interrupted raw captures and device identifiers are not persisted; add those for traceability. Historical chart reopening and an installer are also not implemented. A digital AC-coupling filter could be added after choosing and documenting its cutoff and settling trade-offs.
