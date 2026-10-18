# Keys

## The owner key (signs firmware updates and slot license keys)
Make it on **your own computer**, never in CI or a cloud session (`pip install cryptography` first):

    python3 scripts/make_owner_keys.py            # saves ~/pisophone_license_key.pem

It refuses to overwrite a key or to write inside the repository, and writes only the **public** half into
`esp32_firmware/include/LicensePubKey.h`. Commit that file, rebuild, and write the new software to every box.

Back the private key up in two offline places. Lose it and no box can ever take a signed update or a new license key; leak it and anyone
can make license keys and fake updates.

**This repository currently holds the PisoHub owner's public key** (it was copied from the first product). Keys you sign with
`~/pisophone_license_key.pem` work on boxes built from this repository because that is the matching private key. If you would rather keep
the two products on separate keys, run `make_owner_keys.py` with a new file, commit the new public key and rebuild; then only the new key
signs updates and licenses.

Until a public key is built in, boxes accept unsigned firmware and refuse every license key.

## Signed firmware updates
A box with a public key built in only installs images you signed. `.github/workflows/firmware-images.yml` builds the ESP32-C3 image on
every push to `main` and commits it to `release/` (`flash/` for the setup program, `update/` for the admin page's *Install update*).
To sign the update image in CI, put the key in the protected GitHub Environment `production-signing` (Settings > Environments;
require your approval; only `main`) as the secret `OWNER_SIGNING_KEY_B64`:

    python3 scripts/make_owner_keys.py --github-secret     # prints the key once; nothing is saved

Without the secret, `release/update/` is published **unsigned**, which boxes refuse once the owner key is built in. Or sign by hand:
`python3 scripts/sign_firmware.py --private KEY.pem --chip esp32c3 --image firmware.bin`, copy the image and its `.manifest.json` into
`release/update/` and run `python3 scripts/publish_firmware_ota.py --json-only`. Raise `PISO_FW_VERSION` in
`esp32_firmware/include/FirmwareVersion.h` for every release: a box installs only a newer version.

## Other keys (owner tools, optional)
* **Card key** (`~/pisophone_card_key.pem`): signs time cards; its public half is `esp32_firmware/include/CardPubKey.h`.
* **Admin key** (`~/pisophone-superadmin.key`): the admin QR code; its public half is `SuperAdminPubKey.h`.
Boxes trust only the public halves built into their software, so a key made new works only after a rebuild.

## Per-box secrets
Each box makes its own random secret on first start; the admin page's *Set up a phone* link hands it to the phone. Nothing shared is in
the repository. Never commit a key, password or token.
