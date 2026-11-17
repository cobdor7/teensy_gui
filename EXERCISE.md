
## Simulated Sensor + Actuator Test GUI

## Context

You’ll build a small desktop (or web) GUI that simulates a manufacturing line test on a sensor sub assembly. This simulated ‘sensor’ is stimulated by an ‘actuator’. The sensor is something like a piezo electric sensor, which outputs an AC coupled analog signal. The actuator is a speaker, vibration table, ERM, something that can pass a known vibration profile to the sensor and the software determines if the sensor is outputting an adequate response.

We’re evaluating test software development: technical skill in building software, an ability to parameterize vague requirements, understanding of the audience who may use the software, and communication with hardware.



## What’s in the Box!?

1. Arduino Teensy - preflashed with firmware. **DO NOT OVERWRITE IT**
2. Micro usb cable

## Preferred Tech Stack

- Python: PyQt5/6, PySide6, or Streamlit/Dash (desktop or local web)
- LabVIEW: 2020+ (attach a build and VI[s]) ok to use a demo version (so no build)
- C#: WPF (.NET 6+)

## Key Elements of the GUI

### 1. Connection to the Hardware

- We will send you an arduino teensy with firmware pre-installed on it, along with a cable to plug into a USB A port
- The arduino will stream 8 channels of pseudo ‘sensor data’ at 100Hz constantly.
- When the ascii characters “baseline” are sent to the teensy, it will respond with the baseline, non-excited sensor response
- When the ascii characters “response:XX” is sent, it will respond with sensor output from a XX hz test frequency. Values of XX can be from 1-25.

### 2. Visualization

- Real-time plot of sensor signal
- Clear indicators for Actuator ON/OFF and current parameters.

### 3. Evaluation

- Parameterization of pass/fail criteria.
- We are interested to see what you propose here
- Show numeric results and simple PASS/FAIL against thresholds
- Make it clear to the operator whether it passes or fails

### 4. Sequencing

- How does the operator know what is going on with the test?

### 5. Logging

- Log file (csv or json) which has all relevant test information, pass/fail, metrics, etc.

### 6. Packaging

- Must run locally on Windows, Linux, and MacOS.

## Nice to Have

1. Multiple Sensors for a single ‘test run’
2. An external ‘Settings’ file to set pass/fail criteria and have it be read into the system

3. An installer, easily packaged and distributed
   - single-file binary (PyInstaller), or MSIX / macOS app bundle.
4. Batch sequencing (run N tests and compare results)
5. Export Chart (png file or similar)

## Deliverables

1. Source code.
2. README.md: exact install/run steps (Windows, Linux,macOS), and how to build a one-file app if you did.
3. Written Report (1–2 pages):
   - Sensor output evaluation. Any issues with any ‘sensors’?
   - Assumptions, signal & plant model, AC-coupling method.
   - State Machine/Diagram
   - Threading/timers/rendering choices.
   - How evaluation metrics are computed.
   - If you skipped features: how you’d add them & trade-offs.
   - What you’d do next with more time.
   - Anything else you would like to include!
4. Prebuilt binary or .app/.exe.
5. Unit tests for the client application implementation

