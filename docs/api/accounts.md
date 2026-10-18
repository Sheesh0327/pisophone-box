# Player accounts: QR cards (phone <-> box)


A player's account is a **QR card** you sell. Their unused time stays on the box, so it is not lost when they leave. The box
holds the only copy (flash partition `spiffs`, see `esp32_firmware/include/Accounts.h`); the phone keeps nothing but the number
and name of the player who is signed in. The message format is `esp32_firmware/include/AccountProtocol.h` (box) and
`app/.../network/Esp32AccountRequests.kt` (phone), both tested against `protocol/fixtures/box_phone_v1.json`.

### The card

`PISO1.<box>.<serial>.<seconds>.<signature>`: the box's MAC (12 hex digits), the card number (1 to 65535, the account id), the
starter time in seconds (10800 = 3 hours) and an ECDSA P-256 signature over `pisophone-card-v1|<box>|<serial>|<seconds>`. The
format is `scripts/card_format.py`; `esp32_firmware/include/CardCodec.h` reads it.

- **Printing:** `scripts/make_card_key.py` makes the card key (not the firmware owner key) and writes its public half to
  `CardPubKey.h` (commit it, rebuild, flash). `scripts/make_cards.py --box <MAC> --count N` prints A4 sheets of 12 cards for back-to-back printing
  (`fronts.pdf` with the QR, `backs.pdf` with the logo as a mirror image so each logo lands behind its QR when the paper is turned
  over, and `duplex_both_sides.pdf` for printers with automatic two-sided printing; print on card stock, since a dark back can
  show through thin paper) and keeps a ledger so a serial is never reused. The dashboard shows the box ID to use. Until a card key is built in, every card is refused.
- **Guests are unaffected:** nobody needs a card. A guest inserts coins on the lock screen exactly as before; the card is only
  an extra, a place where time is saved. A signed-in player's coins add to their account the same way a guest's coins add to the
  phone's timer.
- **One box:** a card works only on the box whose MAC is in it.
- **First scan:** creates the account holding the starter time and asks the player for a name. Later scans only sign in.
- **Never twice:** the box keeps a permanent "redeemed" mark per card number (a bitmap saved with the accounts). It outlives the
  account: pruning an unused account, or an admin deleting one, never lets its card claim the starter time again; scanning it
  afterwards gives an empty account.
- **The card is the key.** Anyone holding the card, or a photo of it, can use its time, and a lost card is lost time. Only one phone
  can be signed in with a card at a time.

### Calls

Every call comes from a paired, active phone and is signed with the box secret: `sig = HMAC-SHA256(secret,
"v1:acct_<op>:<deviceId>:<ts>:<bound>")`. `<bound>` holds every parameter that matters, so a captured request cannot be edited.

| op | URL | bound | answer |
|---|---|---|---|
| scan | `/api/account/scan?device_id&card&ts&sig` | the card text | `{success, id, name, balance_sec, bonus_sec}` |
| name | `/api/account/name?device_id&id&name&ts&sig` | `<id>:<name>` | same |
| signout | `/api/account/signout?device_id&id&time&ts&sig` | `<id>:<time>` | same |
| info | `/api/account/info?device_id&id&ts&sig` | `<id>` | same (only on the phone the player is signed in on) |

- `bonus_sec` is the starter time this call just gave (only on a card's first scan, else 0). `name` is empty until chosen: 1 to 16 of
  letters, digits, space, `_`, `-`. `time` is the seconds the phone still has.
- Errors: `{success:false, error}` with `BAD_CARD`, `WRONG_BOX` (403), `CARDS_OFF` (503, no card key installed), `BAD_NAME`,
  `ACCOUNTS_FULL` (507), `NO_SUCH_ACCOUNT`, `ALREADY_SIGNED_IN` (409), `NOT_SIGNED_IN`, `TOO_FAST` (429), `CLOCK_UNKNOWN` (503, the box
  has no clock yet: retry in a few seconds), `SLOT_NOT_PAIRED`, `SLOT_EXPIRED`, `SETUP_REQUIRED`, `ACCOUNTS_OFF`, `AUTH_FAILED`
  (`reason` `STALE_TIMESTAMP` or `BAD_SIGNATURE`, with the box's time).
- The box allows 12 card checks per minute in total (a signature check takes a fraction of a second on the box).

### Heartbeat

While a player is signed in the phone adds `acct` (the card number), `atime` and `asig` to its heartbeat, where `asig` signs
`v1:acct_report:<deviceId>:<ts>:<acct>:<atime>` (the heartbeat's own signature does not cover these). The box lowers the balance to
`atime` if it is lower; **a report can never raise a balance**. The heartbeat answer carries `"acct":"<number or empty>"`, `acct_name`
and `acct_bal`: the box's view of who is signed in on that slot. A phone that is still signed in locally but sees `acct` empty twice in a
row has been signed out by the box and locks.

### Coins and silent phones

When the phone acknowledges a coin (`ack`), the box adds the coin's seconds to the account signed in on that phone, once per `tx_id`,
before the queued payment is erased. A phone that stops reporting for 90 s is signed out by the box; the account keeps the last
reported balance.

### Pruning, storage and limits

- An account with no time that is not signed in is deleted after 30 days without use, or after 7 days if it never had any time.
  Accounts with time are never deleted. Up to 600 accounts at once. Nothing is deleted while the box has no clock.
- Accounts are written to a temporary file, read back and checked, and only then moved into place, with the previous good file kept as
  `accounts.bak`; after a power cut the newest intact copy of the three is loaded. The filesystem is formatted only the first time it
  is used; if it later fails to mount, accounts are switched off and nothing is erased.
- `/api/diagnostics` has an `accounts` object (`ready`, `fs_mounted`, `count`, `unsaved`, `save_failures`, `failing_now`, `saves_ok`),
  and the dashboard warns while saves are failing and while no card key is installed.
