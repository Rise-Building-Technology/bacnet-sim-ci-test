#!/usr/bin/env python3
"""BACnet protocol test client.

Sends real BACnet/IP messages (Who-Is, ReadProperty, WriteProperty) to the
simulator using BAC0 as a BACnet client.  Validates that the simulator speaks
correct BACnet/IP protocol on the wire.

Usage (inside Docker on bacnet-test-net):
    python test_bacnet_protocol.py [--device-ip 172.20.0.10] [--client-ip 172.20.0.100/24]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import BAC0

# ---------------------------------------------------------------------------
# Defaults — overridable via CLI args
# ---------------------------------------------------------------------------
DEFAULT_DEVICE_IP = "172.20.0.10"
DEFAULT_CLIENT_IP = "172.20.0.100/24"
BACNET_PORT = 47808
CLIENT_DEVICE_ID = 999  # arbitrary, must differ from simulator

# Expected defaults from bacnet_sim.defaults.default_config()
DEVICE_ID = 1001
DEVICE_NAME = "HVAC Controller"

# BAC0 factory auto-assigns 0-based instance numbers per object type.
# The defaults.py creates objects in this order, so instances are:
#   analogInput:  0=Zone Temp (72.5),  1=Supply Air Temp (55.0)
#   analogOutput: 0=Zone Setpoint (72.0),  1=Damper Position (50.0)
#   binaryInput:  0=Fan Status (active)
#   binaryOutput: 0=Fan Command (inactive)
#   multiStateValue: 0=Occupancy Mode (1)
#   characterstringValue: 0=Device Status ("Normal")

# ---------------------------------------------------------------------------
# Test bookkeeping
# ---------------------------------------------------------------------------
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS: {name}")
    else:
        _failed += 1
        print(f"  FAIL: {name} -- {detail}")


# ---------------------------------------------------------------------------
# Individual test sections
# ---------------------------------------------------------------------------

async def test_whois(bacnet: BAC0.lite, device_ip: str) -> None:
    """Who-Is broadcast discovery with retries."""
    print("\n--- Test: Who-Is Discovery ---")
    devices = None
    for attempt in range(1, 4):
        # Try discover() first (higher-level), fall back to who_is()
        try:
            await bacnet.discover()
        except Exception:
            await bacnet.who_is()
        await asyncio.sleep(3)

        # Check discoveredDevices attribute
        devices = bacnet.discoveredDevices
        if devices:
            break

        # Also try _devices() which may return discovered devices
        try:
            dev_list = await bacnet._devices(_return_list=True)
            if dev_list:
                devices = dev_list
                break
        except Exception:
            pass

        print(f"  (retry Who-Is {attempt}/3)")

    # If broadcast discovery didn't work, try a targeted Who-Is
    if not devices:
        print("  (trying targeted Who-Is to device IP)")
        try:
            await bacnet.who_is(f"{device_ip}")
            await asyncio.sleep(3)
            devices = bacnet.discoveredDevices
        except Exception as e:
            print(f"  (targeted Who-Is failed: {e})")

    check("Who-Is received response(s)", bool(devices),
          "discoveredDevices is empty after all attempts")

    if devices:
        found = str(DEVICE_ID) in str(devices)
        check(f"Device {DEVICE_ID} discovered via Who-Is", found,
              f"discoveredDevices={devices}")


async def test_read_object_list(bacnet: BAC0.lite, device_ip: str) -> None:
    """Read the device's object list for debugging."""
    print("\n--- Debug: Device Object List ---")
    try:
        obj_list = await bacnet.read(f"{device_ip} device {DEVICE_ID} objectList")
        print(f"  Objects: {obj_list}")
    except Exception as e:
        print(f"  (could not read objectList: {e})")


