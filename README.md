# Macdonald Smart Greenhouse Prototype

A Flask dashboard and Arduino Uno prototype with USB-serial controls for an LED grow-light indicator, a servo-driven vent demonstrator, and a water sensor.

## Run with the Arduino

1. Connect the Arduino Uno to this computer with USB.
2. Upload `arduino/smart_greenhouse.ino` from the Arduino IDE. The sketch uses 9600 baud. Close the IDE Serial Monitor and any other app that might be using the Arduino serial port.
3. In a terminal opened in this project folder, install dependencies and run the app:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python app.py
   ```

   On macOS/Linux, use `python3 -m venv .venv`, activate with `source .venv/bin/activate`, then `python app.py`.

4. Open <http://127.0.0.1:5000> and sign in with the local prototype credentials `Macdonald` / `admin`.

The app is hardware-first: it attempts to discover and connect to the Arduino when it starts. Auto-detection only considers recognizable Arduino/USB serial ports, not unrelated built-in ports. If exactly one likely device is found, it selects it; if several are present, set the correct port in `.env` (copy `.env.example` to `.env`) as `GREENHOUSE_SERIAL_PORT=COM3`, replacing `COM3` with the Arduino IDE's **Tools → Port** value. On macOS/Linux, use the port path, for example `/dev/ttyACM0`.

The dashboard reports whether USB serial is connected and displays the connection error when it is not. It retries the serial connection, so you can also start the server and then connect the Arduino. Device buttons send commands to the hardware; they no longer simulate changes. The dashboard and Arduino must be on the same computer because the USB cable connects to the computer running Flask.

## Hardware functions and limits

- **Grow light bulb:** the sketch switches an LED on D2. This is a low-voltage indicator; do not connect a mains-powered lamp directly to an Arduino.
- **Ventilation:** the sketch sweeps a positional servo on D9. A positional servo does not spin continuously like a fan.
- **Manual/Auto:** the mode command is sent and reported, but automatic control rules are not implemented yet.
- **Water sensor:** analog input A0; calibrate `WATER_THRESHOLD` in the sketch using dry and wet readings.
- **Temperature and humidity:** the dashboard uses OpenWeather outdoor values. It does not measure internal greenhouse conditions.

## Wiring

- Water sensor analog output (AO/S) → Arduino A0; connect VCC/GND according to the module's rating.
- LED anode → 220–330 Ω resistor → Arduino D2; LED cathode → GND.
- Servo signal → D9; power it from a suitable regulated supply and connect supply ground to Arduino GND.

Do not power a large servo from the Uno 5V pin if it causes resets. Do not wire mains voltage directly to the Arduino. Real greenhouse equipment needs an appropriately rated driver and safe electrical installation.

## Configuration and security

Copy `.env.example` to `.env` only if you need to override the serial port, weather settings, or Flask secret. The default serial setting is `auto`. The `.env` file is ignored by Git. Change the default demonstration login and Flask secret before any non-local deployment; this prototype authentication is not production-grade. Weather and chart assets use internet access, but weather availability is not required for local Arduino control.
