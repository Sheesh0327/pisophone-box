#!/usr/bin/env python3
"""Tests of setup/pisophone_setup.py without a board, a network or a real key: a pretend coin box on a pretend USB port (it follows the
rules in esp32_firmware/include/SerialProvision.h), the flashing steps with a pretend esptool, the slot license keys against the real
firmware code (SlotLicense.h compiled with the system C++ compiler and mbedtls when they are there), the time cards and the ledgers.
Run:  python3 tests/test_setup.py      (needs: pip install cryptography)"""
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="pisophone-box-test-")
os.environ["PISOPHONE_BOX_HOME"] = os.path.join(TMP, "home")
os.environ["PISOPHONE_KEYS"] = os.path.join(TMP, "keys")
os.makedirs(os.environ["PISOPHONE_KEYS"])

spec = importlib.util.spec_from_file_location("pisophone_setup", os.path.join(ROOT, "setup", "pisophone_setup.py"))
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

try:
    from cryptography.hazmat.primitives import serialization
except ImportError:
    print("skipped: the cryptography package is not installed")
    sys.exit(0)

checks = failures = 0


def check(ok, name):
    global checks, failures
    checks += 1
    if not ok:
        failures += 1
        print("FAIL:", name)


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return str(e)
    return None


class FakeBox:
    """The coin box on the other end of the USB cable: SerialProvision.h's rules in Python."""

    def __init__(self, mac="aa:bb:cc:00:11:22", setup_done=False, drop_first=False, joins=True):
        self.mac, self.setup_done, self.drop_first, self.joins = mac, setup_done, drop_first, joins
        self.rx = b""
        self.out = b"boot noise\n"
        self.wifi = self.admin = None
        self.closed = False
        self.setup_lines = 0

    def write(self, data):
        self.rx += data
        while b"\n" in self.rx:
            line, _, self.rx = self.rx.partition(b"\n")
            self.handle(line.decode("ascii", "replace").strip())

    def handle(self, line):
        if line == "PISO-HELLO":
            up = self.setup_done and self.joins
            self.out += f"PISO-BOX fw=1.0.0 mac={self.mac} setup={'done' if self.setup_done else 'needed'} wifi={'up' if up else 'down'} ip={'192.168.1.77' if up else '-'}\n".encode()
        elif line.startswith("PISO-SETUP "):
            self.setup_lines += 1
            if self.setup_done:
                self.out += b"PISO-SETUP-ERR already-set-up\n"
                return
            if self.drop_first and self.setup_lines == 1:
                line = line[:-3]   # a damaged line (the USB buffer overflowed)
            words = line.split(" ")[1:]
            try:
                assert len(words) == 3
                ssid, pw, admin = (bytes.fromhex(w).decode("ascii") for w in words)
                assert 1 <= len(ssid) <= 32 and 8 <= len(pw) <= 63 and len(admin) >= 8 and admin != "Coinslot@Setup"
            except (AssertionError, ValueError):
                self.out += b"PISO-SETUP-ERR bad-format\n"
                return
            self.wifi, self.admin, self.setup_done = (ssid, pw), admin, True
            self.out += b"PISO-SETUP-OK\n"

    def read(self, n):
        data, self.out = self.out[:n], self.out[n:]
        return data

    def close(self):
        self.closed = True


def link_for(box):
    return ps.BoxLink(box, sleep=lambda s: None)


# ---- the line the setup sends --------------------------------------------------------------------------------------------------
line = ps.setup_line("Dela Cruz 5G", "p@ss w0rd!", "Shop-Admin-2026")
check(line.startswith("PISO-SETUP ") and len(line.split(" ")) == 4 and all(c in "0123456789abcdef" for c in "".join(line.split(" ")[1:])),
      "the setup line is three hex words")
check(bytes.fromhex(line.split(" ")[1]).decode() == "Dela Cruz 5G", "and they decode to what was typed")
check(ps.parse_status("PISO-BOX fw=1.0.0 mac=aa:bb setup=needed wifi=down ip=-") == {"fw": "1.0.0", "mac": "aa:bb", "setup": "needed", "wifi": "down", "ip": "-"},
      "a status line is read")
