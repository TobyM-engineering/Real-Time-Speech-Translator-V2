"""Waveshare UPS HAT (E) fuel gauge — MCU at I2C 0x2d, register map taken
from Waveshare's own demo (UPS_HAT_E.zip/ups.py) and verified against the
live device 2026-08-28: pack 16.49 V (4S, cells 4090-4158 mV), SOC 100 %,
15.1 V PD charger on VBUS while fast-charging.

read() returns None when the gauge is unreachable (HAT absent, I2C off) —
callers hide the indicator rather than guessing.
"""
import time

_ADDR = 0x2d
_LOW_CELL_MV = 3150   # Waveshare's own low-voltage shutdown threshold


def read():
    try:
        from smbus2 import SMBus
        with SMBus(1) as bus:
            status = bus.read_i2c_block_data(_ADDR, 0x02, 1)[0]
            vbus = bus.read_i2c_block_data(_ADDR, 0x10, 6)
            batt = bus.read_i2c_block_data(_ADDR, 0x20, 12)
            cells = bus.read_i2c_block_data(_ADDR, 0x30, 8)
    except Exception:
        return None

    def u16(b, i):
        return b[i] | (b[i + 1] << 8)

    current = u16(batt, 2)
    if current > 0x7FFF:
        current -= 0xFFFF
    if status & 0x40:
        state = "fast-charging"
    elif status & 0x80:
        state = "charging"
    elif status & 0x20:
        state = "discharging"
    else:
        state = "idle"
    cell_mv = [u16(cells, i) for i in (0, 2, 4, 6)]
    return {
        "state": state,
        "percent": u16(batt, 4),
        "pack_mv": u16(batt, 0),
        "current_ma": current,
        "remaining_mah": u16(batt, 6),
        "vbus_mv": u16(vbus, 0),
        "vbus_ma": u16(vbus, 2),
        "cell_mv": cell_mv,
        "cell_min_mv": min(cell_mv),
        "low": min(cell_mv) < _LOW_CELL_MV,
        "ts": time.time(),
    }