async def test_read_analog_inputs(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadProperty for analog-input objects."""
    print("\n--- Test: ReadProperty - analogInput 0 (Zone Temp) ---")
    try:
        val = await bacnet.read(f"{device_ip} analogInput 0 presentValue")
        check("Read Zone Temp presentValue", val is not None, "returned None")
        check("Zone Temp = 72.5", abs(float(val) - 72.5) < 0.1, f"got {val}")
    except Exception as e:
        check("Read analogInput 0", False, str(e))

    print("\n--- Test: ReadProperty - analogInput 1 (Supply Air Temp) ---")
    try:
        val = await bacnet.read(f"{device_ip} analogInput 1 presentValue")
        check("Supply Air Temp = 55.0", abs(float(val) - 55.0) < 0.1, f"got {val}")
    except Exception as e:
        check("Read analogInput 1", False, str(e))


async def test_read_analog_outputs(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadProperty for analog-output objects."""
    print("\n--- Test: ReadProperty - analogOutput 0 (Zone Setpoint) ---")
    try:
        val = await bacnet.read(f"{device_ip} analogOutput 0 presentValue")
        check("Zone Setpoint = 72.0", abs(float(val) - 72.0) < 0.1, f"got {val}")
    except Exception as e:
        check("Read analogOutput 0", False, str(e))

    print("\n--- Test: ReadProperty - analogOutput 1 (Damper Position) ---")
    try:
        val = await bacnet.read(f"{device_ip} analogOutput 1 presentValue")
        check("Damper Position = 50.0", abs(float(val) - 50.0) < 0.1, f"got {val}")
    except Exception as e:
        check("Read analogOutput 1", False, str(e))


async def test_read_binary_input(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadProperty for binary-input objects."""
    print("\n--- Test: ReadProperty - binaryInput 0 (Fan Status) ---")
    try:
        val = await bacnet.read(f"{device_ip} binaryInput 0 presentValue")
        # BAC0 may return "active"/"inactive", True/False, or 1/0
        check("Fan Status is active/true",
              str(val).lower() in ("active", "true", "1"),
              f"got {val}")
    except Exception as e:
        check("Read binaryInput 0", False, str(e))


async def test_read_binary_output(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadProperty for binary-output objects."""
    print("\n--- Test: ReadProperty - binaryOutput 0 (Fan Command) ---")
    try:
        val = await bacnet.read(f"{device_ip} binaryOutput 0 presentValue")
        check("Fan Command is inactive/false",
              str(val).lower() in ("inactive", "false", "0"),
              f"got {val}")
    except Exception as e:
        check("Read binaryOutput 0", False, str(e))


async def test_read_multistate_value(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadProperty for multistate-value objects."""
    print("\n--- Test: ReadProperty - multiStateValue 0 (Occupancy Mode) ---")
    try:
        val = await bacnet.read(f"{device_ip} multiStateValue 0 presentValue")
        check("Occupancy Mode = 1", int(val) == 1, f"got {val}")
    except Exception as e:
        check("Read multiStateValue 0", False, str(e))


async def test_read_character_string(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadProperty for character-string-value objects."""
    print("\n--- Test: ReadProperty - characterstringValue 0 (Device Status) ---")
    try:
        val = await bacnet.read(f"{device_ip} characterstringValue 0 presentValue")
        check('Device Status = "Normal"', str(val) == "Normal", f"got {val}")
    except Exception as e:
        check("Read characterstringValue 0", False, str(e))


async def test_read_device_object(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadProperty on the device object itself."""
    print("\n--- Test: ReadProperty - device objectName ---")
    try:
        val = await bacnet.read(f"{device_ip} device {DEVICE_ID} objectName")
        check(f'Device objectName = "{DEVICE_NAME}"',
              str(val) == DEVICE_NAME, f"got {val}")
    except Exception as e:
        check("Read device objectName", False, str(e))


async def test_write_and_readback(bacnet: BAC0.lite, device_ip: str) -> None:
    """WriteProperty then ReadProperty to verify round-trip."""
    print("\n--- Test: WriteProperty + ReadBack (analogOutput 0 = Zone Setpoint) ---")
    try:
        await bacnet._write(f"{device_ip} analogOutput 0 presentValue 68.5 - 8")
        await asyncio.sleep(1)
        val = await bacnet.read(f"{device_ip} analogOutput 0 presentValue")
        check("Write 68.5 then read-back", abs(float(val) - 68.5) < 0.1,
              f"got {val}")
    except Exception as e:
        check("WriteProperty analogOutput 0", False, str(e))


async def test_read_property_multiple(bacnet: BAC0.lite, device_ip: str) -> None:
    """ReadPropertyMultiple — fetch several properties in one request."""
    print("\n--- Test: ReadPropertyMultiple - analogInput 0 (presentValue + objectName) ---")
    try:
        val = await bacnet.readMultiple(
            f"{device_ip} analogInput 0 presentValue objectName"
        )
        # readMultiple returns a list of (value, property) tuples or similar
        print(f"  readMultiple result: {val}")
        check("ReadPropertyMultiple returned data", val is not None and len(val) > 0,
              f"got {val}")
        # Extract values — format varies by BAC0 version
        values = [v[0] if isinstance(v, tuple) else v for v in val]
        pv_found = any(abs(float(v) - 72.5) < 0.1
                       for v in values if _is_numeric(v))
        name_found = any("Zone Temp" in str(v) for v in values)
        check("RPM contains presentValue 72.5", pv_found, f"values={values}")
        check("RPM contains objectName Zone Temp", name_found, f"values={values}")
    except Exception as e:
        check("ReadPropertyMultiple analogInput 0", False, str(e))


async def test_write_property_multiple(bacnet: BAC0.lite, device_ip: str) -> None:
    """WritePropertyMultiple — write to two objects in one request, then verify."""
    print("\n--- Test: WritePropertyMultiple - analogOutput 0 + analogOutput 1 ---")
    args = [
        "analogOutput 0 presentValue 70.0 - 8",
        "analogOutput 1 presentValue 45.0 - 8",
    ]
    # Try available WPM methods — name varies across BAC0 versions
    wpm_fn = (getattr(bacnet, "writeMultiple", None)
              or getattr(bacnet, "_writeMultiple", None))
    if wpm_fn is None:
        print("  SKIP: WritePropertyMultiple not available in BAC0 Lite mode")
        return
    try:
        result = wpm_fn(addr=device_ip, args=args)
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(1)

        val0 = await bacnet.read(f"{device_ip} analogOutput 0 presentValue")
        check("WPM: analogOutput 0 = 70.0", abs(float(val0) - 70.0) < 0.1,
              f"got {val0}")

        val1 = await bacnet.read(f"{device_ip} analogOutput 1 presentValue")
        check("WPM: analogOutput 1 = 45.0", abs(float(val1) - 45.0) < 0.1,
              f"got {val1}")
    except Exception as e:
        check("WritePropertyMultiple", False, str(e))


def _is_numeric(v: object) -> bool:
    """Check if a value can be converted to float."""
    try:
        float(v)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def async_main(device_ip: str, client_ip: str) -> None:
    print("=" * 60)
    print("BACnet Protocol Test Client")
    print(f"  Simulator : {device_ip}:{BACNET_PORT}")
    print(f"  Client    : {client_ip}")
    print("=" * 60)

    # ---- Initialise BAC0 as a BACnet/IP client ----
    print(f"\n--- Initializing BAC0 client on {client_ip} ---")
    async with BAC0.lite(ip=client_ip, port=BACNET_PORT, deviceId=CLIENT_DEVICE_ID) as bacnet:
        # Allow BACnet stack to settle
        await asyncio.sleep(2)

        await test_whois(bacnet, device_ip)
        await test_read_object_list(bacnet, device_ip)
        await test_read_analog_inputs(bacnet, device_ip)
        await test_read_analog_outputs(bacnet, device_ip)
        await test_read_binary_input(bacnet, device_ip)
        await test_read_binary_output(bacnet, device_ip)
        await test_read_multistate_value(bacnet, device_ip)
        await test_read_character_string(bacnet, device_ip)
        await test_read_device_object(bacnet, device_ip)
        await test_read_property_multiple(bacnet, device_ip)
        await test_write_and_readback(bacnet, device_ip)
        await test_write_property_multiple(bacnet, device_ip)

    # ---- Summary ----
    total = _passed + _failed
    print("\n" + "=" * 60)
    print(f"Results: {_passed}/{total} passed, {_failed}/{total} failed")
    print("=" * 60)

    if _failed > 0:
        sys.exit(1)
    print("\nAll BACnet protocol tests passed!")


def main() -> None:
    parser = argparse.ArgumentParser(description="BACnet protocol test client")
    parser.add_argument("--device-ip", default=DEFAULT_DEVICE_IP,
                        help="Simulator device IP (default: %(default)s)")
    parser.add_argument("--client-ip", default=DEFAULT_CLIENT_IP,
                        help="Client IP/mask (default: %(default)s)")
    args = parser.parse_args()
    asyncio.run(async_main(args.device_ip, args.client_ip))


if __name__ == "__main__":
    main()