check(ps.parse_status("hello") is None and ps.parse_status("") is None, "other lines are not a status")

# ---- the pretend box -------------------------------------------------------------------------------------------------------------
box = FakeBox()
link = link_for(box)
status = link.hello(5)
check(status and status["setup"] == "needed" and status["mac"] == "aa:bb:cc:00:11:22", "hello finds a new box (boot noise ignored)")
ps.provision_box(link, "Dela Cruz 5G", "p@ss w0rd!", "Shop-Admin-2026", emit=lambda t: None)
check(box.wifi == ("Dela Cruz 5G", "p@ss w0rd!") and box.admin == "Shop-Admin-2026", "provisioning gives the box the Wi-Fi and the admin password")
msg = raises(ps.SetupError, ps.provision_box, link, "x", "12345678", "Shop-Admin-2026", emit=lambda t: None)
check(msg and "set up already" in msg, "a box that was set up refuses a second setup: " + str(msg))
dropped = FakeBox(drop_first=True)
ps.provision_box(link_for(dropped), "shop", "12345678", "Shop-Admin-2026", emit=lambda t: None)
check(dropped.setup_lines == 2 and dropped.setup_done, "a damaged line is sent again")
check(raises(ps.SetupError, ps.provision_box, link_for(FakeBox(setup_done=False)), "shop", "short", "Shop-Admin-2026", emit=lambda t: None) is not None,
      "a Wi-Fi password the board will not take is refused (after retries)")

class Silent(FakeBox):
    def handle(self, line):
        pass
check(Silent() and link_for(Silent()).hello(0.3) is None, "a board that says nothing is not a PisoPhone box")
check(raises(ps.SetupError, ps.provision_box, link_for(Silent()), "shop", "12345678", "Shop-Admin-2026", emit=lambda t: None, tries=1) is not None,
      "and the setup says so")

# ---- inputs ---------------------------------------------------------------------------------------------------------------------
check(ps.wifi_problem("Shop", "12345678") is None and ps.wifi_problem("", "12345678") and ps.wifi_problem("Shop", "1234567")
      and ps.wifi_problem("S" * 33, "12345678") and ps.wifi_problem("Shop", "x" * 64) and ps.wifi_problem("Shöp", "12345678"), "Wi-Fi name and password rules")
check(ps.admin_password_problem("Shop-Admin-2026") is None and ps.admin_password_problem("short") and ps.admin_password_problem("Coinslot@Setup")
      and ps.admin_password_problem("with space1"), "admin password rules")
check(len(ps.generate(12)) == 12 and ps.generate(12) != ps.generate(12), "a made-up password")


# ---- the whole setup with a pretend board -----------------------------------------------------------------------------------------
class Args:
    port = "/dev/ttyACM0"
    branch = "main"
    image = None
    wifi_name = "Dela Cruz 5G"
    wifi_password = "p@ss w0rd!"
    admin_password = None


def setup_world(existing=None):
    state = {"flashed": 0, "box": existing or FakeBox(), "said": []}

    def flasher(branch, port, emit, cache, image=None):
        state["flashed"] += 1
        state["box"] = FakeBox()   # (writing the software erases everything: a new box)
        return "ESP32-C3", "1.0.0"

    def opener(device):
        return link_for(state["box"])

    def waiter(find_device, name, emit):
        state["box"].joins = True
        status = link_for(state["box"]).hello(3)
        assert status["wifi"] == "up"
        return status
    return state, flasher, opener, waiter


state, flasher, opener, waiter = setup_world(existing=FakeBox())   # a blank board that answers
out = os.path.join(TMP, "out")
said = []
path = ps.run_setup(Args, read=lambda p: "", read_secret=lambda p: "", emit=said.append, finder=lambda: [("/dev/ttyACM0", "ESP32-C3")],
                    flasher=flasher, opener=opener, waiter=waiter, sleep=lambda s: None, out_dir=out)
text = open(path).read()
admin = state["box"].admin
check(state["flashed"] == 1 and state["box"].wifi == ("Dela Cruz 5G", "p@ss w0rd!"), "the setup writes the software, then the Wi-Fi")
check(len(admin) == 12 and admin in text and "http://192.168.1.77/" in text and "aa:bb:cc:00:11:22" in text and "Dela Cruz 5G" in text,
      "the saved sheet has the address, the box code and the new admin password")
