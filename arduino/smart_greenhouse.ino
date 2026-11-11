/*
  Macdonald Smart Greenhouse Prototype - Arduino Uno
  Water sensor AO/S -> A0
  LED/BULB + -> D2 through 220-330 ohm resistor; - -> GND
  Servo signal -> D9; power supply suited to servo; common GND

  Serial commands from Flask:
    FAN:ON / FAN:OFF       sweep servo 0..180..0 to imitate a fan
    LED:ON / LED:OFF       turn the bulb LED on/off
    MODE:AUTO / MODE:MANUAL

  OpenWeather temperature/humidity are fetched by Flask, not this Arduino.
  A standard positional servo sweeps back and forth; it does not spin like a fan.
*/
#include <Servo.h>
#define WATER_PIN A0
#define SERVO_PIN 9
#define LED_PIN 2

Servo fanServo;
const int WATER_THRESHOLD = 500; // Calibrate from dry/wet sensor readings
const unsigned long READ_INTERVAL_MS = 2500;
const unsigned long FAN_STEP_INTERVAL_MS = 18;

bool fanOn = false;
bool bulbOn = false;
bool automaticMode = false;
int servoAngle = 0;
int sweepDirection = 1;
int waterRaw = 0;
bool waterDetected = false;
unsigned long lastReadMs = 0;
unsigned long lastFanStepMs = 0;
String commandBuffer = "";

void setFan(bool on) {
  fanOn = on;
  if (fanOn) {
    automaticMode = false;
    lastFanStepMs = millis();
  } else {
    servoAngle = 0;
    fanServo.write(servoAngle);
  }
}

void processCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd == "FAN:ON") setFan(true);
  else if (cmd == "FAN:OFF") setFan(false);
  else if (cmd == "LED:ON") { bulbOn = true; digitalWrite(LED_PIN, HIGH); }
  else if (cmd == "LED:OFF") { bulbOn = false; digitalWrite(LED_PIN, LOW); }
  else if (cmd == "MODE:AUTO") automaticMode = true;
  else if (cmd == "MODE:MANUAL") automaticMode = false;
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      processCommand(commandBuffer);
      commandBuffer = "";
    } else if (c != '\r' && commandBuffer.length() < 80) {
      commandBuffer += c;
    }
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  pinMode(WATER_PIN, INPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.begin(9600);
  fanServo.attach(SERVO_PIN);
  fanServo.write(0);
  delay(400);
}

void loop() {
  readSerialCommands();
  unsigned long now = millis();

  // Sweep from 0 to 180 degrees and back while the fan control is ON.
  if (fanOn && now - lastFanStepMs >= FAN_STEP_INTERVAL_MS) {
    lastFanStepMs = now;
    servoAngle += sweepDirection;
    if (servoAngle >= 180) { servoAngle = 180; sweepDirection = -1; }
    if (servoAngle <= 0) { servoAngle = 0; sweepDirection = 1; }
    fanServo.write(servoAngle);
  }

  if (now - lastReadMs >= READ_INTERVAL_MS) {
    lastReadMs = now;
    waterRaw = analogRead(WATER_PIN);
    waterDetected = waterRaw >= WATER_THRESHOLD;

    Serial.print("{\"temperature\":null,\"humidity\":null");
    Serial.print(",\"water_raw\":"); Serial.print(waterRaw);
    Serial.print(",\"water_detected\":"); Serial.print(waterDetected ? "true" : "false");
    Serial.print(",\"servo_angle\":"); Serial.print(servoAngle);
    Serial.print(",\"fan_on\":"); Serial.print(fanOn ? "true" : "false");
    Serial.print(",\"mode\":\""); Serial.print(automaticMode ? "AUTO" : "MANUAL"); Serial.print("\"");
    Serial.print(",\"led\":"); Serial.print(bulbOn ? "true" : "false");
    Serial.println("}");
  }
}
