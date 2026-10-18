# PisoPhone Box

A coin box for **rental phones**: a customer drops a coin, the phone unlocks for that many minutes, and locks again when the time runs
out. One ESP32-C3 board is the whole product on the shop side. There is no router software and no Wi-Fi selling in this repository
(that is a different product, TechNet PisoHub): the coin box and the rental phones simply join **the shop's own Wi-Fi**.

* **2 phone slots are free.** Every further slot needs a license key, P500 each, up to 12 slots ([docs/LICENSING.md](docs/LICENSING.md)).
* **One file sets it up.** `setup/pisophone_setup.py` writes the software to the board over USB, gives it your Wi-Fi and a new admin
  password, and tells you the address of its admin page.
* **The admin page installs the phones.** Open it, go to *Phone slots*, tap *Set up a phone*: the page installs the PisoPhone app on
  each rental phone. You never touch an app file.

## Set up a coin box (about 5 minutes)

You need: the ESP32-C3 board with the coin acceptor wired to it ([docs/WIRING.md](docs/WIRING.md)), a USB **data** cable, a computer with
Python 3.8 or newer and internet, and the shop's Wi-Fi name and password (the **2.4 GHz** network: the ESP32-C3 cannot use 5 GHz).

1. Plug the board into the computer with the USB cable.
2. Download [`setup/pisophone_setup.py`](setup/pisophone_setup.py) and run it: `python3 pisophone_setup.py`
   (Windows: `py pisophone_setup.py`). Choose **1. Set up a new coin box**.
3. Type the Wi-Fi name and password when it asks. Wait. It prints the address of the admin page, the admin password and the box code, and
   saves them in a file only you can read.
4. Open the address on a phone or computer on the same Wi-Fi, sign in as `admin`, then *Phone slots > Set up a phone* for each rental phone.

That is all. The first two phones work at once; to add more slots buy license keys ([docs/LICENSING.md](docs/LICENSING.md)).
If something goes wrong the program says what to try; `python3 pisophone_setup.py check` shows what the board reports.

## For the owner (you sell the licenses)

`python3 pisophone_setup.py tools` opens the owner tools: **slot license keys**, time cards (printable QR cards for players), the admin QR
code, the phone recovery code, and your key files. These need your owner key ([docs/KEYS.md](docs/KEYS.md)).

## What is in this repository

| Folder | What |
|---|---|
| `esp32_firmware/` | the coin box software (ESP32-C3, Arduino/PlatformIO) and its tests (`host_tests/`) |
| `setup/pisophone_setup.py` | the one-file setup program and the owner tools |
| `protocol/` | the box <-> phone message contract (test vectors) |
| `scripts/` | owner and release tools (license keys, firmware signing, QR codes) |
| `release/` | the finished images, committed by CI: `flash/` for the setup program, `update/` for the admin page's *Install update* |
| `docs/` | licensing, keys, the hardware checklist, how the parts fit |

Status: the software is new in this product and **has not run on a real board yet**. [docs/REAL_WORLD_TESTING.md](docs/REAL_WORLD_TESTING.md)
lists what to check first.
