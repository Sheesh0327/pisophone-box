#!/bin/sh
# Repository security rules, run by CI (.github/workflows/quality.yml). Exit 1 on any violation.
# Run it locally from the repository root: sh scripts/check_security_rules.sh
cd "$(dirname "$0")/.." || exit 1
fail=0
bad() { echo "RULE VIOLATION: $1"; fail=1; }

CODE_DIRS="esp32_firmware release scripts setup protocol tests .github"
# ($EXCLUDE is split into words on purpose wherever it is used.)
EXCLUDE='--exclude-dir=.git --exclude-dir=build --exclude-dir=target --exclude-dir=node_modules --exclude-dir=__pycache__ --exclude=check_security_rules.sh'

# 1. No private keys anywhere in the repository (the signing keys live offline). Binary files are skipped (-I): a firmware image
# contains the mbedTLS parser's own text "-----BEGIN EC PRIVATE KEY-----", which is not a key.
if grep -rEIl $EXCLUDE -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' . ; then
    bad "a private key is committed (files listed above)"
fi

# 3. No factory passwords in code: every box and phone generates its own.
if grep -rnE $EXCLUDE -e 'Admin@123|AdminSetup|superadmin123' $CODE_DIRS; then
    bad "a factory password is hard-coded (lines above)"
fi

# 4. TLS verification may only be skipped where the payload is signed, and the line must say so.
if grep -rn $EXCLUDE 'setInsecure' $CODE_DIRS | grep -v 'piso-allow-insecure'; then
    bad "setInsecure() without a 'piso-allow-insecure: <reason>' marker on the same line"
fi

# 5. The box serves its coin-slot API (arm, unarm, ack, status) to signed calls only: an unsigned ack could clear a phone's
#    paid coins. The transition switch must stay on in the source (a build for old phones may turn it off explicitly).
if ! grep -q '^#define PISO_REQUIRE_SIGNED_COINSLOT 1$' esp32_firmware/src/WebServerCoinslot.cpp; then
    bad "the coin-slot API no longer requires signed calls by default (PISO_REQUIRE_SIGNED_COINSLOT)"
fi

# 6. A slot is opened only by a license key the owner signed for this box. The pairing limit must come from the licenses, and
#    no handler may open a slot without checking a key (SlotLicense.h).
if grep -rn 'addLicenseKey' esp32_firmware/src | grep -v 'esp32_firmware/src/Config.cpp' | grep -v 'WebServerApi.cpp'; then
    bad "addLicenseKey is called outside the license handler"
fi
if ! grep -q 'slotlicense::verifyToken' esp32_firmware/src/WebServerApi.cpp; then
    bad "the license handler no longer verifies the key's signature (slotlicense::verifyToken)"
fi

# 7. The USB setup command must stay locked to a box that has not been set up.
if ! grep -q 'if (adminPwChanged)' esp32_firmware/src/SerialCli.cpp; then
    bad "PISO-SETUP no longer refuses a box that was set up already"
fi

# 8. Source folders hold source: nothing but code may sit next to the C++ files, and no file over 5 MB may be tracked outside the
#    folder CI publishes builds into (release/).
bad_files=$(git ls-files esp32_firmware/src esp32_firmware/include 2>/dev/null | grep -vE '\.(kt|java|cpp|h|hpp|c|ino)$')
if [ -n "$bad_files" ]; then
    echo "$bad_files"
    bad "files that are not source sit in a source folder (listed above)"
fi
for f in $(git ls-files); do
    case "$f" in website/*|tools/pisoportal/bin/*|setup/piso-setup*) continue ;; esac
    [ -f "$f" ] || continue
    if [ "$(wc -c < "$f")" -gt 5000000 ]; then
        bad "$f is over 5 MB; build outputs and packages do not belong in the repository"
    fi
done

[ "$fail" -eq 0 ] && echo "security rules: all passed"
exit "$fail"
