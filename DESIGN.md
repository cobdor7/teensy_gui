# Test Station Design Notes

## System boundary / protocol

The preflashed Teensy exposes a USB virtual serial port and streams eight comma-separated floating-point values per line, often with a trailing comma. Commands are raw ASCII `baseline` or `response:XX` (XX = 1..25 Hz) **without a newline**, as verified using `pyserial` on COM5. Other text, including command acknowledgements such as `10.00`, is ignored by the eight-value parser. The app does not flash firmware. The GUI indicates commands *sent*, not an independently confirmed physical actuator state.

## Architecture and threading

`main.py` owns the Qt GUI, plotting buffers, test sequence, result table, and output files. A `QThread` runs serial reads/writes and emits `(monotonic_timestamp, eight_values)` samples. GUI-to-worker commands go through a thread-safe queue: the GUI never directly writes a live `Serial` object from another thread. Plots redraw every 50 ms, while serial samples stream independently, avoiding a GUI refresh bottleneck. Each chart shows a rolling five-second window. A 20 ms Qt timer drives the state machine; it does not control the sampling rate. The app records when the worker *actually writes* each command, then starts settle/capture intervals.

## Test state machine

`IDLE → WAIT_BASELINE_COMMAND → BASELINE_SETTLE → BASELINE_CAPTURE → WAIT_RESPONSE_COMMAND → EXCITE_SETTLE → EXCITE_CAPTURE → EVALUATING → PASS/FAIL`. Serial errors, insufficient data, or command write timeouts lead to `ERROR`; operator cancellation leads to `ABORTED`. The app requests `baseline` on normal completion, error, and abort if connected. The waveform buffers for baseline and excitation are separate, and samples outside each capture window are excluded. Controls that could change parameters mid-test are disabled. The last result remains visible after the run.

## Signal model and AC coupling

Individual channels have different DC offsets and noise. The baseline noise estimate is `std(x_baseline)` (sample standard deviation, after mean subtraction). In the excited window, the software fits `x(t) = a·sin(2πft) + b·cos(2πft) + c` by least squares. The intercept `c` removes DC mathematically, without requiring a hardware AC-coupling capacitor. Peak response amplitude is `sqrt(a²+b²)`. The single-frequency fit rejects other frequencies better than raw peak-to-peak or total RMS, important for channels with independent noise or large DC offsets.

SNR is `20 log10[(amplitude/√2)/baseline_RMS_noise]`. Each channel passes only if amplitude exceeds the user threshold *and* SNR exceeds the user threshold. Default minimum amplitude 100 and minimum SNR 15 dB are exploratory settings, not calibrated product specifications. The user can change them in the GUI or settings.json. Test frequency defaults to 10 Hz because initial device tests showed questionable high-frequency behavior; frequency selection still spans 1–25 Hz.

## Timing, failure modes, and limitations

The assignment describes 100 Hz samples, but waveform spacing in a recorded `response:10` capture suggests the received rate may differ. Therefore the fitted phase uses per-sample **host monotonic arrival times** rather than `sample_index/100`. The displayed effective receive rate helps identify discrepancies. This is not true hardware timestamping: packet buffering and OS scheduling can cause timing jitter and degrade sine fitting, especially at 20–25 Hz. More accurate production evaluation would require device timestamps or hardware sampling metadata and a calibrated excitation source. The application skips malformed/incomplete serial rows, checks a minimum capture size, and treats a lost connection as an error, not a failing sensor. It currently does not check clipping, harmonics, phase, actuator feedback, or production limits by channel. The last requested command is not hardware acknowledgement.

## Traceability, outputs, verification and AI use

Each successful run saves CSV baseline and excited samples with relative host timestamps and a JSON summary including timestamps, test port/frequency, thresholds, received sample rate, and per-channel metrics/results. `tests/test_protocol.py` covers line parsing and command framing; `tests/test_analysis.py` covers known-amplitude synthetic sine waves, baseline DC offsets, different nominal rates, noise failures, and incomplete captures. Integration testing with the physical Teensy remains necessary, including low/high frequency transitions and disconnect/abort behavior. AI was used to draft the Python code, test structure, and documentation; decisions around thresholds and potential sensor defects must be validated experimentally rather than treated as ground truth.
