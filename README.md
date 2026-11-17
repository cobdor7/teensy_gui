# Teensy Sensor Test Station

Cross-platform PySide6 GUI for the **preflashed** eight-channel Teensy. It never uploads or modifies firmware. The full 1–25 Hz sequence and an abort were exercised on Windows; the device rejected 20–25 Hz, and other operating systems still need validation.

The assignment is in [EXERCISE.md](EXERCISE.md), the measured results and design rationale are in [REPORT.md](REPORT.md), and additional design notes are in [DESIGN.md](DESIGN.md). Generated test runs in `runs/` remain local and are excluded from version control.

## Install and run from source

Install Python 3.10 or newer, including `pip` and `venv`, and use a graphical desktop session. Download or extract this project, open a terminal in the project folder containing `main.py` and `requirements.txt`, and use the commands for your operating system below. The `pip install` step requires internet access. The Python packages include the Qt libraries; a separate Qt SDK is not required. See [Qt for Python's setup guide](https://doc.qt.io/qtforpython-6/gettingstarted.html).

Connect the preflashed Teensy with USB and close `miniterm` or any other program using its port. The application does not upload firmware.

### Windows

Open PowerShell in the project folder. The `py` command requires the Python launcher; if it is unavailable, install Python with the launcher from [python.org](https://www.python.org/downloads/windows/).

```powershell
py --version
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m serial.tools.list_ports -v
.\.venv\Scripts\python.exe main.py
```

The port list should include a USB serial device such as `COM6`; the actual COM number can differ.

### Linux

Open a terminal in the project folder. On Debian/Ubuntu, install the Python environment tools first if they are missing: `sudo apt-get install python3 python3-venv python3-pip`. Other distributions use their own package manager.

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m serial.tools.list_ports -v
.venv/bin/python main.py
```

The Teensy is commonly `/dev/ttyACM0`, but use the port shown on your machine. The user must have access to the serial device; on Debian-based systems it is commonly owned by the `dialout` group. If access is denied, inspect the device's group with `ls -l /dev/ttyACM0`; if it is `dialout`, an administrator can run `sudo usermod -aG dialout "$USER"`, then you must log out and back in. See [pySerial's permission guidance](https://pyserial.readthedocs.io/en/latest/appendix.html). Some Linux installations also need system libraries for Qt's graphical platform plugin; see [Qt's Linux requirements](https://doc.qt.io/qt-6/linux-requirements.html).

### macOS

Open Terminal in the project folder with Python 3.10+ installed and available as `python3`.

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m serial.tools.list_ports -v
.venv/bin/python main.py
```

Select the Teensy's USB serial device from the list; it commonly has a name such as `/dev/cu.usbmodem...`.

### Run a test

In the application, select the Teensy port, then click **Connect**. Eight live charts update independently of the test workflow. Windows and the connected Teensy were tested; the Linux and macOS commands are provided but have not been validated on those platforms.

Drag the divider between the charts and results to give either area more space. On shorter windows, scroll within the chart area to see all eight sensors.

Use **Live charts** for the rolling signal, or **Captured test charts** after an evaluated frequency to compare baseline (gray) with the response (colored). The default **Baseline-referenced** display subtracts each channel's baseline mean from both traces; **Raw values** shows the original samples. This is a change of display reference, not point-by-point subtraction of the independent captures or a high-pass filter. Select a captured frequency from the drop-down; both traces start at zero seconds within their respective capture windows, so they are overlaid for comparison rather than displayed as one continuous timeline. Rejected frequencies do not have captured response charts. The captured tab holds the current or most recent run; starting another run clears it. Raw samples from completed runs remain in their CSV files. The display mode does not change evaluation metrics or pass/fail criteria.

Click **Export current charts to PNG…** to save all eight graphs from the selected chart tab, including graphs outside the visible scroll area. The live export shows the current rolling window; the captured export shows the selected frequency and display mode. Choose a writable destination in the save dialog.

1. Select a frequency, enter a comma-separated sequence such as `1, 5, 10, 15`, or click **Use all frequencies (1–25 Hz)** to fill in the whole range. A blank sequence uses the selected frequency. On the tested device, 20–25 Hz were explicitly rejected despite the assignment specifying 1–25 Hz.
2. Change **Minimum response amplitude** and **Minimum SNR** if needed. Defaults are **100 (sensor units)** and **15 dB**, respectively; these are *provisional* engineering thresholds, not manufacturer specs. Enter optional **Signal lower limit** and **Signal upper limit** values for raw samples, or leave either blank to disable it. Click **Save Settings** to retain these values. Other limits are configurable in `settings.json`.
3. To use different times for individual frequencies, click **Frequency timing…**, enter optional settling and/or capture times in seconds, and click **OK**. Leave a cell blank to use the common default. Click **Save Settings** if you want to keep the overrides for later runs. For example, a 1 Hz capture needs at least 2 seconds for two cycles; longer can provide more data.
4. Click **RUN AUTOMATIC TEST**. The app captures one baseline, then settles, captures and evaluates each requested frequency in turn, showing the frequency and progress. It requests `baseline` again after the test. Each excited capture must cover at least two cycles. Testing all 25 frequencies takes approximately a minute with the default capture settings, depending on device responses.
5. Inspect the per-frequency results table for values, effective limits, PASS/FAIL, and the visible reason. A rejected frequency gets its own **REJECTED** row and no sensor results; the app requests baseline and continues to the next frequency. The overall result is **TEST ERROR** if any frequency was rejected. Other capture or communication errors stop the run; partial results do not constitute an overall pass.
6. Outputs are saved in `runs/test_*.csv` (raw captures, timestamps and phase) and `runs/test_*.json` (criteria and results).

**Manual controls:** Baseline / OFF sends `baseline`; Excite / ON sends `response:XX` using the current frequency. Changing the frequency selector alone does not send a command. The actuator indicator describes the **last command written to serial**; the firmware provides no independently verified actuator state.

## Batch testing

Set **Runs** to a value from 2 to 100, choose the frequencies and criteria, and click **RUN AUTOMATIC TEST**. The application repeats the same sequence on the connected device, taking a fresh baseline for every run. It does not prompt for a new device or record individual serial numbers. The status shows the current run, and the **Batch comparison** tab shows each run's result and report, plus per-sensor/per-frequency pass and fail counts and ranges for response amplitude, SNR, and baseline noise.

Each completed run saves its usual `test_*.csv` and `test_*.json`. A `batch_*.json` summary is updated after every run with the run reports and comparisons. A rejected frequency remains distinct from a sensor failure; the batch continues, but its final result is TEST ERROR if any run contains a rejection. A sensor failure does not stop later runs. An abort or other test error stops the batch, requests baseline when connected, and marks the summary `ABORTED` or `ERROR` with the number of completed runs. Values from an interrupted run are not treated as completed results.

## Configuration

When running from source, the app reads `settings.json` beside `main.py` and writes logs under the project's `runs/` directory. Edit `settings.json` while the app is closed to set capture/settle durations, frequency sequence, validity limits, and common or per-channel criteria. The **Save Settings** button saves the selected frequency, frequency sequence, per-frequency timing overrides, and common thresholds shown in the GUI, including signal limits. Channel and frequency overrides still take precedence over common limits. Settings are locked during a test. An invalid settings file disables testing and displays a TEST ERROR; correct the file and restart the app. Packaged executables use a separate settings file beside the executable, as described below.

```json
{
  "default_frequency_hz": 10,
  "test_frequencies_hz": [],
  "baseline_settle_s": 0.5,
  "baseline_capture_s": 2.0,
  "excite_settle_s": 0.5,
  "initial_excite_extra_settle_s": 0.5,
  "excite_capture_s": 2.0,
  "min_amplitude": 100.0,
  "min_snr_db": 15.0,
  "max_baseline_noise": null,
  "max_response_amplitude": null,
  "clip_min": null,
  "clip_max": null,
  "channel_criteria": {},
  "frequency_criteria": {},
  "frequency_timings": {},
  "nominal_sample_rate_hz": 100,
  "min_receive_rate_hz": 60,
  "max_receive_rate_hz": 200,
  "max_sample_gap_s": 0.1
}
```

Frequency timing overrides can also be set in the file, for example `"frequency_timings": {"1": {"excite_capture_s": 4.0}, "10": {"excite_settle_s": 1.0, "excite_capture_s": 2.5}}`. Each settle or capture setting must be 0.1–30 seconds; omitted values inherit the common excited settling and capture times. The first excitation in each run also waits `initial_excite_extra_settle_s` (0.5 seconds by default, adjustable from 0 to 30 seconds) before capture to allow the waveform extra time to settle. The app validates the two-cycle minimum for every selected frequency before starting and records the actual settling and capture times for each frequency in the run JSON. Longer capture times improve the opportunity to observe more cycles but increase total test time; two cycles is a basic minimum, not a guarantee of measurement accuracy.

All four optional limits default to `null` (disabled): no noise ceiling, response ceiling, or signal bounds are assumed without a specification. `clip_min` and `clip_max` are absolute sample-value boundaries; any baseline or excited sample at or beyond a configured boundary fails that channel. They are a range-limit check, not proof of physical clipping. For example, this structure sets illustrative channel and frequency overrides:

```json
"channel_criteria": {"4": {"max_baseline_noise": 100}},
"frequency_criteria": {
  "5": {"channels": {"2": {"max_response_amplitude": 500}}},
  "10": {"min_amplitude": 150}
}
```

Overrides are applied in order: common criteria, channel criteria, frequency-wide criteria, then frequency-specific channel criteria. Numbers in the example illustrate configuration, not calibrated limits. Use `null` in an override to disable an optional inherited limit. The JSON summary records the resolved criteria for each result.

Capture windows use the host's monotonic, high-resolution `time.perf_counter()` when the serial reader receives each row. The **host receive rate** is `(number of samples - 1) / (last receive time - first receive time)`. The GUI also shows the 95th percentile of inter-arrival intervals and the maximum gap; the JSON records those values, the median interval, equal-timestamp count, and clock resolution for baseline and each excited capture. These are host-delivery diagnostics, not measurements of the device's sampling clock or physical sampling jitter.

The default receive-rate range of 60–200 Hz accommodates both the specified 100 Hz and the approximately 150 Hz observed during Windows testing; `max_sample_gap_s` defaults to 0.1 s. These are provisional transport-validity settings, not sampling specifications. Too few samples, CSV-like malformed rows, backward timestamps, a rate outside the configured range, a gap beyond the limit, lost connection, or a reported frequency rejection produces TEST ERROR. Explicit rejections and their frequency are saved in the JSON alongside completed sensor results. One-value frequency acknowledgements are checked when present; their absence is allowed and does not establish whether excitation occurred. Equal receive timestamps are allowed because USB delivery and host clock resolution can batch samples. Host timing cannot prove the true device sampling rate or guarantee that no rows were lost; that would require device-side timestamps or sequence numbers.

## Math

For each channel, baseline noise is the sample standard deviation of its baseline capture (DC-removed). For the excited capture, the app least-squares fits:

`x(t) = A sin(2πft) + B cos(2πft) + C`

- **Response amplitude:** `sqrt(A²+B²)` (peak amplitude at the commanded frequency)
- **SNR (dB):** `20 log10[(response_amplitude / sqrt(2)) / baseline_noise_RMS]`
- **PASS:** amplitude `>= minimum amplitude` and SNR `>= minimum SNR`, plus any enabled maximum noise, maximum amplitude, and signal-range limits.

These criteria are frequency-selective: a large waveform at a **different** frequency may still fail. The application cannot independently verify the physical actuator frequency; additional checks would be needed for waveform distortion, nonlinear response, and out-of-band faults.

## Run unit tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux/macOS, use `.venv/bin/python -m unittest discover -s tests -v`. The suite covers analysis and protocol with synthetic data, GUI initialization, sequencing, abort, errors, settings, output files, and the threaded serial worker using a simulated port. It does not automatically access hardware.

To run a separate direct-serial hardware check without the GUI, close any app using the port and run `.\.venv\Scripts\python.exe tools/hardware_check.py COM6` (substitute the actual port; on Linux/macOS use `.venv/bin/python`). It sends a 10 Hz command, captures data using the provisional default criteria, requests baseline afterward, and saves a CSV and JSON under `runs/`. This check does not exercise GUI sequencing.

## Build a one-file executable

First complete the source installation for the platform above. Run these commands from the project folder, and build separately on each target operating system and architecture; PyInstaller is not a cross-compiler. These steps download PyInstaller into the virtual environment. Only the Windows build has been exercised here.

### Windows build

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name SensorTestStation main.py
if (-not (Test-Path -LiteralPath '.\dist\settings.json')) { Copy-Item -LiteralPath '.\settings.json' -Destination '.\dist\settings.json' }
.\dist\SensorTestStation.exe
```

This produces `dist\SensorTestStation.exe`. The copy command creates its external settings file only if one does not already exist, preserving any customized file. The `dist` folder must be writable: the executable reads and saves `dist\settings.json` and writes logs under `dist\runs\`. If its settings file is absent, the executable uses built-in defaults. An existing Windows executable and settings file are available in this workspace's `dist` folder.

**Windows path containing a comma:** Some PySide6 versions report an incorrect Qt plugin path to PyInstaller when the project or virtual environment lives in a comma-containing path, as in this workspace. If the build reports a missing Qt plugin directory, run the following instead of the PyInstaller command above. It temporarily maps the current project folder to an unused `T:` drive and removes the mapping afterward. Set `$buildDrive` to a different unused letter if `T:` is occupied.

```powershell
$projectDirectory = (Get-Location).Path
$buildDrive = 'T:'
if (Test-Path "${buildDrive}\") { throw 'T: is already in use; choose another drive letter.' }
subst $buildDrive $projectDirectory
if ($LASTEXITCODE -ne 0) { throw 'Could not create the temporary drive mapping.' }
try {
    Set-Location "${buildDrive}\"
    & "${buildDrive}\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name SensorTestStation main.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally {
    Set-Location -LiteralPath $projectDirectory
    subst $buildDrive /D
}
if (-not (Test-Path -LiteralPath '.\dist\settings.json')) { Copy-Item -LiteralPath '.\settings.json' -Destination '.\dist\settings.json' }
```

### Linux build

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --onefile --name SensorTestStation main.py
if [ ! -e dist/settings.json ]; then cp settings.json dist/settings.json; fi
./dist/SensorTestStation
```

The output is the single executable `dist/SensorTestStation`. It reads `dist/settings.json` and writes `dist/runs/`, so use a writable directory. Serial-device permissions still apply.

### macOS build

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --onefile --name SensorTestStation main.py
if [ ! -e dist/settings.json ]; then cp settings.json dist/settings.json; fi
./dist/SensorTestStation
```

The output is the single executable `dist/SensorTestStation`; this recipe runs it from Terminal and keeps `settings.json` and `runs/` beside it. It does not create a Finder `.app` bundle. PyInstaller can also create an `.app` with `--windowed`, but [its documentation does not recommend combining a macOS app bundle with `--onefile`](https://pyinstaller.org/en/stable/usage.html#building-macos-app-bundles). Linux and macOS builds and distribution have not been verified here.

For a packaged executable on any platform, the application's data directory is the executable's actual parent directory, not the shell's current directory. Keep that directory writable and copy `settings.json` there if using customized criteria. A fresh build does not automatically include the external settings file.

For an automated offscreen startup check, run `.\dist\SensorTestStation.exe --smoke-test`; it creates the window, processes events briefly, and exits without accessing the Teensy. The normal application starts when no argument is supplied.
