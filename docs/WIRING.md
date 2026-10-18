# Wiring (ESP32-C3)

The pins are set in `esp32_firmware/envs/esp32_c3_dev.ini` and can be changed on the admin page (Settings) afterwards.

| Signal | Pin | Notes |
|---|---|---|
| Coin acceptor pulse output | GPIO 3 | the acceptor's pulse output; the box counts the pulses (the pin has a pull-up; check the acceptor's datasheet for the wiring) |
| Acceptor power relay | GPIO 4 | switches the acceptor on only while a phone waits for coins |
| Status LED | GPIO 8 | the board's own LED on most C3 boards |
| Hardware reset | GPIO 2 to GND for 5 s | factory reset: erases the settings, keeps the sales totals and the used slot-license keys |

Anti-tamper details (pulse width checks, lighter and wire tricks) are in `docs/COIN_SLOT_PROTECTION.md`.
