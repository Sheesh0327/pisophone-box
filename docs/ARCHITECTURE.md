# How the parts fit

    owner's computer                      shop
    ----------------                      ----
    pisophone_setup.py --USB--> ESP32-C3 coin box --- shop Wi-Fi --- rental phones (PisoPhone app)
         |  writes software, Wi-Fi,            |                       ^
         |  admin password                     +-- admin page (port 80) installs the app on each phone
         +-- owner tools: slot license keys, time cards, admin QR, recovery code (offline, with the owner key)

* **The box** (`esp32_firmware/`) records every coin before anything else, credits it to a phone exactly once (by transaction id), and
  keeps the unused time of QR cards. It has no access point; it joins the Wi-Fi it was given.
* **The phones** run the PisoPhone kiosk app. They talk to the box with signed messages (`protocol/`).
* **Setup over USB** (`esp32_firmware/include/SerialProvision.h`, `setup/pisophone_setup.py`): the line `PISO-SETUP` carries the Wi-Fi and the
  admin password, and a box accepts it only while its admin password is still the factory one. A fresh box joins no Wi-Fi at all.
* **Slot licenses** (`esp32_firmware/include/SlotLicense.h`): see `docs/LICENSING.md`.
* **Updates**: CI builds the image, signs it with the owner key and commits it to `release/update/`; the admin page's *Install update*
  fetches `release/update/firmware.json` from this repository (raw.githubusercontent.com) and only installs an image the owner signed.
* **The phone installer page** ("Set up a phone") is the first product's page, https://pisophone.pages.dev, unchanged. It is the one thing
  this product depends on outside this repository (`WebDashboardComponents.cpp`, `portal-modals.js`).

Money and time are never wrong: a payment is recorded first, credited once, and survives a power cut. Only software the owner signed is installed.
