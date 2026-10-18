# Hardware checklist (nothing in this product has run on a real board yet)

Do these in order on one box and one phone; write what happened under each item. Everything here is written from reading the code and from
host tests; the first real run will find things.

## PB1 Setup program
- [ ] `python3 setup/pisophone_setup.py` finds the ESP32-C3 on USB (try Windows and Linux). If the port is not listed: which chip and cable?
- [ ] It writes the software; the board restarts. (CI must have committed `release/flash/` first: Actions > "Coin box images".)
- [ ] After the restart the program finds the board again (the USB port may come back under another name) and sends the Wi-Fi; the board
      answers `PISO-SETUP-OK` and restarts. Check with `python3 setup/pisophone_setup.py check`.
- [ ] The box joins the shop Wi-Fi and the program prints a working address. Try a wrong Wi-Fi password: the message should name the usual causes.
- [ ] A box that was set up refuses the setup line (plug the cable in and run `setup` again: it must ask you to type ERASE first).

## PB2 Admin page and the phones
- [ ] `http://<address>/` opens from a phone on the same Wi-Fi; sign in as `admin` with the printed password.
- [ ] *Phone slots > Set up a phone* installs the app on a rental phone (this page is the one from the first product and is unchanged).
- [ ] The phone finds the box on the shop Wi-Fi. The app looks for the box at `<network>.10` and listens for the box's broadcast: if the
      shop's router isolates Wi-Fi clients ("AP isolation"), turn that off or the phone can not reach the box. Give the box a fixed address
      (DHCP reservation) if the phone loses it after the router restarts.
- [ ] A coin gives the right minutes; the phone unlocks and locks.

## PB3 Slot licenses
- [ ] Two phones pair. The third is refused with the message about the P500 license key; the third slot shows "P500 license".
- [ ] Make a key for the box code shown (`python3 setup/pisophone_setup.py licenses --box <code>`), paste it: *Add slot* opens a third slot, the page reloads.
- [ ] Paste the same key again: refused ("already used"). Paste a key made for another box code: refused ("another box").
- [ ] Press the operator's factory reset (dashboard or the reset pin): the slots stay open, the sales totals stay.
- [ ] Write the software again and re-issue the key (`--reissue`): it works again.

## PB4 Updates
- [ ] Raise `PISO_FW_VERSION`, merge to `main`, let CI sign (needs the `production-signing` Environment and the secret): the admin page's
      *Install update* offers and installs it. Without the secret nothing is published to `release/update/`.