check(stat.S_IMODE(os.stat(path).st_mode) == 0o600 or os.name == "nt", "and is readable only by the owner")
check(any("http://192.168.1.77/" in s for s in said) and any("Box code" in s for s in said), "the owner is told the address and the box code")

# a box that is already working is not erased by accident
working = FakeBox(setup_done=True)
state, flasher, opener, waiter = setup_world(existing=working)
msg = raises(ps.SetupError, ps.run_setup, Args, read=lambda p: "", read_secret=lambda p: "", emit=lambda t: None, finder=lambda: [("/dev/ttyACM0", "x")],
             flasher=flasher, opener=opener, waiter=waiter, sleep=lambda s: None, out_dir=out)
check(msg and state["flashed"] == 0, "a working box is not erased unless the owner types ERASE")
state, flasher, opener, waiter = setup_world(existing=FakeBox(setup_done=True))
ps.run_setup(Args, read=lambda p: "ERASE", read_secret=lambda p: "", emit=lambda t: None, finder=lambda: [("/dev/ttyACM0", "x")],
             flasher=flasher, opener=opener, waiter=waiter, sleep=lambda s: None, out_dir=out)
check(state["flashed"] == 1, "typing ERASE goes on")

# bad Wi-Fi details stop before anything is written
class BadArgs(Args):
    wifi_password = "short"
state, flasher, opener, waiter = setup_world()
check(raises(ps.SetupError, ps.run_setup, BadArgs, read=lambda p: "", read_secret=lambda p: "", emit=lambda t: None, finder=lambda: [("/dev/ttyACM0", "x")],
             flasher=flasher, opener=opener, waiter=waiter, sleep=lambda s: None, out_dir=out) and state["flashed"] == 0, "a short Wi-Fi password stops before writing")
check(raises(ps.SetupError, ps.choose_board, [], input) and ps.choose_board([("/dev/a", "one")], input, lambda t: None) == "/dev/a"
      and ps.choose_board([("/dev/a", "one"), ("/dev/b", "two")], lambda p: "2", lambda t: None) == "/dev/b", "no board / one board / pick one")

# waiting for the Wi-Fi
clock = [0.0]
class NeverJoins(FakeBox):
    pass
nb = NeverJoins(setup_done=True, joins=False)
msg = raises(ps.SetupError, ps.wait_online, lambda: "/dev/x", "Shop", lambda t: None, 20, lambda d: link_for(nb), lambda: clock[0], lambda s: clock.__setitem__(0, clock[0] + s))
check(msg and "2.4 GHz" in msg and "wrong Wi-Fi password" in msg, "a box that never joins: the usual causes are named")
check(raises(ps.SetupError, ps.wait_online, lambda: None, "Shop", lambda t: None, 6, lambda d: link_for(nb), lambda: clock[0], lambda s: clock.__setitem__(0, clock[0] + s)),
      "a board that never comes back on USB is reported")

# ---- the software image ---------------------------------------------------------------------------------------------------------
image = os.urandom(5000)
manifest = {"version": "1.0.0", "builds": [{"env": "esp32-c3-dev", "chip": "ESP32-C3", "file": "esp32-c3-dev.bin", "offset": 0, "size": len(image), "sha256": hashlib.sha256(image).hexdigest()}]}

class Reply:
    def __init__(self, body):
        self.body = body
    def read(self):
        return self.body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

def fake_open(images):
    def op(req, timeout=0):
        url = req.full_url
        if url.endswith("manifest.json"):
            return Reply(json.dumps(manifest).encode())
        return Reply(images)
    return op

