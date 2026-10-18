#ifndef OWNER_DATA_H
#define OWNER_DATA_H

// What an operator's factory reset (dashboard button or the physical reset pin) must NOT erase:
// the lifetime revenue counters, the vendor revenue split and the used slot-license keys (SlotLicense.h). Only the owner, signed in with the admin QR code, wipes those
// too (/api/superadmin/factory_reset).
// Pure C++ (no Arduino), tested on the host.
//
// `Store` is any class with: isKey, getInt, putInt, getUInt, putUInt, getString, putString.

#include <stdint.h>
#include <string>

namespace ownerdata {

static const char* const K_TOTAL_COINS = "total_coins";
static const char* const K_TOTAL_CENTAVOS = "earn_c";
static const char* const K_VENDOR_SPLIT = "vendor_split";
static const char* const K_LICENSES = "lic_ids";

struct Snapshot {
    bool hasCoins = false, hasCentavos = false, hasSplit = false, hasLicenses = false;
    int32_t split = 0;
    std::string licenses;
    uint32_t coins = 0, centavos = 0;
};

template <class Store> Snapshot capture(Store& s) {
    Snapshot o;
    if ((o.hasCoins = s.isKey(K_TOTAL_COINS))) o.coins = s.getUInt(K_TOTAL_COINS, 0);
    if ((o.hasCentavos = s.isKey(K_TOTAL_CENTAVOS))) o.centavos = s.getUInt(K_TOTAL_CENTAVOS, 0);
    if ((o.hasSplit = s.isKey(K_VENDOR_SPLIT))) o.split = s.getInt(K_VENDOR_SPLIT, 0);
    if ((o.hasLicenses = s.isKey(K_LICENSES))) o.licenses = s.getString(K_LICENSES, "");
    return o;
}

template <class Store> void restore(Store& s, const Snapshot& o) {
    if (o.hasCoins) s.putUInt(K_TOTAL_COINS, o.coins);
    if (o.hasCentavos) s.putUInt(K_TOTAL_CENTAVOS, o.centavos);
    if (o.hasSplit) s.putInt(K_VENDOR_SPLIT, o.split);
    if (o.hasLicenses) s.putString(K_LICENSES, o.licenses);
}

} // namespace ownerdata

#endif
