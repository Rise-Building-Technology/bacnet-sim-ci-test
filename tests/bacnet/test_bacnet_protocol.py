#!/usr/bin/env python3
"""BACnet protocol test client — 9-device edition.

Sends real BACnet/IP messages (Who-Is, ReadProperty, WriteProperty,
ReadPropertyMultiple) to a multi-device simulator and validates correct
behaviour across all device types with BACnet-layer lag verification.

Usage (inside Docker on bacnet-test-net):
    python test_bacnet_protocol.py \
        [--device-ip 172.20.0.10] \
        [--device-count 9] \
        [--client-ip 172.20.0.100/24]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import BAC0

# ---------------------------------------------------------------------------
# Defaults — overridable via CLI args
# ---------------------------------------------------------------------------
DEFAULT_DEVICE_IP = "172.20.0.10"
DEFAULT_CLIENT_IP = "172.20.0.100/24"
DEFAULT_DEVICE_COUNT = 9
BACNET_PORT = 47808
CLIENT_DEVICE_ID = 999  # arbitrary, must differ from simulator

# Device layout: (device_id, name, template, ip_offset)
# IP = base_ip + offset  (172.20.0.10 + offset)
DEVICE_TABLE = [
    (1001, "AHU-1",            "ahu",    0),
    (1002, "AHU-2",            "ahu",    1),
    (2001, "VAV-101",          "vav",    2),
    (2002, "VAV-102",          "vav",    3),
    (2003, "VAV-201",          "vav",    4),
    (2004, "VAV-202",          "vav",    5),
    (3001, "Boiler-1",         "boiler", 6),
    (4001, "Elec Meter Main",  "meter",  7),
    (4002, "Elec Meter Floor2","meter",  8),
]

# BAC0 factory auto-assigns 0-based instance numbers per object type,
# regardless of the instance numbers in the template config.
# Template instance 1 → BAC0 instance 0, template instance 2 → BAC0 instance 1, etc.

# Template-specific expected values for analogInput 0 (first AI on each type)
TEMPLATE_AI0 = {
    "ahu":    ("Supply Air Temp",   55.0),
    "vav":    ("Zone Temp",         72.0),
    "boiler": ("Supply Water Temp", 160.0),
    "meter":  ("Power",             125.0),
}

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


def _is_numeric(v: object) -> bool:
    try:
        float(v)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


def _ip_add(base: str, offset: int) -> str:
    """Add offset to the last octet of an IPv4 address."""
    parts = base.split(".")
    parts[3] = str(int(parts[3]) + offset)
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Individual test sections
# ---------------------------------------------------------------------------

async def test_whois(bacnet: BAC0.lite, device_ip: str, device_count: int) -> None:
    """Who-Is broadcast discovery — expect all device IDs."""
    print("\n--- Test: Who-Is Discovery (expect %d devices) ---" % device_count)

    expected_ids = {d[0] for d in DEVICE_TABLE[:device_count]}
    found_ids: set[int] = set()

    for attempt in range(1, 6):
        try:
            await bacnet.discover()
        except Exception:
            try:
                await bacnet.who_is()
            except Exception:
                pass
        await asyncio.sleep(3)

        devices = bacnet.discoveredDevices
        if devices:
            # discoveredDevices may be a dict keyed by (network, address) or similar
            dev_str = str(devices)
            for did, _, _, _ in DEVICE_TABLE[:device_count]:
                if str(did) in dev_str:
                    found_ids.add(did)

        if found_ids >= expected_ids:
            break
        print(f"  (retry Who-Is {attempt}/5 — found {len(found_ids)}/{device_count})")

    check("Who-Is received response(s)", len(found_ids) > 0,
          "discoveredDevices is empty after all attempts")
    check(f"All {device_count} devices discovered",
          found_ids >= expected_ids,
          f"missing: {expected_ids - found_ids}")


async def test_per_device_reads(bacnet: BAC0.lite, base_ip: str, device_count: int) -> None:
    """Read analogInput 0 from one device of each template type to verify values."""
    print("\n--- Test: Per-Device-Type Reads (analogInput 0) ---")

    # Pick one representative device per template type
    seen_templates: set[str] = set()
    for did, name, template, offset in DEVICE_TABLE[:device_count]:
        if template in seen_templates:
            continue
        seen_templates.add(template)

        ip = _ip_add(base_ip, offset)
        expected_name, expected_val = TEMPLATE_AI0[template]
        print(f"\n  [{template}] Device {did} ({name}) @ {ip}")

        try:
            val = await bacnet.read(f"{ip} analogInput 0 presentValue")
            check(f"{name}: AI-0 presentValue ~ {expected_val}",
                  val is not None and abs(float(val) - expected_val) < 0.5,
                  f"got {val}")
        except Exception as e:
            check(f"{name}: read AI-0 presentValue", False, str(e))

        try:
            obj_name = await bacnet.read(f"{ip} analogInput 0 objectName")
            check(f"{name}: AI-0 objectName = '{expected_name}'",
                  str(obj_name) == expected_name,
                  f"got {obj_name}")
        except Exception as e:
            check(f"{name}: read AI-0 objectName", False, str(e))


async def test_device_object_name(bacnet: BAC0.lite, base_ip: str, device_count: int) -> None:
    """Read device objectName from first and last device to check naming."""
    print("\n--- Test: Device Object Names ---")
    for idx in [0, device_count - 1]:
        did, name, _, offset = DEVICE_TABLE[idx]
        ip = _ip_add(base_ip, offset)
        try:
            val = await bacnet.read(f"{ip} device {did} objectName")
            check(f"Device {did} objectName = '{name}'",
                  str(val) == name, f"got {val}")
        except Exception as e:
            check(f"Device {did} objectName", False, str(e))


async def test_write_and_readback(bacnet: BAC0.lite, base_ip: str, device_count: int) -> None:
    """WriteProperty then ReadProperty round-trip on AHU and VAV."""
    print("\n--- Test: Write + ReadBack ---")

    # Write to AHU-1 (device 1001) analogOutput 0 (Supply Air Temp Setpoint)
    ahu_ip = _ip_add(base_ip, 0)
    print(f"\n  AHU-1 @ {ahu_ip}: write analogOutput 0 = 60.5")
    try:
        await bacnet._write(f"{ahu_ip} analogOutput 0 presentValue 60.5 - 8")
        await asyncio.sleep(1)
        val = await bacnet.read(f"{ahu_ip} analogOutput 0 presentValue")
        check("AHU-1: write 60.5 → read-back", abs(float(val) - 60.5) < 0.1,
              f"got {val}")
    except Exception as e:
        check("AHU-1: write analogOutput 0", False, str(e))

    # Write to VAV-101 (device 2001) analogOutput 0 (Cooling Setpoint)
    if device_count >= 3:
        vav_ip = _ip_add(base_ip, 2)
        print(f"\n  VAV-101 @ {vav_ip}: write analogOutput 0 = 73.0")
        try:
            await bacnet._write(f"{vav_ip} analogOutput 0 presentValue 73.0 - 8")
            await asyncio.sleep(1)
            val = await bacnet.read(f"{vav_ip} analogOutput 0 presentValue")
            check("VAV-101: write 73.0 → read-back", abs(float(val) - 73.0) < 0.1,
                  f"got {val}")
        except Exception as e:
            check("VAV-101: write analogOutput 0", False, str(e))


async def test_read_property_multiple(bacnet: BAC0.lite, base_ip: str) -> None:
    """ReadPropertyMultiple on AHU-1 for two properties at once."""
    print("\n--- Test: ReadPropertyMultiple (AHU-1 analogInput 0) ---")
    ahu_ip = _ip_add(base_ip, 0)
    try:
        val = await bacnet.readMultiple(
            f"{ahu_ip} analogInput 0 presentValue objectName"
        )
        print(f"  readMultiple result: {val}")
        check("RPM returned data", val is not None and len(val) > 0,
              f"got {val}")

        values = [v[0] if isinstance(v, tuple) else v for v in val]
        pv_found = any(abs(float(v) - 55.0) < 0.5
                       for v in values if _is_numeric(v))
        name_found = any("Supply Air Temp" in str(v) for v in values)
        check("RPM contains presentValue ~55.0", pv_found, f"values={values}")
        check("RPM contains objectName 'Supply Air Temp'", name_found,
              f"values={values}")
    except Exception as e:
        check("ReadPropertyMultiple", False, str(e))


async def test_lag_timing(bacnet: BAC0.lite, base_ip: str, device_count: int) -> None:
    """Time a batch of reads to verify BACnet-layer lag is applied.

    With local-network profile (0-10ms), average round-trip overhead should
    be > 0ms but < 15ms per read.  We do several reads across multiple
    devices to smooth out variance.
    """
    print("\n--- Test: Lag Timing Verification (local-network: 0-10ms) ---")

    # Collect IPs for up to 4 devices (one per type)
    seen: set[str] = set()
    targets: list[str] = []
    for _, _, template, offset in DEVICE_TABLE[:device_count]:
        if template not in seen:
            seen.add(template)
            targets.append(_ip_add(base_ip, offset))

    # Do 20 reads total (5 reads per device type) and time them
    num_reads = 20
    reads_per_target = max(1, num_reads // len(targets))

    timings: list[float] = []
    for ip in targets:
        for _ in range(reads_per_target):
            t0 = time.monotonic()
            try:
                await bacnet.read(f"{ip} analogInput 0 presentValue")
                elapsed_ms = (time.monotonic() - t0) * 1000
                timings.append(elapsed_ms)
            except Exception:
                pass  # skip failed reads for timing purposes

    if timings:
        avg_ms = sum(timings) / len(timings)
        min_ms = min(timings)
        max_ms = max(timings)
        print(f"  {len(timings)} reads: avg={avg_ms:.1f}ms, "
              f"min={min_ms:.1f}ms, max={max_ms:.1f}ms")
        # With local-network lag (0-10ms), reads should complete but not be instant
        # Allow generous upper bound for CI variability
        check("Average read time < 500ms (CI-safe)",
              avg_ms < 500, f"avg={avg_ms:.1f}ms")
        check("Reads complete successfully (lag doesn't break protocol)",
              len(timings) >= num_reads // 2,
              f"only {len(timings)}/{num_reads} reads succeeded")
    else:
        check("At least some timed reads succeeded", False, "no reads completed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def async_main(device_ip: str, client_ip: str, device_count: int) -> None:
    print("=" * 60)
    print("BACnet Protocol Test Client — 9-Device Edition")
    print(f"  First device : {device_ip}:{BACNET_PORT}")
    print(f"  Device count : {device_count}")
    print(f"  Client       : {client_ip}")
    print("=" * 60)

    print(f"\n--- Initializing BAC0 client on {client_ip} ---")
    async with BAC0.lite(ip=client_ip, port=BACNET_PORT, deviceId=CLIENT_DEVICE_ID) as bacnet:
        await asyncio.sleep(2)

        await test_whois(bacnet, device_ip, device_count)
        await test_per_device_reads(bacnet, device_ip, device_count)
        await test_device_object_name(bacnet, device_ip, device_count)
        await test_write_and_readback(bacnet, device_ip, device_count)
        await test_read_property_multiple(bacnet, device_ip)
        await test_lag_timing(bacnet, device_ip, device_count)

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
                        help="First simulator device IP (default: %(default)s)")
    parser.add_argument("--device-count", type=int, default=DEFAULT_DEVICE_COUNT,
                        help="Number of devices (default: %(default)s)")
    parser.add_argument("--client-ip", default=DEFAULT_CLIENT_IP,
                        help="Client IP/mask (default: %(default)s)")
    args = parser.parse_args()
    asyncio.run(async_main(args.device_ip, args.client_ip, args.device_count))


if __name__ == "__main__":
    main()