cache = os.path.join(TMP, "cache")
check(ps.flash_manifest("main", cache, fake_open(image), lambda t: None)["version"] == "1.0.0", "the software list is read")
build = ps.choose_build(manifest, "ESP32-C3")
p1 = ps.fetch_image("main", build, cache, fake_open(image))
check(open(p1, "rb").read() == image, "the image is downloaded and checked")
check(ps.fetch_image("main", build, cache, fake_open(b"")) == p1, "and kept for next time")
shutil.rmtree(cache)
check(raises(ps.SetupError, ps.fetch_image, "main", build, cache, fake_open(image[:-1])) is not None, "an image that does not match its checksum is refused")
bad = dict(build, file="../evil.bin")
check(raises(ps.SetupError, ps.fetch_image, "main", bad, cache, fake_open(image)) is not None, "a file name with a path in it is refused")
check("raw.githubusercontent.com/Sheesh0327/pisophone-box/main/release/flash/manifest.json" == ps.raw_url("main", "release/flash/manifest.json").split("https://")[1], "the images come from this repository")

class FakeTool:
    __version__ = "4.8.1"
    calls = []
    @staticmethod
    def main(argv):
        FakeTool.calls.append(argv)
        if "chip_id" in argv:
            print("Chip is ESP32-C3 (QFN32) (revision v0.4)")
        if "write_flash" in argv:
            print("Writing at 0x00000000... (50 %)")
            print("Writing at 0x00001000... (100 %)")
shutil.rmtree(cache, ignore_errors=True)
chip, version = ps.flash_box("main", "/dev/x", lambda t: None, cache, fake_open(image), tool=FakeTool)
args = FakeTool.calls[-1]
check(chip == "ESP32-C3" and version == "1.0.0" and "-e" in args and args[-2] == "0x0" and os.path.isfile(args[-1]), "the image is written from offset 0 after erasing the flash")
class FakeS3(FakeTool):
    @staticmethod
    def main(argv):
        print("Chip is ESP32-S3 (revision v0.1)")
check(raises(ps.SetupError, ps.flash_box, "main", "/dev/x", lambda t: None, cache, fake_open(image), tool=FakeS3) is not None, "another chip is refused (this is the ESP32-C3 product)")
check(ps.usb_boards(lambda: []) == [] and ps.usb_boards(lambda: [type("P", (), {"device": "/dev/ttyACM0", "vid": 0x303A, "description": "USB JTAG"})(),
                                                                  type("P", (), {"device": "/dev/ttyS0", "vid": None, "description": "x"})()]) == [("/dev/ttyACM0", "USB JTAG")],
      "only USB boards are listed")

# ---- slot license keys ----------------------------------------------------------------------------------------------------------
key = ps.create_key("owner")
box_code = "AA:BB:CC:00:11:22"
ledger = os.path.join(TMP, "licenses.csv")
made = ps.make_slot_licenses(box_code, 2, "Juan, receipt 12", ledger=ledger)
check(len(made["keys"]) == 2 and made["total"] == 2 and all(k.startswith("PSLOT1.aabbcc001122.") for k in made["keys"]), "two keys for one box")
check(all(ps.slot_license_verify(key.public_key(), k) for k in made["keys"]) and len({k.split(".")[2] for k in made["keys"]}) == 2, "each is signed and has its own number")
check(len(ps.read_license_ledger(ledger)) == 2 and ps.read_license_ledger(ledger)[0]["note"] == "Juan, receipt 12", "and both are written in the ledger")
made2 = ps.make_slot_licenses("aa-bb-cc-00-11-22", 8, ledger=ledger)
check(made2["total"] == 10, "a box can have 10 keys (12 slots less the 2 free ones)")
msg = raises(ps.ToolError, ps.make_slot_licenses, box_code, 1, ledger=ledger)
check(msg and "at most 10" in msg, "an 11th key is refused: " + str(msg))
check(ps.make_slot_licenses("AA:BB:CC:00:11:23", 1, ledger=ledger)["total"] == 1, "another box has its own count")
check(raises(ps.ToolError, ps.make_slot_licenses, "nonsense", 1, ledger=ledger) is not None and raises(ps.ToolError, ps.make_slot_licenses, box_code, 0, ledger=ledger) is not None,
      "a bad box code or count is refused")
re_issued = ps.reissue_slot_licenses(box_code, ledger=ledger)
sold = {r["key"] for r in ps.read_license_ledger(ledger) if r["box"] == "aabbcc001122"}
check(len(re_issued) == 10 and {k.split(".")[2] for k in re_issued} == sold and all(ps.slot_license_verify(key.public_key(), k) for k in re_issued),
      "re-issuing gives back the keys already sold (the same numbers, never more)")
