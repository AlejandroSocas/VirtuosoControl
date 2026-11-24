"""Hardware constants, USB IDs, EQ bands/presets, and Bragi/V2W protocol opcodes."""

VENDOR_ID = 0x1b1c

PRODUCT_IDS = {
    # Wireless Dongles
    0x0a3e: "Wireless",  # Virtuoso SE Wireless
    0x0a3f: "Wireless",  # Virtuoso RGB Wireless
    0x0a42: "Wireless",  # Virtuoso Black Wireless (Slipstream)
    0x0a46: "Wireless",  # Virtuoso White Wireless
    0x0a4a: "Wireless",  # Virtuoso SE v2 Wireless
    0x0a55: "Wireless",  # Virtuoso XT Wireless
    0x0a63: "Wireless",  # Virtuoso High-Fidelity
    0x0a64: "Wireless",  # Virtuoso Pearl Wireless

    # Wired Connections / Direct USB headsets
    0x0a3d: "Wired",     # Virtuoso SE
    0x0a41: "Wired",     # Virtuoso Black
    0x0a45: "Wired",     # Virtuoso White
    0x0a49: "Wired",     # Virtuoso SE v2
    0x0a54: "Wired",     # Virtuoso XT
    0x0a62: "Wired",     # Virtuoso Pearl
}

DEVICE_MODELS = {
    0x0a3d: "Corsair Virtuoso SE (Wired)",
    0x0a3e: "Corsair Virtuoso SE (Wireless)",
    0x0a3f: "Corsair Virtuoso RGB (Wireless)",
    0x0a41: "Corsair Virtuoso RGB (Wired)",
    0x0a42: "Corsair Virtuoso RGB (Wireless Dongle)",
    0x0a45: "Corsair Virtuoso White (Wired)",
    0x0a46: "Corsair Virtuoso White (Wireless)",
    0x0a49: "Corsair Virtuoso SE v2 (Wired)",
    0x0a4a: "Corsair Virtuoso SE v2 (Wireless Dongle)",
    0x0a54: "Corsair Virtuoso XT (Wired)",
    0x0a55: "Corsair Virtuoso XT (Wireless)",
    0x0a62: "Corsair Virtuoso Pearl (Wired)",
    0x0a63: "Corsair Virtuoso High-Fidelity (Wireless)",
    0x0a64: "Corsair Virtuoso Pearl (Wireless Dongle)",
}

# Official Corsair battery status thresholds from virtuoso_black_manifest
BATTERY_THRESHOLDS = {
    "CriticalLow": 15,
    "Low": 30,
    "Medium": 50,
    "High": 97,
    "Charged": 100,
}

# 10-Band Official Equalizer presets from iCUE 5 defaultUnlimitedEqualizerPresets
EQ_FREQUENCIES = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]  # Hz

EQ_PRESETS = {
    "Pure Direct": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "Movie Theater": [-1.0, 2.0, 1.0, 1.0, 3.0, 4.0, 3.0, 1.0, 1.0, 3.0],
    "FPS Competition": [-2.0, -2.0, 1.0, 2.0, 1.0, 2.0, 4.0, 5.0, 2.0, 4.0],
    "Clear Chat": [-2.0, -1.0, 1.0, 4.0, 5.0, 4.0, 2.0, 2.0, 1.0, 1.0],
    "Bass Boost": [4.0, 6.0, 4.0, 0.0, -3.0, -3.0, -2.0, 1.0, 2.0, 2.0],
}

EQ_SINK_NAME = "effect_input.virtuoso_eq"

# V2W / Bragi Endpoints
EP_RECEIVER = 0x08  # Wireless Dongle/Receiver
EP_HEADSET = 0x09   # Auriculares

# Bragi Command IDs (verified from BragiProtocols.dll bragiprotocol::CommandId)
CMD_SET_PROP = 0x01
CMD_GET_PROP = 0x02
CMD_UNBIND_BUFFERS = 0x05
CMD_WRITE_BUF_BEGIN = 0x06
CMD_OPEN_FILE = 0x0d
CMD_PING = 0x11

# Bragi / V2W Property IDs (extracted from bragiprotocol::PropertyId)
OP_POLLING_RATE = 0x01             # uint8
OP_BRIGHTNESS = 0x02               # uint16 LE (0-1000, controls Logo LED brightness)
OP_OPERATING_MODE = 0x03           # 0x01: HW, 0x02: SW
OP_POWER_SAVING = 0x0b             # bool (0/1) - PowerSavingModeEnabled
OP_SLEEP_ENABLED = 0x0d            # bool (0/1) - AutomaticSleepEnabled (Bragi 0x0d)
OP_SLEEP_TIMEOUT_NORMAL = 0x0e     # uint32 LE ms - AutomaticSleepTimeoutNormal (Bragi 0x0e)
OP_BATTERY = 0x0f                  # uint8/uint16 (0-1000)
OP_CHARGE = 0x10                   # 1 = charging, 2 = discharging
OP_USB_VID = 0x11                  # uint16
OP_USB_PID = 0x12                  # uint16
OP_FIRMWARE_VERSION = 0x13         # uint32 firmware version
OP_BOOTLOADER_VERSION = 0x14       # uint32 bootloader version
OP_RF_POWER = 0x17                 # uint8
OP_CONNECTED_SUBDEVICES = 0x36     # uint8 bitmap
OP_SLEEP_TIMEOUT_POWERSAVE = 0x37  # uint32 LE ms (on-device sleep timer)
OP_IDLE_TIME = 0x40                # uint32
OP_SIDETONE_MUTED = 0x46           # bool (0=unmuted, 1=muted)
OP_SIDETONE_LEVEL = 0x47           # uint16 LE (0-1000)
OP_ENCRYPTION_STATUS = 0x4b        # uint8 WirelessEncryptionStatusSubdevice1
OP_BATTERY_VOLTAGE = 0x53          # uint16 mV
OP_MIC_MUTED = 0x8e                # bool (0=active, 1=muted)
OP_MIC_LEVEL = 0x8f                # uint8 (0-100)

# Notification pushed by the headset when the physical mic-mute button is
# pressed while in software mode: [0x03, 0x01, 0x02, down].
# Captured from the control interface — the button reports key DOWN (1) and
# key UP (0) roughly 100-200ms apart, it does NOT report a mute state. Act on
# the down edge only; treating the value as a state mutes on press and unmutes
# on release.
MIC_BUTTON_REPORT = (0x03, 0x01, 0x02)

# Asynchronous status notification the headset pushes when a value changes:
#   [0x03, 0x01, 0x01, opcode, 0x00, lo, hi]
# Note the payload sits one byte later than in a query reply, which is
#   [0x01, 0x01, 0x02, 0x00, lo, hi]
NOTIFY_STATUS = (0x03, 0x01, 0x01)
