#ifndef FIRMWARE_VERSION_H
#define FIRMWARE_VERSION_H

// Bump this when releasing a new firmware.bin. scripts/publish_firmware_ota.py publishes the same
// string in firmware.json, and the dashboard compares the two to decide whether an update exists.
#define PISO_FW_VERSION "1.0.0"

#endif // FIRMWARE_VERSION_H