check(raises(ps.ToolError, ps.reissue_slot_licenses, "AA:BB:CC:00:99:99", ledger=ledger) is not None, "nothing to re-issue for an unknown box")
check(not ps.slot_license_verify(key.public_key(), made["keys"][0].replace("aabbcc001122", "aabbcc001123")), "a key does not verify for another box code")

# the keys against the firmware's own code (SlotLicense.h), when a C++ compiler and mbedtls are there
gxx = shutil.which("g++")
if gxx:
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    src = os.path.join(TMP, "x.cpp")
    other = ps.make_slot_licenses("AA:BB:CC:00:11:30", 1, ledger=os.path.join(TMP, "other.csv"))["keys"][0]
    with open(src, "w") as f:
        f.write('#include "%s"\n#include <cstdio>\nstatic const unsigned char PUB[] = {%s};\nint main(){ std::string id; int bad = 0;\n'
                % (os.path.join(ROOT, "esp32_firmware", "include", "SlotLicense.h"), ", ".join(str(b) for b in der)))
        for k in made["keys"]:
            f.write('  if (slotlicense::verifyToken("%s", "AA:BB:CC:00:11:22", PUB, sizeof(PUB), "", id) != slotlicense::Check::Ok) { puts("not accepted"); bad++; }\n' % k)
        f.write('  if (slotlicense::verifyToken("%s", "AA:BB:CC:00:11:22", PUB, sizeof(PUB), "", id) != slotlicense::Check::WrongBox) { puts("other box accepted"); bad++; }\n' % other)
        f.write('  return bad; }\n')
    exe = os.path.join(TMP, "x")
    build_run = subprocess.run([gxx, "-std=c++17", "-include", "limits.h", "-include", "sys/param.h", "-o", exe, src, "-lmbedcrypto"], capture_output=True, text=True)
    if build_run.returncode == 0:
        r = subprocess.run([exe], capture_output=True, text=True)
        check(r.returncode == 0, "the firmware accepts the keys this program makes: " + r.stdout)
    else:
        print("note: the firmware cross-check was skipped (mbedtls is not installed)")

# ---- time cards and the other owner tools ----------------------------------------------------------------------------------------
ps.create_key("card")
ps.create_key("admin")
cards = ps.make_cards(box_code, 3, 1.5, out=os.path.join(TMP, "cards"))
check(cards["first"] == 1 and cards["last"] == 3 and all(os.path.isfile(p) for _t, p in cards["files"]), "time cards are made")
page = open(cards["files"][1][1]).read()
check("data:image/png;base64," in page and "class=\"back\"" in page, "the card backs carry the emblem")
check(ps.make_cards(box_code, 2, 1, out=os.path.join(TMP, "cards"))["first"] == 4, "card numbers continue from the ledger")
qr = ps.make_admin_qr()
check(os.path.isfile(qr["path"]) and "<svg" in qr["svg"], "the admin QR code is made")
rec = ps.make_recovery_qr(5)
check(os.path.isfile(rec["path"]) and raises(ps.ToolError, ps.make_recovery_qr, 0) is not None, "the recovery code is made, and 0 minutes is refused")
check(ps.terminal_qr("http://192.168.1.77/", tty=True).count("\n") > 10 and ps.terminal_qr("x", tty=False) == "", "the address as a QR code in the terminal")

# the menu: slot license keys through the owner tools
inputs = iter(["1", "AA:BB:CC:00:11:50", "2", "Maria", "0"])
said = []
ps.tools_menu(read=lambda p: next(inputs), emit=said.append)
check(sum(1 for s in said if s.startswith("PSLOT1.aabbcc001150.")) == 2 and any("P500" in s for s in said), "the owner tools menu makes slot license keys and says the price")
check(ps.main(["licenses"]) == 1, "licenses without a box says what is missing")
check(ps.main(["licenses", "--box", "AA:BB:CC:00:11:60", "--count", "1"]) == 0, "the command line makes a key")

shutil.rmtree(TMP, ignore_errors=True)
print(f"{checks} checks, {failures} failures")
sys.exit(1 if failures else 0)
