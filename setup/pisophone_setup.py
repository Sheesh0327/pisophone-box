#!/usr/bin/env python3
"""PisoPhone Box Setup: one file that turns a bare ESP32-C3 into a working PisoPhone coin box.

    python3 pisophone_setup.py             the menu: set up a new coin box, check one, owner tools
    python3 pisophone_setup.py setup       set up a new coin box right away
    python3 pisophone_setup.py check       look at the coin box on USB (is it set up, is it online, its address)
    python3 pisophone_setup.py tools       the owner tools (slot license keys, time cards, admin QR code, recovery code, keys)
    python3 pisophone_setup.py licenses --box AA:BB:CC:DD:EE:FF --count 2     make slot license keys without the menu

Setting up a coin box takes four things from you: plug the board into this computer with a USB data cable, type the shop's Wi-Fi
name and password (the 2.4 GHz network: the ESP32-C3 cannot use 5 GHz), and press Enter. The program then
  1. writes the coin box software to the board (esptool, checked against the checksum CI published),
  2. gives the board the shop's Wi-Fi and a new admin password over the USB cable (the board accepts that only while it is new),
  3. waits until the board is on your Wi-Fi and tells you the address of its admin page.
Open that address in a browser, sign in, and use "Set up a phone" in Phone slots: the admin page installs the PisoPhone app on each
rental phone. The coin box and the phones all join the shop's own Wi-Fi; there is no router software in this product.

It needs Python 3.8+ and the internet; nothing else is installed by hand: esptool (and, for the owner tools, cryptography) are
installed into the program's own folder (~/.pisophone-box/lib, no root needed) the first time they are needed. Only the Python
standard library is used otherwise.

Slot licenses: every box has 2 free phone slots; each further slot needs a license key (P500) made with your owner key. The
owner tools make them: "Slot license keys" asks for the box code the customer reads from their admin page (Phone slots).
Build a standalone program with Nuitka (no Python needed on the computer that runs it):
    python -m nuitka --onefile --include-package=esptool --include-package=serial setup/pisophone_setup.py"""
import argparse
import base64
import contextlib
import csv
import datetime
import getpass
import hashlib
import importlib
import io
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_NAME = "PisoPhone Box Setup"
REPO = "Sheesh0327/pisophone-box"
DEFAULT_BRANCH = "main"
USER_AGENT = "PisoPhoneBoxSetup/1.0 (+https://github.com/Sheesh0327/pisophone-box)"
FLASH_BAUD = 460800   # fast, and still reliable on the cheap USB-serial chips of most boards
# the USB-serial chips on ESP32 boards: Espressif's own (the C3 boards), CH340, CP210x, FTDI, Prolific
USB_BOARD_IDS = {0x303A: "Espressif", 0x1A86: "CH340", 0x10C4: "CP210x", 0x0403: "FTDI", 0x067B: "Prolific"}
CHIP_RE = re.compile(r"(?:Chip is|Chip type:|Connected to)\s*(ESP32(?:-[CSHP]\d)?)", re.I)
PROGRESS_RE = re.compile(r"\((\d+) ?%\)")
SERIAL_BAUD = 115200
FREE_SLOTS, MAX_SLOTS, SLOT_PRICE_PHP = 2, 12, 500   # SlotLicense.h
SLOT_PREFIX = "PSLOT1"
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class SetupError(Exception):
    """A problem the owner can fix (the message says how)."""


def say(text=""):
    print(text, flush=True)


def raw_url(branch, path):
    return f"https://raw.githubusercontent.com/{REPO}/{branch}/{path}"


def html_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def generate(length):
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


# ---- where things are kept ---------------------------------------------------------------------------------------------------

def pisophone_home():
    return os.environ.get("PISOPHONE_BOX_HOME") or os.path.join(os.path.expanduser("~"), ".pisophone-box")


def private_lib_dir():
    return os.path.join(pisophone_home(), "lib")


def use_private_libs():
    """Makes the packages this program installed for itself importable."""
    lib = private_lib_dir()
    if os.path.isdir(lib) and lib not in sys.path:
        sys.path.insert(0, lib)
    importlib.invalidate_caches()


def default_out_dir():
    """Where the saved information of each coin box and the printable pages go: Documents/PisoPhone-Box, or PisoPhone-Box in the home folder."""
    if os.environ.get("PISOPHONE_BOX_HOME"):
        return os.path.join(os.environ["PISOPHONE_BOX_HOME"], "saved")
    home = pathlib.Path.home()
    docs = home / "Documents"
    return str((docs if docs.is_dir() else home) / "PisoPhone-Box")


def flash_cache_dir():
    return os.path.join(pisophone_home(), "firmware-cache")


def write_private_file(path, text):
    """Writes a text file readable only by its owner."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def ensure_package(module, pip_name, emit=say, run=subprocess.run):
    """Makes `import <module>` work: already there, or installed with pip into this program's own folder. False when it could not be."""
    use_private_libs()
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        pass
    lib = private_lib_dir()
    os.makedirs(lib, exist_ok=True)
    emit(f"Installing {pip_name} (once; it goes into {lib})...")
    argv = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade", "--target", lib, pip_name]
    for extra in ([], ["--break-system-packages"]):   # (Debian's pip refuses a plain install; --target is safe, so it is retried)
        try:
            result = run(argv + extra, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as e:
            emit(f"pip could not run ({e}).")
            return False
        if result.returncode == 0:
            break
        if "externally-managed" not in (result.stderr or "") + (result.stdout or ""):
            emit((result.stderr or result.stdout or "").strip().splitlines()[-1:] and (result.stderr or result.stdout).strip().splitlines()[-1])
            break
    use_private_libs()
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        emit(f"{pip_name} could not be installed. Is the internet on? Is pip installed (Debian family: sudo apt install python3-pip)?")
        return False


# ---- QR codes ----------------------------------------------------------------------------------------------------------------
# A small QR encoder (byte mode, error correction M, versions 1 to 10: up to 213 bytes), so nothing needs installing to draw a QR code.

_QR_M = {   # version: (data codewords per block list, EC codewords per block)
    1: ([16], 10), 2: ([28], 16), 3: ([44], 26), 4: ([32] * 2, 18), 5: ([43] * 2, 24), 6: ([27] * 4, 16), 7: ([31] * 4, 18),
    8: ([38] * 2 + [39] * 2, 22), 9: ([36] * 3 + [37] * 2, 22), 10: ([43] * 4 + [44], 26)}
_QR_ALIGN = {2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
             10: [6, 28, 50]}
_GF_EXP, _GF_LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i], _GF_LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_remainder(data, n):
    """The n Reed-Solomon error correction codewords of `data`."""
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= _gf_mul(c, 1)
            nxt[j + 1] ^= _gf_mul(c, _GF_EXP[i])
        gen = nxt
    rem = [0] * n
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(n):
            rem[i] ^= _gf_mul(gen[i + 1], factor)
    return rem


def _bch_remainder(value, poly, degree):
    """The BCH check bits for the format (degree 10, poly 0x537) and version (degree 12, poly 0x1F25) information."""
    rem = value
    for _ in range(degree):
        rem = (rem << 1) ^ ((rem >> (degree - 1)) * poly)
    return rem


