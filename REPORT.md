# Sensor test station — written report

## Sensor output evaluation

On November 16, 2026, the source app tested the preflashed Teensy on Windows COM5 at every integer frequency from **1 through 25 Hz**. Baseline and response captures were two seconds each; settling was one second for the first excitation and 0.5 seconds for later frequencies. Provisional criteria were a minimum peak amplitude of 100 sensor units and minimum SNR of 15 dB; raw-signal limits were disabled.

| Sensor | Baseline noise RMS | Peak amplitude range, 1–19 Hz | SNR range, 1–19 Hz (dB) | Results, 1–19 Hz |
| --- | ---: | ---: | ---: | --- |
| 1 | 27.67 | 5.39–16.32 | −17.2 to −7.6 | 0 pass, 19 fail |
| 2 | 19.98 | 986.42–1002.17 | 30.9 to 31.0 | 19 pass, 0 fail |
| 3 | 11.46 | 886.78–906.07 | 34.8 to 35.0 | 19 pass, 0 fail |
| 4 | 174.27 | 1180.68–1232.62 | 13.6 to 14.0 | 0 pass, 19 fail |
| 5 | 14.31 | 785.50–802.93 | 31.8 to 32.0 | 19 pass, 0 fail |
| 6 | 25.94 | 913.27–934.26 | 27.9 to 28.1 | 19 pass, 0 fail |
| 7 | 8.66 | 1005.97–1024.28 | 38.3 to 38.4 | 19 pass, 0 fail |
| 8 | 22.30 | 1.02–12.58 | −29.8 to −8.0 | 0 pass, 19 fail |

Every frequency from 1–19 Hz produced the same pass/fail pattern: channels 1, 4, and 8 failed, while channels 2, 3, 5, 6, and 7 passed. Channels 1 and 8 had little fitted response; channel 4 had a large response but high baseline noise and failed the SNR criterion. The table shows the minimum and maximum measured values across the 19 evaluated frequencies, not just the endpoints. These results use exploratory thresholds and do not establish physical defects or calibrated production acceptance.

Further analysis of the raw captures found that sensor 8 is not simply inactive: its response is dominated by the **second harmonic**, at twice the commanded frequency. In the 10 Hz capture, a simultaneous fit gives approximately 7 sensor units at 10 Hz and 888 at 20 Hz; similar doubling appears at every evaluated frequency. The application's existing pass/fail metric fits only the commanded frequency, so it flags the weak fundamental but does not diagnose the harmonic. Whether doubling is expected in this simulated channel requires confirmation.

Across eight saved full sweeps using these criteria, sensors 1 and 4 failed all 152 evaluated frequency captures; sensor 8 failed 146 and passed six. Sensors 2, 3, 5, 6, and 7 passed 151 captures and failed together in one. Some inconsistent results coincided with irregular host receive timing, but the logs alone do not establish the cause. These are repeated measurements, not eight independently identified physical units.

The device explicitly rejected **each of 20, 21, 22, 23, 24, and 25 Hz**, despite the specified range. No sensor results were produced for those frequencies, and the overall run was **TEST ERROR**, distinct from a sensor failure. Per-frequency metrics and raw samples are in `runs/test_20261117T034108_557374Z.json` and the matching CSV. These generated files are local and excluded from Git. The hardware GUI check ran offscreen; it did not constitute a visual usability review.

## Assumptions, plant, and AC coupling

Each sensor is modeled as an offset plus noise and an approximately sinusoidal response at frequency `f`: `x(t) = c + a sin(2πft) + b cos(2πft) + n(t)`. The plant is assumed to produce a repeatable component at the commanded frequency. Actuator motion, transfer function, and sensor sensitivities are not calibrated; an acknowledgement does not verify physical motion.

Baseline mean removal excludes DC from the noise calculation, and the response fit includes a constant offset. The baseline-referenced chart subtracts the baseline mean. This software DC removal does not reproduce a physical AC-coupling high-pass filter; no cutoff was specified. A real circuit can attenuate or phase-shift low frequencies, especially near 1 Hz.

## State machine and operator flow

```text
IDLE → request OFF → baseline settle → baseline capture
     → [request frequency → settle → capture → evaluate] × N → PASS / FAIL
                  └→ rejected → request OFF → next frequency → TEST ERROR
```

The GUI reports state and frequency and starts settling after the serial worker reports the command written. The first excitation receives an extra configurable 0.5 seconds. Rejected frequencies are recorded; any rejection makes the overall result TEST ERROR. Invalid captures, disconnection, and write timeouts stop the test; operator abort produces ABORTED. When connected, the client requests `baseline` at completion, rejection, error, or abort. The ON/OFF indicator reflects the last command sent. Batch mode takes a fresh baseline for each run.

## Threading, timers, and rendering

A `QThread` owns the serial port, receives commands through a thread-safe queue, and sends timestamped rows to the GUI via Qt signals. A 20 ms timer advances the state machine; a separate 50 ms timer redraws rolling plots. Neither determines device sampling rate. Captured data is separate from display buffers; results and raw captures are saved as JSON and CSV.

The observed host receive rate was approximately 149.7–150.5 rows/s across evaluated frequencies, versus the specified nominal 100 Hz. Host timestamps support fitting and gap checks, but USB buffering and operating-system scheduling can distort timing; without device timestamps or sequence numbers, they cannot prove the true sampling interval or detect every lost row. The initial settling delay was exercised in single and batch runs, but its adequacy has not been established with independent actuator measurements.

## Evaluation metrics

For each channel, baseline noise is the sample standard deviation after mean removal. Least-squares fitting of the excited samples estimates `a`, `b`, and `c`; peak response amplitude is `A = sqrt(a² + b²)`. SNR is `20 log10[(A / sqrt(2)) / baseline_noise_RMS]`: fitted sinusoidal RMS compared with baseline noise, not excited-state residual noise.

A channel passes when amplitude and SNR meet their minimums and any enabled maximum noise, maximum amplitude, and raw-value boundaries are satisfied. A sample at or beyond an enabled boundary fails the channel; this check alone does not prove physical clipping. Common thresholds and signal limits are editable in the GUI, with channel and frequency overrides available in the settings file. Captures with malformed or insufficient data, excessive timing gaps, or out-of-range receive rates are test errors. Each excitation capture must cover at least two cycles.

## Omitted features and next steps

- **Calibrated acceptance and plant verification:** Measure known-good and defective units and actuator motion, then choose validated limits and characterize settling across frequencies. Device timestamps and sample sequence numbers would improve timing and loss detection, but require firmware support.
- **Physical coupling and signal quality:** Once the actual cutoff is known, add a documented digital high-pass model if needed, accounting for filter startup and low-frequency attenuation. Add residual-noise, distortion, and phase metrics with appropriate calibration; these add complexity and require clear acceptance criteria.
- **Traceability and distribution:** Persist interrupted raw captures and device identifiers, and allow reopening historical runs. This improves investigations but increases storage and data-management needs. Build platform-specific packages and an installer, then validate on Windows, Linux, and macOS. A separate updated Windows executable passed an offscreen startup check but has not been exercised with the Teensy.
- **Robustness and usability:** Investigate the 20–25 Hz rejections without replacing the supplied firmware, test physical disconnects and recovery, and review the visible operator workflow. Forty-seven automated tests pass; they include synthetic analysis, simulated serial, sequencing, logging, GUI, and export checks, but do not replace physical validation.

AI assistance and remaining review risks are described in [DESIGN.md](DESIGN.md).
