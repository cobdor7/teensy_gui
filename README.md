# Teensy Sensor Test Station — Phase 2

Cross-platform PySide6 GUI for the **preflashed** eight-channel Teensy. **Never uploads or modifies firmware.** Tested against the previously captured serial output format; physical hardware should be validated on your Windows machine.

The original assignment is included in [TAKE_HOME_EXERCISE.md](TAKE_HOME_EXERCISE.md). Generated test runs in `runs/` remain local and are excluded from version control.

## Windows quick start

Close `miniterm` or any other program using the Teensy before connecting.

```powershell
py -m pip install -r requirements.txt
py main.py
```

Select **COM5** (or the Teensy port), then **Connect**. Eight live charts update independently of the test workflow.

1. Leave frequency at **10 Hz** for now (higher frequencies have shown inconsistent behavior on this device).
2. Change **Minimum response amplitude** and **Minimum SNR** if needed. Defaults are **100 (sensor units)** and **15 dB**, respectively; these are *provisional* engineering thresholds, not manufacturer specs.
3. Click **RUN AUTOMATIC TEST**. The app requests baseline, settles for 0.5 s, records baseline for 2 s, requests excitation, settles for 0.5 s, records excited response for 2 s, then evaluates each channel. It requests `baseline` again after the test.
4. Inspect the results table (baseline RMS noise, 10 Hz sine amplitude, SNR dB, PASS/FAIL). Hover the result for a failure reason.
5. Outputs are saved in `runs/test_*.csv` (raw captures, timestamps and phase) and `runs/test_*.json` (criteria and results).

**Manual controls:** Baseline / OFF sends `baseline`; Excite / ON sends `response:XX` using the current frequency. Changing the frequency selector alone does not send a command. The status label shows the **last command written to serial**, not an independently verified hardware state.

## Other platforms

Linux/macOS:

```bash
python3 -m pip install -r requirements.txt
python3 main.py
```

On Linux, the serial port is commonly `/dev/ttyACM0`. Your account needs access to the serial device. Python 3.10+ recommended.

## Configuration

Edit `settings.json` to set capture/settle durations, default frequency, nominal minimum expected sampling rate, and test thresholds. The **Save Settings** button saves the frequency and thresholds currently in the UI. The two live thresholds apply to the next test; values are locked during an active test.

```json
{
  "default_frequency_hz": 10,
  "baseline_settle_s": 0.5,
  "baseline_capture_s": 2.0,
  "excite_settle_s": 0.5,
  "excite_capture_s": 2.0,
  "min_amplitude": 100.0,
  "min_snr_db": 15.0,
  "nominal_sample_rate_hz": 100
}
```

Capture windows use the **host's monotonic receive timestamps**, so FFT/sine fitting does **not** assume exactly 100 Hz. The nominal rate is only used to reject extremely short/incomplete captures. Host USB buffering can introduce timing jitter. The estimated received sample rate is shown after each test.

## Math

For each channel, baseline noise is the sample standard deviation of its baseline capture (DC-removed). For the excited capture, the app least-squares fits:

`x(t) = A sin(2πft) + B cos(2πft) + C`

- **Response amplitude:** `sqrt(A²+B²)` (peak amplitude at the commanded frequency)
- **SNR (dB):** `20 log10[(response_amplitude / sqrt(2)) / baseline_noise_RMS]`
- **PASS:** amplitude `>= minimum amplitude` **and** SNR `>= minimum SNR`

These criteria are frequency-selective: a large waveform at a **different** frequency may still fail. For waveform distortion, clipping, nonlinear response, and out-of-band fault criteria, additional tests would be needed.

## Run unit tests

```powershell
py -m unittest discover -s tests -v
```

Tested pure analysis/protocol modules using synthetic data; hardware integration is not automatically tested.

## Build an executable

```powershell
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --onefile --windowed --name SensorTestStation main.py
```

Build separately on Windows, Linux, and macOS. Put `settings.json` alongside the executable if you want to keep customized settings, and run from a writable folder for the `runs/` logs. A Windows executable cannot be built reliably from Linux using this recipe.