def qr_matrix(text):
    """The QR code for a text (UTF-8, byte mode, level M) as a list of rows of booleans (True = dark), without a quiet zone."""
    raw = text.encode("utf-8")
    version = next((v for v in range(1, 11) if len(raw) <= sum(_QR_M[v][0]) - (2 if v < 10 else 3)), None)
    if version is None:
        raise ValueError("the text is too long for this QR encoder")
    blocks, ec_len = _QR_M[version]
    capacity = sum(blocks)
    bits = [0, 1, 0, 0]
    count_bits = 8 if version < 10 else 16
    bits += [(len(raw) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for byte in raw:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    for pad in range(capacity - len(data)):
        data.append(0xEC if pad % 2 == 0 else 0x11)
    # split into blocks, add the error correction, interleave
    parts, at = [], 0
    for size in blocks:
        chunk = data[at:at + size]
        at += size
        parts.append((chunk, _rs_remainder(chunk, ec_len)))
    out = []
    for i in range(max(blocks)):
        out += [c[i] for c, _e in parts if i < len(c)]
    for i in range(ec_len):
        out += [e[i] for _c, e in parts]
    stream = [(byte >> i) & 1 for byte in out for i in range(7, -1, -1)]
    size = 17 + 4 * version

    def blank():
        return [[None] * size for _ in range(size)]

    def finder(m, r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    m[rr][cc] = (0 <= dr <= 6 and 0 <= dc <= 6 and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)))

    base = blank()
    finder(base, 0, 0)
    finder(base, 0, size - 7)
    finder(base, size - 7, 0)
    pos = _QR_ALIGN.get(version, [])
    for r in pos:
        for c in pos:
            if base[r][c] is not None:
                continue   # (the three corners hold finders)
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    base[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1
    for i in range(8, size - 8):
        base[6][i] = base[i][6] = (i % 2 == 0)
    base[size - 8][8] = True   # the always-dark module
    reserved = [[cell is not None for cell in row] for row in base]
    for i in range(9):
        reserved[8][i] = reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = reserved[size - 1 - i][8] = True
    if version >= 7:
        for i in range(6):
            for j in range(3):
                reserved[i][size - 11 + j] = reserved[size - 11 + j][i] = True

    def place(mask):
        m = [row[:] for row in base]
        bit = 0
        col = size - 1
        up = True
        while col > 0:
            if col == 6:
                col -= 1
            for step in range(size):
                r = size - 1 - step if up else step
                for c in (col, col - 1):
                    if reserved[r][c]:
                        continue
                    v = bool(stream[bit]) if bit < len(stream) else False
                    bit += 1
                    if (mask == 0 and (r + c) % 2 == 0) or (mask == 1 and r % 2 == 0) or (mask == 2 and c % 3 == 0) or \
                       (mask == 3 and (r + c) % 3 == 0) or (mask == 4 and (r // 2 + c // 3) % 2 == 0) or \
                       (mask == 5 and (r * c) % 2 + (r * c) % 3 == 0) or (mask == 6 and ((r * c) % 2 + (r * c) % 3) % 2 == 0) or \
                       (mask == 7 and ((r + c) % 2 + (r * c) % 3) % 2 == 0):
                        v = not v
                    m[r][c] = v
            col -= 2
            up = not up
        data_bits = 0b00 << 3 | mask   # (error correction level M is 00)
        fmt = ((data_bits << 10) | _bch_remainder(data_bits, 0x537, 10)) ^ 0x5412
        for i in range(15):
            bit_i = bool((fmt >> i) & 1)
            if i < 6:
                m[i][8] = bit_i
            elif i < 8:
                m[i + 1][8] = bit_i
            elif i == 8:
                m[8][7] = bit_i
            else:
                m[8][14 - i] = bit_i
            if i < 8:
                m[8][size - 1 - i] = bit_i
            else:
                m[size - 15 + i][8] = bit_i
        if version >= 7:
            info = (version << 12) | _bch_remainder(version, 0x1F25, 12)
            for i in range(18):
                v = bool((info >> i) & 1)
                m[i // 3][size - 11 + i % 3] = v
                m[size - 11 + i % 3][i // 3] = v
        return m

    def penalty(m):
        score = 0
        for grid in (m, [list(col) for col in zip(*m)]):
            for row in grid:
                run = 1
                for i in range(1, size):
                    if row[i] == row[i - 1]:
                        run += 1
                        if run == 5:
                            score += 3
                        elif run > 5:
                            score += 1
                    else:
                        run = 1
                pattern = "".join("1" if v else "0" for v in row)
                score += 40 * (pattern.count("10111010000") + pattern.count("00001011101"))
        for r in range(size - 1):
            for c in range(size - 1):
                if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                    score += 3
        dark = sum(v for row in m for v in row)
        score += 10 * (abs(dark * 100 // (size * size) - 50) // 5)
        return score
    return min((place(mask) for mask in range(8)), key=penalty)


def qr_svg(text, module=8, quiet=4):
    """The QR code as an SVG picture (black modules on white, with the quiet zone the readers need)."""
    m = qr_matrix(text)
    n = len(m) + 2 * quiet
    cells = "".join(f'<rect x="{c + quiet}" y="{r + quiet}" width="1" height="1"/>' for r, row in enumerate(m) for c, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * module}" height="{n * module}" '
            f'shape-rendering="crispEdges"><rect width="{n}" height="{n}" fill="#fff"/><g fill="#000">{cells}</g></svg>')

def terminal_qr(text, tty=None):
    """The QR code for a text drawn with half-block characters and colours (black on white, so a phone reads it on any terminal),
    or an empty string when the output is not a terminal that shows colours."""
    if tty is None:
        tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and (os.name != "nt" or bool(os.environ.get("WT_SESSION")))
    if not tty:
        return ""
    try:
        rows = qr_matrix(text)
    except ValueError:
        return ""
    quiet = 2
    width = len(rows) + 2 * quiet
    grid = [[False] * width for _ in range(quiet)] + [[False] * quiet + list(r) + [False] * quiet for r in rows] + [[False] * width for _ in range(quiet)]
    if len(grid) % 2:
        grid.append([False] * width)
    lines = []
    for top, bottom in zip(grid[0::2], grid[1::2]):
        # "upper half block": its colour is the top module, the background is the bottom module (30 black / 97 white; 40 / 107)
        lines.append("".join(f"\x1b[{30 if t else 97};{40 if b else 107}m\u2580" for t, b in zip(top, bottom)) + "\x1b[0m")
    return "\n".join(lines)

# ---- the logo and the emblem on the printed pages ---------------------------------------------------------------------------
LOGO_LARGE_B64 = (
    "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"
)
CARD_EMBLEM_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAMAAADDpiTIAAADAFBMVEVa2t1jmaUOFx4i3OgSGiNc"
    "p+ad19oaWGOXbibf16MFESCqmFUbLVTPrVkFESAKH1dYYF8hnJtqVSYlp+7hx3AIn3kCNJUOXdUR"
    "K0vk6eVRNxvdaQHq59kkao+VrsibqKnz+Pj/4i0QKkrYnipR0rYC2KYbVmMR2KgAKO21jCv/5xuN"
    "cVPfoBH0+PUE+d4hRDr4qBb16F6fpK7zpRPFusXKs6AB0qizxLhdZ2yxztP/1ysCkmwCrJLw8KIF"
    "GIOzih2wusknWGMCyaQbbYpnbI6wdgv74lcAtYpYX2OsnFEvVFtZYmZpjlsGrYpncI9Odstsl5eE"
    "e4SYoaxXRyGdo52tzd7/0ST/4ln//KIJ+tcW3815gImfOwGwzMzXuFXVq0wqc4cB5H1MPUZfRyZh"
    "//eut8GzwbvhZADQuFzp57UAR9QAwH9GJwBhTzBue4tfcYdqm6Rjs/98yP+jo26+r14AAAD8/f3+"
    "qhABFSoAZf4ABA4NJUQSJDsAw5cAyLEAd/4ACSUB2NH+tBkHExoAh/7rmwoZM1AAGkcAWfYAuo8G"
    "1LGibBkmOE8kJyvU5uzr6u37uCUIpPr8xywDl/zT+fr9+q0yRVVkTCXN1tWYpq3+xhZOWWgF5tH7"
    "5m21xswCFS2rtLgASPP+1S9seIb++G361k+mq7L46I+vusQqJBmv5+38/NAC+O51hpFVRylVZHEE"
    "u6aQmqaFiZFDS1T99pL3yksDy8ex+fn+51RsVykKppCNlZkVRVQG6OUuV2grR2cuNDEpGRDGys/N"
    "2ubz2W6z2ehMU1hQdodJKQ1laXOtdBZSOBOZZhVtdHkQOWxr+/cr2smO+fYJJksFVtMrZ2/qqioI"
    "tvv8/fxU+/cAO/Hotk9rRhUqhesADCNy2c/Ohw0cc3SOWQ8mJyUQmIwBBhAmBQVQ6Oayhi3alBII"
    "ZtID5bIDJ22Q2erW49UNR43migEBBw//9lZQaIYG9dJ1yuuS5esLWLCy1tUmc3cxya3Mlirn3uAG"
    "SK3u2YyEvu//0xcNLsvpAAABAHRSTlP+/hv+6v7+/f7+V/73/pwO/v7+/v7///9cFv//9/7+/Wb6"
    "o/r/DAj2/v4N/wajBv+pDwtO//9b/w0DYgIHCgICBaCoAhICqKykBWNm/1L//wX/cAm1aZJYl1O1"
    "av+mE1dWAv9hCl4DArpiA/8Cp3C8qmVlRboA/v7+/v7+/v7+/v7//v7//v7+/v7+//7+/v7+//7+"
    "/v7+/v7+/v7+/v4E/v7+/v7//v/+//7///7+/v7+/v7+/v/+/v7+/v7//v7+//7+/v7+/v/+//7+"
    "/v7///4H//7//wb//v7//wL+//7/CP9v//////////7+//6y/v7//v///v7///7//v//G5wgpAAA"
    "wSBJREFUeNrt/Ql8FFeWJwqndoEkELvBLLZx2eV2Ld3VVV3V87p6me7p6ZmeN2/2fd77vre/b98y"
    "MxQRkkKZytRCKjNJLWiX0IYECAmQkNi0sIvVNosx2IUBY/BuwGVsF25/99x7I+JGZERkRCoF2O37"
    "M1iAlsy4/3PO/+wu93f/LHxNOXY+Xf3shX8PHo7rO373cLSXu3DhU6Zn4UItSPRf/j0Avi333pGe"
    "3rFQvXW48+fgCDYO/kRAg4qFDs33+x4AT/jt/+lCRX6femrBggVC0mfBAoSEFxaq3+9PF34PgCda"
    "7jvSqcyDxOcLQgXHicKsTz5oBKoPXgNt8D0Anly5R1K/YIGIjiCIHCcEg+h/s7r9Ck6kZ8GC+QsX"
    "ftd0wXcCAB1UOBc+BVIvyDLP8f6hwQtBTuKE1J385xA5IChAvOB7ADzm8/rrLyhyn4mlXlRVvsQH"
    "1z04LkopBQD8ADET6YLXyM/9tmuCbzMAfv3rP8DyjwSfkXtVd+fnrxkbuyBJFcKcHFkVpHd0fA+A"
    "R3/+7PdWwsNPf2cBFsv4++H4iuVL6sINvCTM0QFaQFjBwte+B8CjPD/6R38G/1v5f/wNb3q9FcgA"
    "rMioO1TDS0FhTg/SBORlpX8PgEdx0v8AFP8f/Pu/npdXaG7eRU7KXFObsXVdJs/NMQCQJkDMEG7/"
    "Rz/6v30PgDmW/T/7Efr99/7BX/f0jC7mLOid6K9YXrQxI/tlbu4BQBXBfMDAn/33P/oeAHMm+7/3"
    "e3D7f/j7vvG2I1dO8zxvTu858Wxlee3GtEI/zwmP6FQsmP+v4QX+Xvr3AEj9+YM/+zMs++u9Xl9X"
    "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"
)
CARD_EMBLEM_URI = "data:image/png;base64," + CARD_EMBLEM_B64   # the circle emblem alone, 512 px, for the card backs
LOGO_LARGE_URI = "data:image/png;base64," + LOGO_LARGE_B64


# ---- the board: finding it, writing the software ----------------------------------------------------------------------------

def esptool_module():
    """Espressif's flashing tool (installed on first use), or None."""
    if not ensure_package("esptool", "esptool"):
        return None
    import esptool
    return esptool


def esptool_major(tool):
    try:
        return int(str(tool.__version__).split(".")[0])
    except (AttributeError, ValueError):
        return 4


def usb_boards(comports=None):
    """[(port, description)] of the USB serial adapters coin box boards use, sorted. Empty when none is plugged in."""
    if comports is None:
        try:
            from serial.tools.list_ports import comports
        except ImportError:
            return []
    found = []
    for port in comports():
        if getattr(port, "vid", None) in USB_BOARD_IDS:
            found.append((port.device, port.description or USB_BOARD_IDS[port.vid]))
    return sorted(found)


class _Lines(io.TextIOBase):
    """Collects what esptool prints and hands it on line by line (its progress lines end in a carriage return)."""

    def __init__(self, emit):
        self.emit, self.buf = emit, ""

    def writable(self):
        return True

    def write(self, text):
        self.buf += text
        parts = re.split(r"[\r\n]", self.buf)
        self.buf = parts.pop()
        for part in parts:
            if part.strip():
                self.emit(part.rstrip())
        return len(text)

    def flush(self):
        if self.buf.strip():
            self.emit(self.buf.rstrip())
        self.buf = ""


def run_esptool(tool, argv, emit):
    """Runs one esptool command in this process. Raises SetupError (with the tool's last words) when it fails."""
    seen = []

    def take(line):
        seen.append(line)
        emit(line)
    out = _Lines(take)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            tool.main(argv)
    except SystemExit as e:
        if e.code not in (0, None):
            out.flush()
            raise SetupError("the flashing tool stopped: " + (seen[-1] if seen else str(e.code)))
    except Exception as e:   # esptool's own errors (FatalError, serial problems)
        out.flush()
        raise SetupError("the flashing tool failed: " + re.sub(r"\s+", " ", str(e)).strip())
    out.flush()
    return seen


def board_advice(message):
    """What to try when the board did not answer (esptool says 'Failed to connect', 'Permission denied' ...)."""
    low = message.lower()
    if "permission" in low or "access is denied" in low:
        return ("This computer may not use the USB port. Linux: add your user to the 'dialout' group and log in again. "
                "Another program (a serial monitor) may also be using the port.")
    if "failed to connect" in low or "timed out" in low or "no serial data" in low:
        return ("The board did not answer. Use a USB DATA cable, hold the board's BOOT button, tap RESET, release BOOT, and try "
                "again. Some boards need their USB driver (CH340 or CP210x) installed once.")
    return ""


def with_advice(error, evidence):
    text = str(error).rstrip(". ")
    advice = board_advice(evidence)
    return f"{text}. {advice}" if advice else text + "."


def flash_manifest(branch, cache_dir, opener=urllib.request.urlopen, emit=say):
    """The list of firmware images (release/flash/manifest.json in the repository). Offline, the copy kept from the last time is used."""
    path = os.path.join(cache_dir, "manifest.json")
    try:
        with opener(urllib.request.Request(raw_url(branch, "release/flash/manifest.json"), headers={"User-Agent": USER_AGENT}), timeout=20) as r:
            text = r.read().decode("utf-8")
        manifest = json.loads(text)
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except (urllib.error.URLError, OSError, ValueError):
        try:
            with open(path, encoding="utf-8") as f:
                manifest = json.load(f)
            emit("GitHub could not be reached: using the coin box software kept from the last time.")
        except (OSError, ValueError):
            raise SetupError("the coin box software list could not be downloaded. This computer needs internet for this step "
                             f"(it comes from github.com/{REPO}, release/flash/manifest.json).")
    if not isinstance(manifest.get("builds"), list) or not manifest["builds"]:
        raise SetupError("the repository has no coin box software for this branch yet.")
    return manifest


def choose_build(manifest, chip):
    name = (chip or "").upper()
    return next((b for b in manifest["builds"] if str(b.get("chip", "")).upper() == name), None)


def fetch_image(branch, build, cache_dir, opener=urllib.request.urlopen):
    """The path of the image, downloaded and checked against the manifest's size and sha256 (kept for next time)."""
    if re.search(r"[/\\]", build["file"]):
        raise SetupError("the software list is not valid.")
    path = os.path.join(cache_dir, f"{build['sha256'][:16]}-{build['file']}")

    def good(data):
        return len(data) == build["size"] and hashlib.sha256(data).hexdigest() == build["sha256"]
    try:
        with open(path, "rb") as f:
            if good(f.read()):
                return path
    except OSError:
        pass
    try:
        with opener(urllib.request.Request(raw_url(branch, f"release/flash/{build['file']}"), headers={"User-Agent": USER_AGENT}), timeout=90) as r:
            data = r.read()
    except (urllib.error.URLError, OSError) as e:
        raise SetupError(f"the coin box software could not be downloaded ({e}).")
    if not good(data):
        raise SetupError("the downloaded coin box software does not match its checksum. Try again in a minute.")
    os.makedirs(cache_dir, exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return path


def flash_box(branch, port, emit, cache_dir, opener=urllib.request.urlopen, tool=None, image=None):
    """Writes the software to the board on `port`: detects the chip, takes the image for it (checked; or `image`, a merged image
    you built yourself), erases the whole flash, writes and verifies it, restarts the board. Returns (chip, version)."""
    tool = tool or esptool_module()
    if tool is None:
        raise SetupError("the flashing tool could not be installed: run  python3 -m pip install esptool  and start this again.")
    major = esptool_major(tool)

    def name(text):   # esptool 5 spells its commands and options with hyphens (4 with underscores)
        return text.replace("_", "-") if major >= 5 else text
    base = ["--port", port, "--baud", str(FLASH_BAUD)]
    emit(f"== Looking at the board on {port}")
    seen = []
    try:
        run_esptool(tool, base + [name("chip_id")], seen.append)
    except SetupError as e:
        raise SetupError(with_advice(e, ' '.join(seen) + ' ' + str(e)))
    match = CHIP_RE.search("\n".join(seen))
    if not match:
        raise SetupError("the board did not say which chip it has. " + board_advice("failed to connect"))
    chip = match.group(1).upper()
    if chip != "ESP32-C3":
        raise SetupError(f"this board is an {chip}; this PisoPhone coin box software is for the ESP32-C3.")
    if image:
        path, version, offset = image, "your own build", 0
        emit(f"{chip} found. Writing {image}.")
    else:
        manifest = flash_manifest(branch, cache_dir, opener, emit)
        build = choose_build(manifest, chip)
        if not build:
            raise SetupError(f"the repository has no software for the {chip} yet.")
        version, offset = manifest.get("version", "?"), build["offset"]
        emit(f"{chip} found. Software version {version} ({build['size'] / 1048576:.2f} MB).")
        path = fetch_image(branch, build, cache_dir, opener)
    emit("== Writing the coin box software (erasing the board first; about a minute)")
    last = [-10]

    def progress(line):
        found = PROGRESS_RE.search(line)
        if found:
            pct = int(found.group(1))
            if pct // 10 == last[0] // 10 and pct < 100:
                return
            last[0] = pct
        emit(line)
    args = base + [name("write_flash"), "-e", "-z", name("--flash_mode"), "keep", name("--flash_freq"), "keep",
                   name("--flash_size"), "keep", hex(offset), path]
    try:
        run_esptool(tool, args, progress)
    except SetupError as e:
        raise SetupError(with_advice(e, str(e)))
    emit("Written and verified. The board restarts by itself.")
    return chip, version


# ---- talking to the board over USB (SerialProvision.h) ----------------------------------------------------------------------

def parse_status(line):
    """'PISO-BOX fw=1.0.0 mac=aa:bb:.. setup=needed wifi=down ip=-' -> {'fw': '1.0.0', 'mac': ..., 'setup': 'needed', ...}, or None."""
    if not line.startswith("PISO-BOX "):
        return None
    status = {}
    for part in line.split()[1:]:
        key, _, value = part.partition("=")
        if key:
            status[key] = value
    return status


def setup_line(wifi_name, wifi_password, admin_password):
    """The PISO-SETUP line: the three values hex-encoded, so no character in them can be mistaken for a separator."""
    return "PISO-SETUP " + " ".join(v.encode("utf-8").hex() for v in (wifi_name, wifi_password, admin_password))


class BoxLink:
    """One USB serial connection to the board. `port` is anything with write/read/close like pyserial's Serial."""

    def __init__(self, port, clock=time.monotonic, sleep=time.sleep):
        self.port, self.clock, self.sleep, self.buf = port, clock, sleep, b""

    @classmethod
    def open(cls, device):
        if not ensure_package("serial", "pyserial"):
            raise SetupError("the serial library (pyserial) could not be installed.")
        import serial
        # (DTR and RTS are set low BEFORE the port opens: on a C3 board, raising them resets the chip or puts it in download mode)
        port = serial.Serial()
        port.port, port.baudrate, port.timeout = device, SERIAL_BAUD, 0.2
        port.dtr = False
        port.rts = False
        try:
            port.open()
        except (OSError, serial.SerialException) as e:
            raise SetupError(with_advice(f"the board's USB port {device} could not be opened ({e})", str(e)))
        return cls(port)

    def close(self):
        try:
            self.port.close()
        except Exception:
            pass

    def send(self, line, chunk=24, pause=0.02):
        """Writes a line a few bytes at a time: the board's USB buffer is small and it reads between loops."""
        data = (line + "\n").encode("ascii")
        for i in range(0, len(data), chunk):
            self.port.write(data[i:i + chunk])
            self.sleep(pause)

    def read_line(self, timeout):
        """The next complete line the board prints (None after `timeout` seconds)."""
        end = self.clock() + timeout
        while True:
            if b"\n" in self.buf:
                line, _, self.buf = self.buf.partition(b"\n")
                return line.decode("utf-8", "replace").strip()
            if self.clock() >= end:
                return None
            chunk = self.port.read(256)
            if chunk:
                self.buf += chunk
            else:
                self.sleep(0.02)

    def expect(self, prefixes, timeout):
        """Reads lines until one starts with one of `prefixes` (the board's boot messages are skipped). None on timeout."""
        end = self.clock() + timeout
        while self.clock() < end:
            line = self.read_line(max(0.05, end - self.clock()))
            if line and line.startswith(tuple(prefixes)):
                return line
        return None

    def hello(self, timeout=12):
        """Asks the board who it is (once a second); returns its status dict, or None if no PisoPhone software answers."""
        end = self.clock() + timeout
        while self.clock() < end:
            self.send("PISO-HELLO")
            line = self.expect(["PISO-BOX "], 1.2)
            if line:
                return parse_status(line)
        return None


def provision_box(link, wifi_name, wifi_password, admin_password, emit=say, tries=3):
    """Gives a new board the shop's Wi-Fi and the admin password. Raises SetupError with the board's reason when it refuses."""
    reasons = {
        "already-set-up": "this box was set up already (to set it up again, write the software to it again: that erases it)",
        "bad-wifi-name": "the board did not accept that Wi-Fi name (1 to 32 plain characters)",
        "bad-wifi-password": "the board did not accept that Wi-Fi password (8 to 63 plain characters)",
        "bad-admin-password": "the board did not accept that admin password (8 or more characters, not the factory one)",
        "busy": "the board is busy; try again in a moment",
        "could-not-save": "the board could not save the settings (its flash may be damaged)",
    }
    for attempt in range(1, tries + 1):
        link.send(setup_line(wifi_name, wifi_password, admin_password))
        answer = link.expect(["PISO-SETUP-OK", "PISO-SETUP-ERR"], 8)
        if answer == "PISO-SETUP-OK":
            return
        if answer and answer.startswith("PISO-SETUP-ERR"):
            why = answer.split(" ", 1)[1].strip() if " " in answer else ""
            if why != "bad-format":   # (bad-format is a damaged line: it is sent again)
                raise SetupError(reasons.get(why, f"the board refused ({why})") + ".")
        emit(f"  The board did not take that (try {attempt} of {tries}); sending it again.")
    raise SetupError("the board did not answer the setup. Unplug it, plug it in again and run this again.")


def wait_online(find_device, wifi_name, emit=say, timeout=120, opener=BoxLink.open, clock=time.monotonic, sleep=time.sleep):
    """Waits until the board says it is on the Wi-Fi (it restarts first, and its USB port may come back under another name).
    Returns its status dict with the address; raises SetupError with what is usually wrong when it never joins."""
    end = clock() + timeout
    last = None
    shown = False
    while clock() < end:
        device = find_device()
        link = None
        if device:
            try:
                link = opener(device)
            except SetupError:
                link = None
        if link:
            try:
                status = link.hello(6)
            finally:
                link.close()
            if status:
                last = status
                if status.get("wifi") == "up" and status.get("ip", "-") != "-":
                    return status
                if not shown:
                    emit(f"  The board is up and joining '{wifi_name}'...")
                    shown = True
        sleep(2)
    if last is None:
        raise SetupError("the board did not come back on USB after the setup. Unplug it, plug it in again and run  check.")
    raise SetupError(f"the board could not join the Wi-Fi '{wifi_name}'. Usual causes: a wrong Wi-Fi password; a 5 GHz-only network "
                     "(the ESP32-C3 needs 2.4 GHz: many routers share one name for both, some need the 2.4 GHz band switched on); a "
                     "router that only lets known devices join. Run the setup again with the right details.")


# ---- the setup, start to finish --------------------------------------------------------------------------------------------

def wifi_problem(name, password):
    if not 1 <= len(name) <= 32 or any(ord(c) < 32 or ord(c) > 126 for c in name):
        return "the Wi-Fi name must be 1 to 32 plain characters (letters, digits, punctuation)."
    if not 8 <= len(password) <= 63 or any(ord(c) < 32 or ord(c) > 126 for c in password):
        return "the Wi-Fi password must be 8 to 63 plain characters. (A network without a password cannot be used.)"
    return None


def admin_password_problem(value):
    if len(value) < 8 or len(value) > 64:
        return "use 8 to 64 characters."
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        return "use plain letters, digits and punctuation, no spaces."
    if value == "Coinslot@Setup":
        return "that is the factory password."
    return None


def choose_board(boards, read, emit=say):
    """The board to use: the only one, or the one the owner picks. SetupError when none."""
    if not boards:
        raise SetupError("no coin box board found on USB. Plug the ESP32-C3 into this computer with a USB data cable (some cables only "
                         "charge), wait a few seconds and run this again.")
    if len(boards) == 1:
        emit(f"Board found: {boards[0][1]} on {boards[0][0]}.")
        return boards[0][0]
    emit("Several USB boards are connected:")
    for i, (port, description) in enumerate(boards, 1):
        emit(f"  {i}. {description} on {port}")
    while True:
        pick = read("Which one is the coin box? (number): ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(boards):
            return boards[int(pick) - 1][0]


def current_device(finder, port):
    """The board's serial port now: the one it had, or the only board connected (the port may come back under another name)."""
    boards = finder()
    if any(p == port for p, _d in boards):
        return port
    return boards[0][0] if len(boards) == 1 else port


def box_summary(status, wifi_name, admin_password):
    ip = status.get("ip", "-")
    return "\n".join([
        f"PisoPhone coin box, set up {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Admin page : http://{ip}/",
        "Sign in    : the password below (the user name is admin)",
        f"Password   : {admin_password}",
        f"Box code   : {status.get('mac', '?')}    (give this to the vendor when you buy a slot license; it is also on the admin page, Phone slots)",
        f"Wi-Fi      : {wifi_name}    (the coin box and all rental phones use this network)",
        f"Software   : {status.get('fw', '?')}",
        "",
        "Keep this file private. Slots: 2 are free, each more needs a license key.",
        "",
    ])


def run_setup(args, read=input, read_secret=getpass.getpass, emit=say, finder=usb_boards, flasher=flash_box, opener=BoxLink.open,
              waiter=wait_online, sleep=time.sleep, out_dir=None):
    """The whole setup of one new coin box. Returns the saved summary's path."""
    emit(f"== {APP_NAME}")
    emit("A new coin box needs: the board on USB, then the shop's Wi-Fi name and password. Nothing else.\n")
    port = args.port or choose_board(finder(), read, emit)

    # 1. is a working box there already? Writing the software erases it, revenue totals and slot licenses included
    link = None
    try:
        link = opener(port)
        status = link.hello(4)
    except SetupError:
        status = None
    finally:
        if link:
            link.close()
    if status and status.get("setup") == "done":
        emit(f"This board already runs a set-up coin box (box code {status.get('mac', '?')}).")
        emit("Writing the software again ERASES everything on it: its settings, its sales totals and its slot license keys.")
        emit("To only update a working box, use its admin page (Tools > Install update) instead.")
        if read("Type ERASE to go on, or press Enter to stop: ").strip() != "ERASE":
            raise SetupError("stopped; nothing was changed.")

    # 2. what the owner types
    wifi_name = args.wifi_name or read("Shop Wi-Fi name (2.4 GHz): ").strip()
    wifi_password = args.wifi_password or read_secret("Shop Wi-Fi password: ")
    problem = wifi_problem(wifi_name, wifi_password)
    if problem:
        raise SetupError(problem)
    admin_password = args.admin_password or generate(12)
    problem = admin_password_problem(admin_password)
    if problem:
        raise SetupError("the admin password: " + problem)

    # 3. the software
    chip, version = flasher(args.branch, port, emit, flash_cache_dir(), image=args.image)

    # 4. the Wi-Fi and the admin password
    emit("== Giving the board your Wi-Fi and a new admin password")
    status = None
    for _ in range(4):
        sleep(3)   # (the board restarts after it is written; its USB port goes away and comes back, sometimes under another name)
        try:
            link = opener(current_device(finder, port))
        except SetupError:
            continue
        try:
            status = link.hello(15)
            if status:
                if status.get("setup") != "needed":
                    raise SetupError("the board is not in first-time setup mode after writing the software. Run this again.")
                provision_box(link, wifi_name, wifi_password, admin_password, emit)
                break
        finally:
            link.close()
    if not status:
        raise SetupError("no PisoPhone software answered on the board after writing it. Unplug it, plug it in again and run  check.")

    # 5. online
    emit("== Waiting for the board to join your Wi-Fi (up to two minutes)")

    status = waiter(lambda: current_device(finder, port), wifi_name, emit)
    folder = out_dir or default_out_dir()
    mac = status.get("mac", "box").replace(":", "")
    path = write_private_file(os.path.join(folder, f"pisophone-box-{mac}.txt"), box_summary(status, wifi_name, admin_password))
    ip = status["ip"]
    emit("")
    emit("==== Done: the coin box is online ====")
    emit(f"  Admin page : http://{ip}/        (open it from a phone or computer on the same Wi-Fi)")
    emit(f"  Sign in    : admin / {admin_password}")
    emit(f"  Box code   : {status.get('mac', '?')}")
    emit(f"  Saved to   : {path}  (readable only by you)")
    qr = terminal_qr(f"http://{ip}/")
    if qr:
        emit("\n  Scan to open the admin page:\n" + qr)
    emit("\nNext: open the admin page, then Phone slots > Set up a phone: it installs the PisoPhone app on a rental phone.")
    emit(f"The first {FREE_SLOTS} phone slots are free. For more, buy a slot license key (P{SLOT_PRICE_PHP} each) using the box code above.")
    return path


def run_check(args, emit=say, finder=usb_boards, opener=BoxLink.open, read=input, probe=None):
    """Looks at the coin box on USB and, when it is online, whether its admin page answers. Changes nothing."""
    port = args.port or choose_board(finder(), read, emit)
    link = opener(port)
    try:
        status = link.hello(12)
    finally:
        link.close()
    if not status:
        raise SetupError("no PisoPhone coin box software answered on that board (it may be empty, or running other software).")
    emit(f"Software   : {status.get('fw', '?')}")
    emit(f"Box code   : {status.get('mac', '?')}")
    emit("Set up     : " + ("yes" if status.get("setup") == "done" else "no, run  setup  (it still has the factory settings)"))
    online = status.get("wifi") == "up" and status.get("ip", "-") != "-"
    emit("Wi-Fi      : " + ("joined, address " + status["ip"] if online else "not joined"))
    if online:
        probe = probe or http_ok
        emit("Admin page : " + ("answers at http://" + status["ip"] + "/" if probe(f"http://{status['ip']}/") else "does not answer from this computer (is it on the same Wi-Fi?)"))
    return status


def http_ok(url, opener=urllib.request.urlopen):
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=5) as r:
            return r.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ---- owner tools: slot license keys, time cards, the admin QR code, the phone recovery code ----------------------------------------
# The QR codes are drawn by this program's own QR encoder and printed from the browser, so only the signing needs a package
# (`cryptography`, installed on request). The key files are in the home folder, the same names the other PisoPhone tools use.
CARD_PREFIX = "PISO1"
CARD_MAX_SERIAL, CARD_MIN_SECONDS, CARD_MAX_SECONDS, CARD_MAX_BATCH = 65535, 60, 240 * 3600, 600
CARD_COLS, CARD_ROWS = 3, 4
CARD_LEDGER_FIELDS = ["serial", "seconds", "box", "printed", "batch", "sold", "note"]
ADMIN_PREFIX, RECOVERY_PREFIX, RECOVERY_MAX_MINUTES = "PISOADMIN1", "PISOREC1", 24 * 60
KEY_FILES = {   # kind: (file in the home folder, what it is for)
    "card": ("pisophone_card_key.pem", "Time cards"),
    "admin": ("pisophone-superadmin.key", "Admin QR code"),
    "owner": ("pisophone_license_key.pem", "Owner key (licenses, recovery)"),
}


class ToolError(Exception):
    """A problem the owner can fix (shown on the Owner tools page)."""


def keys_home():
    return os.environ.get("PISOPHONE_KEYS") or os.path.expanduser("~")


def key_path(kind):
    return os.path.join(keys_home(), KEY_FILES[kind][0])


def cards_dir():
    return os.path.join(keys_home(), "pisophone_cards")


def tool_crypto():
    use_private_libs()
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        raise ToolError("The key tools need the 'cryptography' package: run  python3 -m pip install cryptography")
    return hashes, serialization, ec


def b64u(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def load_key(kind, path=None):
    """The private key in the owner's key file (a ToolError when there is none or it is not a P-256 key)."""
    _hashes, serialization, ec = tool_crypto()
    path = path or key_path(kind)
    if not os.path.isfile(path):
        raise ToolError(f"There is no {KEY_FILES[kind][1].lower()} key yet. Put your key file at the path shown by  Owner tools > Your keys.")
    try:
        with open(path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
    except Exception:
        raise ToolError(f"The key file {path} could not be read (it must be an unprotected PEM key).")
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ToolError(f"The key file {path} is not a P-256 key.")
    return key


def key_fingerprint(key):
    _hashes, serialization, _ec = tool_crypto()
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def write_private_key(kind, pem, replace=False):
    """Saves a key file (mode 600); an existing key is only replaced on request and then kept as <file>.old."""
    path = key_path(kind)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        if not replace:
            raise ToolError(f"There already is a key at {path}. Replacing it makes everything printed with the old one stop working.")
        os.replace(path, path + ".old")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem if isinstance(pem, bytes) else pem.encode())
    return path


def import_key(kind, text=None, path=None, replace=False):
    """Saves a key the owner already has (its PEM text, or the path of its file on this computer) after checking it."""
    _hashes, serialization, ec = tool_crypto()
    if path:
        path = os.path.expanduser(path.strip())
        if not os.path.isfile(path):
            raise ToolError(f"No file at {path}.")
        with open(path, "rb") as f:
            raw = f.read(20000)
    else:
        raw = (text or "").strip().encode()
    if not raw:
        raise ToolError("Paste the key (the text between -----BEGIN and -----END) or type the path of its file.")
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except Exception:
        raise ToolError("That is not a usable key: it must be an unprotected PEM private key.")
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ToolError("That is not a P-256 key.")
    write_private_key(kind, raw if raw.endswith(b"\n") else raw + b"\n", replace)
    return key


def create_key(kind, replace=False):
    """Makes a new key. Boxes only accept what their firmware was built to trust, so a new card or admin key works only after the
    firmware is rebuilt with its public half (a developer step); the page says so."""
    _hashes, serialization, ec = tool_crypto()
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    write_private_key(kind, pem, replace)
    return key


# -- time cards --------------------------------------------------------------------------------------------------------------------
def card_box(text):
    """'AA:BB:CC:DD:EE:FF', 'aa-bb-...' or 'aabbccddeeff' -> 'aabbccddeeff'."""
    box = re.sub(r"[^0-9a-fA-F]", "", text or "").lower()
    if not re.fullmatch(r"[0-9a-f]{12}", box):
        raise ToolError("That is not a box address: type the 12 characters shown on the coin box's page (like AA:BB:CC:DD:EE:FF).")
    return box


def card_text(key, box, serial, seconds):
    hashes, _serialization, ec = tool_crypto()
    sig = key.sign(f"pisophone-card-v1|{box}|{serial}|{seconds}".encode(), ec.ECDSA(hashes.SHA256()))
    return f"{CARD_PREFIX}.{box}.{serial}.{seconds}.{base64.b64encode(sig).decode()}"


def card_verify(public_key, text):
    hashes, _serialization, ec = tool_crypto()
    from cryptography.exceptions import InvalidSignature
    try:
        prefix, box, serial, seconds, sig = text.split(".")
        public_key.verify(base64.b64decode(sig, validate=True), f"pisophone-card-v1|{box}|{serial}|{seconds}".encode(), ec.ECDSA(hashes.SHA256()))
        return prefix == CARD_PREFIX
    except (ValueError, InvalidSignature):
        return False


def read_card_ledger(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def next_serials(rows, count):
    used = [int(r["serial"]) for r in rows]
    if len(used) != len(set(used)):
        raise ToolError("The ledger lists a serial twice; fix the ledger file by hand before printing more cards.")
    start = (max(used) if used else 0) + 1
    if start + count - 1 > CARD_MAX_SERIAL:
        raise ToolError(f"Not enough card numbers are left for this box: {CARD_MAX_SERIAL - start + 1} remain, {count} asked for.")
    return list(range(start, start + count))


def known_boxes(directory=None):
    """The boxes cards were printed for before (from their ledger files): [{box, printed, sold, last}]."""
    directory = directory or cards_dir()
    found = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for name in names:
        m = re.fullmatch(r"ledger_([0-9a-f]{12})\.csv", name)
        if not m:
            continue
        try:
            rows = read_card_ledger(os.path.join(directory, name))
        except (OSError, csv.Error):
            continue
        found.append({"box": m.group(1), "printed": len(rows), "sold": sum(1 for r in rows if (r.get("sold") or "").strip()),
                      "last": max((int(r["serial"]) for r in rows), default=0)})
    return found


def card_back_position(col, row, flip):
    """Where the back of the card at (col, row) goes once the paper is turned over (long edge: left and right swap)."""
    return (CARD_COLS - 1 - col, row) if flip == "long" else (col, CARD_ROWS - 1 - row)


_CARD_CSS = """@page{size:A4;margin:0}*{box-sizing:border-box}body{margin:0;background:#fff;font-family:system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.sheet{position:relative;width:210mm;height:297mm;page-break-after:always;overflow:hidden;background:#fff}.sheet:last-child{page-break-after:auto}
.card{position:absolute;width:61mm;height:66.6mm;border:.3mm solid #787878;text-align:center;color:#000}
.card b{display:block;margin-top:4.5mm;font-size:5.6mm;line-height:6mm}.card .h{margin-top:.8mm;font-size:3.7mm;line-height:4.4mm;color:#3c3c3c}
.card svg{position:absolute;left:50%;top:17mm;width:38mm;height:38mm;margin-left:-19mm}
.card .s{position:absolute;left:0;right:0;top:56.5mm;font-size:2.9mm}.card .n{position:absolute;left:0;right:0;bottom:2.4mm;font-size:2.7mm;color:#505050}
.back{position:absolute;width:61mm;height:66.6mm;border:.3mm solid #787878;display:flex;align-items:center;justify-content:center}.back img{width:42mm;height:42mm}
.note{position:absolute;left:8mm;right:8mm;bottom:3mm;font-size:3mm;color:#666;text-align:center}
@media screen{body{background:#dde3ee;padding:10px}.sheet{margin:0 auto 12px;box-shadow:0 1px 6px #0006}.note{position:static;margin:4px 0}}
@media print{.noprint{display:none}}"""


def _card_pos(col, row):
    cell_w, cell_h = (2480 - 200) / 3, (3508 - 200) / 4
    mm = 25.4 / 300
    return ((100 + col * cell_w + 20) * mm, (100 + row * cell_h + 20) * mm)


def card_front_html(cards, hours_label, per_page=CARD_COLS * CARD_ROWS):
    sheets = []
    for p in range(0, len(cards), per_page):
        body = []
        for i, (serial, text) in enumerate(cards[p:p + per_page]):
            x, y = _card_pos(i % CARD_COLS, i // CARD_COLS)
            body.append(f'<div class="card" style="left:{x:.2f}mm;top:{y:.2f}mm"><b>PisoPhone</b><div class="h">{html_escape(hours_label)} play card</div>'
                        f'{qr_svg(text, 4, 4)}<div class="s">Scan at the PisoPhone to start</div><div class="n">No. {serial:05d}</div></div>')
        sheets.append('<div class="sheet">' + "".join(body) + "</div>")
    return sheets


def card_back_html(count, flip, shift=(0.0, 0.0), per_page=CARD_COLS * CARD_ROWS):
    sheets = []
    for p in range(0, count, per_page):
        body = []
        for i in range(min(per_page, count - p)):
            bc, br = card_back_position(i % CARD_COLS, i // CARD_COLS, flip)
            x, y = _card_pos(bc, br)
            body.append(f'<div class="back" style="left:{x + shift[0]:.2f}mm;top:{y + shift[1]:.2f}mm"><img src="{CARD_EMBLEM_URI}" alt=""></div>')
        sheets.append('<div class="sheet">' + "".join(body) + "</div>")
    return sheets


def cards_page(title, sheets, hint):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html_escape(title)}</title><style>{_CARD_CSS}</style></head><body><p class=\"note noprint\">{html_escape(hint)}</p>"
            + "".join(sheets) + "</body></html>")


def make_cards(box, count, hours, note="", flip="long", shift=(0.0, 0.0), out=None, key=None):
    """Signs `count` new cards for one box, reserves their serial numbers in the box's ledger and writes the printable pages.
    Returns {folder, files: [(label, path)], first, last, ledger}."""
    box = card_box(box)
    try:
        count = int(count)
        hours = float(hours)
    except (TypeError, ValueError):
        raise ToolError("Type a whole number of cards and the hours as a number.")
    if not 1 <= count <= CARD_MAX_BATCH:
        raise ToolError(f"Make 1 to {CARD_MAX_BATCH} cards at a time.")
    seconds = int(round(hours * 3600))
    if not CARD_MIN_SECONDS <= seconds <= CARD_MAX_SECONDS:
        raise ToolError("The hours must be between 1 minute and 240 hours.")
    key = key or load_key("card")
    out = out or cards_dir()
    os.makedirs(out, exist_ok=True)
    ledger = os.path.join(out, f"ledger_{box}.csv")
    serials = next_serials(read_card_ledger(ledger), count)
    cards = [(s, card_text(key, box, s, seconds)) for s in serials]
    for s, text in cards:   # never print a card the box would refuse
        if not card_verify(key.public_key(), text):
            raise ToolError(f"Self-check failed for card {s}: nothing was printed.")
    batch = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    # the serials are reserved before anything is drawn: if something fails below they are skipped, never reused
    new = not os.path.exists(ledger)
    with open(ledger, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CARD_LEDGER_FIELDS)
        if new:
            w.writeheader()
        for s in serials:
            w.writerow({"serial": s, "seconds": seconds, "box": box, "printed": stamp, "batch": batch, "sold": "", "note": note})
    folder = os.path.join(out, f"batch_{batch}")
    os.makedirs(folder, exist_ok=True)
    label = f"{hours:g}-hour"
    fronts = card_front_html(cards, label)
    backs = card_back_html(len(cards), flip, shift)
    pairs = [s for pair in zip(fronts, backs) for s in pair]
    howto = "Print at 100% / 'actual size' on A4, no margins and no headers or footers (Ctrl+P)."
    files = []
    for name, title, sheets, hint in (
            ("1-fronts.html", "Cards: QR sides", fronts, howto + " Print this first."),
            ("2-backs.html", "Cards: logo sides", backs, howto + " Put the printed sheets back so the backs print on the other side (try one sheet first)."),
            ("duplex-both-sides.html", "Cards: both sides", pairs, howto + " For a printer that prints on both sides: flip on the " + flip + " edge.")):
        path = os.path.join(folder, name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cards_page(title, sheets, hint))
        files.append((title, path))
    return {"folder": folder, "files": files, "first": serials[0], "last": serials[-1], "ledger": ledger, "box": box, "hours": label,
            "sheets": len(fronts), "cards": cards}


# -- the admin QR code and the phone recovery code ----------------------------------------------------------------------------
def admin_qr_text(key):
    """PISOADMIN1.<d>.<x>.<y>: the P-256 private number and the public point (32 bytes each, base64url)."""
    n = key.private_numbers()
    return ".".join([ADMIN_PREFIX, b64u(n.private_value.to_bytes(32, "big")), b64u(n.public_numbers.x.to_bytes(32, "big")),
                     b64u(n.public_numbers.y.to_bytes(32, "big"))])


def recovery_text(key, expiry):
    hashes, _serialization, ec = tool_crypto()
    sig = key.sign(f"pisophone-recover-v1|{expiry}".encode(), ec.ECDSA(hashes.SHA256()))
    return f"{RECOVERY_PREFIX}.{expiry}." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def qr_page(title, line, text, warning):
    """A printable page with one QR code (the admin code or a recovery code)."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html_escape(title)}</title><style>@page{{size:A4;margin:14mm}}body{{font:16px/1.45 system-ui,Segoe UI,Roboto,sans-serif;"
            f"text-align:center;margin:0;padding:20px;color:#111}}svg{{width:100%;max-width:420px;height:auto}}.w{{border:2px dashed #b00;color:#800;"
            f"border-radius:10px;padding:8px 12px;margin:16px auto;max-width:520px;font-weight:600}}</style></head><body>"
            f'<img src="{LOGO_LARGE_URI}" alt="" width="64" height="64"><h1>{html_escape(title)}</h1><p>{html_escape(line)}</p>{qr_svg(text, 6, 4)}'
            f'<div class="w">{html_escape(warning)}</div></body></html>')


def save_tool_page(name, html):
    folder = default_out_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def make_admin_qr(key=None):
    key = key or load_key("admin")
    text = admin_qr_text(key)
    page = qr_page("PisoPhone owner admin QR code", "On a coin box's login page tap Scan admin QR and show this code to the camera.", text,
                   "Keep this private: it opens super admin on every box. Print it and store it away from the shop.")
    return {"svg": qr_svg(text, 6, 4), "path": save_tool_page("pisophone-admin-qr.html", page), "fingerprint": key_fingerprint(key)}


def make_recovery_qr(minutes, key=None):
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        raise ToolError("Type the minutes as a number.")
    if not 1 <= minutes <= RECOVERY_MAX_MINUTES:
        raise ToolError(f"The code can work for 1 to {RECOVERY_MAX_MINUTES} minutes.")
    key = key or load_key("owner")
    expiry = int(time.time()) + minutes * 60
    text = recovery_text(key, expiry)
    until = time.strftime("%H:%M", time.localtime(expiry))
    page = qr_page("PisoPhone recovery code", f"Works until {until}. On the phone's lock screen: admin entry > SCAN A RECOVERY CODE.", text,
                   "Anyone holding this code can remove PisoPhone from a phone until it expires. Delete it afterwards.")
    return {"svg": qr_svg(text, 6, 4), "path": save_tool_page("pisophone-recovery-code.html", page), "until": until, "minutes": minutes}


# ---- slot license keys (SlotLicense.h) ---------------------------------------------------------------------------------------

def licenses_ledger():
    return os.path.join(keys_home(), "pisophone_licenses.csv")


LICENSE_LEDGER_FIELDS = ["made", "box", "key", "note"]


def read_license_ledger(path=None):
    path = path or licenses_ledger()
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def slot_license_text(key, box, key_id):
    """PSLOT1.<box code>.<key number>.<signature>: opens ONE more phone slot on that box, once (SlotLicense.h)."""
    hashes, _serialization, ec = tool_crypto()
    sig = key.sign(f"pisophone-slot-v1|{box}|{key_id}".encode(), ec.ECDSA(hashes.SHA256()))
    return f"{SLOT_PREFIX}.{box}.{key_id}.{base64.b64encode(sig).decode()}"


def slot_license_verify(public_key, text):
    hashes, _serialization, ec = tool_crypto()
    from cryptography.exceptions import InvalidSignature
    try:
        prefix, box, key_id, sig = text.split(".")
        public_key.verify(base64.b64decode(sig, validate=True), f"pisophone-slot-v1|{box}|{key_id}".encode(), ec.ECDSA(hashes.SHA256()))
        return prefix == SLOT_PREFIX
    except (ValueError, InvalidSignature):
        return False


def make_slot_licenses(box, count, note="", key=None, ledger=None):
    """Signs `count` new slot license keys for one box and records them in the ledger. A box holds at most MAX_SLOTS - FREE_SLOTS keys.
    Returns {'box', 'keys': [text], 'total', 'ledger'}."""
    box = card_box(box)
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ToolError("Type how many keys as a whole number.")
    room = MAX_SLOTS - FREE_SLOTS
    ledger = ledger or licenses_ledger()
    have = [r for r in read_license_ledger(ledger) if r.get("box") == box]
    if not 1 <= count <= room:
        raise ToolError(f"Make 1 to {room} keys at a time.")
    if len(have) + count > room:
        raise ToolError(f"A box holds at most {room} license keys ({FREE_SLOTS} slots are free, {MAX_SLOTS} is the most). This box already has "
                        f"{len(have)} on record, so {room - len(have)} more can be made.")
    key = key or load_key("owner")
    used = {r.get("key") for r in have}
    made = []
    for _ in range(count):
        key_id = secrets.token_hex(4)
        while key_id in used:
            key_id = secrets.token_hex(4)
        used.add(key_id)
        text = slot_license_text(key, box, key_id)
        if not slot_license_verify(key.public_key(), text):   # never hand out a key the box would refuse
            raise ToolError("Self-check failed: nothing was made.")
        made.append((key_id, text))
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    new = not os.path.exists(ledger)
    os.makedirs(os.path.dirname(ledger) or ".", exist_ok=True)
    with open(ledger, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LICENSE_LEDGER_FIELDS)
        if new:
            w.writeheader()
        for key_id, _text in made:
            w.writerow({"made": stamp, "box": box, "key": key_id, "note": note})
    return {"box": box, "keys": [t for _i, t in made], "total": len(have) + count, "ledger": ledger}


def reissue_slot_licenses(box, key=None, ledger=None):
    """New signatures for the keys already sold to a box (after the box was erased and its keys were lost). The key numbers are the
    recorded ones, so the box can never end up with more slots than were sold. Returns the keys."""
    box = card_box(box)
    rows = [r for r in read_license_ledger(ledger or licenses_ledger()) if r.get("box") == box]
    if not rows:
        raise ToolError("No keys were made for that box on this computer.")
    key = key or load_key("owner")
    return [slot_license_text(key, box, r["key"]) for r in rows]


# ---- the menus ------------------------------------------------------------------------------------------------------------------

def tools_menu(read=input, read_secret=getpass.getpass, emit=say):
    """The owner tools. Every tool asks for what it needs, does one thing and returns to the menu."""
    if not ensure_package("cryptography", "cryptography", emit):
        raise SetupError("the owner tools sign with the 'cryptography' package, which could not be installed. "
                         "Debian family: sudo apt install python3-cryptography")
    while True:
        emit("\nOwner tools")
        emit(f"  1. Slot license keys   (each opens one more phone slot on one box; the price is P{SLOT_PRICE_PHP})")
        emit("  2. Re-issue a box's slot license keys   (after the box was erased)")
        emit("  3. Time cards   (printable QR cards a player scans for play time)")
        emit("  4. Admin QR code   (signs you in as the owner on any coin box)")
        emit("  5. Phone recovery code   (removes PisoPhone from a locked phone)")
        emit("  6. Your keys   (which key files this computer has)")
        emit("  0. Back")
        choice = read("Choose: ").strip()
        try:
            if choice in ("0", ""):
                return
            if choice == "1":
                box = read("Box code of the customer's coin box (shown on their admin page, Phone slots): ")
                count = read("How many keys? [1]: ").strip() or "1"
                note = read("A note for your records (customer name, receipt) [none]: ").strip()
                made = make_slot_licenses(box, count, note)
                emit(f"\nGive the customer these {len(made['keys'])} key(s) after they pay P{SLOT_PRICE_PHP} each. Each one opens ONE slot, on box "
                     f"{made['box']} only, and works once:\n")
                for text in made["keys"]:
                    emit(text)
                emit(f"\nThey paste one at a time into their admin page: Phone slots > Add slot. This box now has {made['total']} key(s) on record "
                     f"({FREE_SLOTS + made['total']} slots). Recorded in {made['ledger']}")
            elif choice == "2":
                box = read("Box code: ")
                for text in reissue_slot_licenses(box):
                    emit(text)
                emit("\nThese are the keys already sold to that box (new signatures, the same key numbers). Paste them into its admin page again.")
            elif choice == "3":
                boxes = known_boxes()
                if boxes:
                    emit("Boxes you printed cards for: " + ", ".join(f"{b['box']} ({b['printed']} printed)" for b in boxes))
                box = read("Box code: ")
                count = read("How many cards? [12]: ").strip() or "12"
                hours = read("Hours of play on each card [1]: ").strip() or "1"
                result = make_cards(box, count, hours)
                emit(f"\nMade cards {result['first']} to {result['last']} for box {result['box']} ({result['hours']} each).")
                emit("Open these files in a browser and print them (A4, 100%, no margins):")
                for title, path in result["files"]:
                    emit(f"  {title}: {path}")
            elif choice == "4":
                result = make_admin_qr()
                emit(f"Saved {result['path']}\nOpen it in a browser and print it. Keep it private: it opens the owner area of every coin box.")
            elif choice == "5":
                minutes = read("How many minutes should the code work? [10]: ").strip() or "10"
                result = make_recovery_qr(minutes)
                emit(f"Saved {result['path']}\nOpen it on a screen near the phone. It works until {result['until']}.")
            elif choice == "6":
                for kind, (name, what) in KEY_FILES.items():
                    path = key_path(kind)
                    state = "missing"
                    if os.path.isfile(path):
                        try:
                            state = "found, fingerprint " + key_fingerprint(load_key(kind))
                        except ToolError as e:
                            state = f"unusable: {e}"
                    emit(f"  {what:<28} {path}  {state}")
                emit("\nThe owner key signs firmware updates and slot license keys; the box trusts the public half built into its software "
                     "(esp32_firmware/include/LicensePubKey.h). A key made new here only works after the software is rebuilt with its public half.")
        except ToolError as e:
            emit(f"\n  {e}")


def main_menu(args, read=input, emit=say):
    while True:
        emit(f"\n{APP_NAME}")
        emit("  1. Set up a new coin box   (ESP32-C3 on USB)")
        emit("  2. Check a coin box")
        emit("  3. Owner tools   (slot license keys, time cards, ...)")
        emit("  0. Quit")
        choice = read("Choose: ").strip()
        try:
            if choice in ("0", "q", ""):
                return 0
            if choice == "1":
                run_setup(args)
            elif choice == "2":
                run_check(args)
            elif choice == "3":
                tools_menu()
        except SetupError as e:
            emit(f"\nProblem: {e}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="pisophone_setup.py", description="Set up PisoPhone coin boxes (ESP32-C3) and make slot license keys.")
    p.add_argument("command", nargs="?", choices=["setup", "check", "tools", "licenses"], help="what to do (default: a menu)")
    p.add_argument("--port", help="the board's serial port (default: found by itself)")
    p.add_argument("--branch", default=DEFAULT_BRANCH, help="the repository branch to take the software from (default main)")
    p.add_argument("--image", help="write this merged image instead of the published one (a build of your own)")
    p.add_argument("--wifi-name", help="the shop's Wi-Fi name (default: asked)")
    p.add_argument("--wifi-password", help="the shop's Wi-Fi password (default: asked; on a shared computer do not use this option)")
    p.add_argument("--admin-password", help="the admin password (default: a new strong one is made)")
    p.add_argument("--box", help="licenses: the box code of the coin box")
    p.add_argument("--count", default="1", help="licenses: how many keys")
    p.add_argument("--note", default="", help="licenses: a note for your records")
    p.add_argument("--reissue", action="store_true", help="licenses: print the keys already made for the box again")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "setup":
            run_setup(args)
        elif args.command == "check":
            run_check(args)
        elif args.command == "tools":
            return tools_menu() or 0
        elif args.command == "licenses":
            if not args.box:
                raise SetupError("licenses needs --box AA:BB:CC:DD:EE:FF (the box code from the customer's admin page).")
            if not ensure_package("cryptography", "cryptography"):
                raise SetupError("the 'cryptography' package could not be installed.")
            if args.reissue:
                for text in reissue_slot_licenses(args.box):
                    say(text)
            else:
                for text in make_slot_licenses(args.box, args.count, args.note)["keys"]:
                    say(text)
        else:
            return main_menu(args)
    except (SetupError, ToolError) as e:
        print(f"\nProblem: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

