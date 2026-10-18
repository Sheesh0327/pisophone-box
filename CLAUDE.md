# CLAUDE.md: what this project is and how to work on it

## 1. What this project is
**PisoPhone Box** is the PisoPhone-only product: one ESP32-C3 coin box that locks and unlocks rental Android phones by the minute. It
was made from the PisoHub coin box software (repository PisoPhone-Kiosk-Launcher-V1) with everything about routers and customer Wi-Fi
(OpenWrt, openNDS, pisoportal, the `/api/gateway/*` API) removed. The coin box and the phones join the shop's own Wi-Fi.
Parts: `esp32_firmware/` (the box), `setup/pisophone_setup.py` (the one-file setup program: a PIN-protected setup page, the same look as the first product's but with no router steps, and the owner tools), `scripts/` (owner and
release tools), `protocol/` (box <-> phone contract), `release/` (images CI commits), `docs/`.
The phone app and the "Set up a phone" installer page are NOT in this repository: they are the first product's (https://pisophone.pages.dev).

Two rules decide every trade-off:
1. **Money and time are never wrong.** The box records a payment before anything else; a payment is credited exactly once (by transaction
   id); nothing is lost on a power cut.
2. **Only software the owner signed is installed, and a slot opens only with a license key the owner signed.** The signing keys are never
   in the repository, in a cloud session, or in CI outside the protected `production-signing` Environment (`docs/KEYS.md`).

**Slot licensing:** 2 free slots, P500 per extra slot, 12 at most (`esp32_firmware/include/SlotLicense.h`, `docs/LICENSING.md`). No admin
password or QR code may open a slot without a key (`scripts/check_security_rules.sh` guards the handler).

## 2. Where the repository is now (2026-10-18)
* New repository, **firmware 1.0.0 not released and never run on a real board**. Everything was checked with host tests, the setup program's
  tests (a pretend board), the dashboard's scripts in a browser and the firmware compiled in CI. `docs/REAL_WORLD_TESTING.md` is the
  hardware checklist.
* The firmware was not compiled on the machine that wrote the changes (PlatformIO's registry was unreachable there): the first CI run is
  its first compile. Watch it.
* The owner key's public half in `esp32_firmware/include/LicensePubKey.h` is the PisoHub owner's (copied). See `docs/KEYS.md`.

## 3. Checks to run before you push
* `bash scripts/check_security_rules.sh`
* `bash esp32_firmware/host_tests/run.sh` (needs `libmbedtls-dev`, python `cryptography`)
* `python3 tests/test_setup.py`, `tests/test_setup_web.py` (the setup page, every step, in a browser), `tests/test_setup_tools.py`, `python3 -m pyflakes setup tests scripts/make_slot_licenses.py`
* `python3 scripts/embed_web.py --check` (regenerate with `python3 scripts/embed_web.py` after a change in `esp32_firmware/web/`)
* clang-format 23.1.2 on `src/*.cpp include/*.h host_tests/*.cpp` except `WebDashboard*`, `SuperAdminTemplate*`, `WebAssets*`
* the browser tests in `scripts/tests/` (Playwright)
CI (`.github/workflows/quality.yml`) runs the same plus the firmware compile.

## 4. Generated files: never edit by hand
`esp32_firmware/include/WebAssets.h` (`scripts/embed_web.py`), `release/*` (CI: `firmware-images.yml`), `protocol/fixtures` (`protocol/gen_vectors.py`).
Firmware code needs a higher `PISO_FW_VERSION` or boxes ignore the update.

## 5. House rules
* Work on `main` only when the owner asks you to release; otherwise a branch. Do not open pull requests or release unless asked.
* The box <-> phone message contract changes on both sides in one change; the phone side is in the other repository, so do not change it here alone.
* Never commit a key, password or token. The setup program uses only the Python standard library (`cryptography`, `esptool` and `pyserial` are installed on request).
* Anything touching credit, expiry, deduction, the payment queue or license checking: say how you checked it.
* Match the surrounding code: naming, comment density, plain English. Commits say what changed and why for the owner to read.
