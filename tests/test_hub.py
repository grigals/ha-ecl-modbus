"""Tests for the low-level Modbus hub: decoding, encoding, reconnects and errors."""

from __future__ import annotations

import logging
import math

import pytest
from pymodbus.exceptions import ModbusException, ModbusIOException

from custom_components.ecl_modbus.modbus import EclCommunicationError, EclModbusHub

from .conftest import float_words, string_words


def _tcp_hub(**kwargs) -> EclModbusHub:
    return EclModbusHub(
        transport="tcp", slave_id=kwargs.pop("slave_id", 5), host="10.0.0.5", tcp_port=502
    )


def _serial_hub() -> EclModbusHub:
    return EclModbusHub(transport="serial", slave_id=7, port="/dev/ttyUSB0", baudrate=19200)


# --- Connection setup --------------------------------------------------------


def test_tcp_client_settings(fake_client) -> None:
    hub = _tcp_hub()
    hub.read_int16(100)
    client = fake_client.instances[0]
    assert client.kwargs["host"] == "10.0.0.5"
    assert client.kwargs["port"] == 502
    assert client.device_ids == {5}


def test_serial_client_settings(fake_client) -> None:
    """Serial uses the ECL's fixed framing: 8 data bits, even parity, 1 stop bit."""
    hub = _serial_hub()
    hub.read_int16(100)
    client = fake_client.instances[0]
    assert client.kwargs["port"] == "/dev/ttyUSB0"
    assert client.kwargs["baudrate"] == 19200
    assert (client.kwargs["bytesize"], client.kwargs["parity"], client.kwargs["stopbits"]) == (
        8,
        "E",
        1,
    )
    assert client.device_ids == {7}


def test_serial_default_baudrate(fake_client) -> None:
    EclModbusHub(transport="serial", slave_id=5, port="/dev/ttyUSB0").read_int16(1)
    assert fake_client.instances[0].kwargs["baudrate"] == 38400


@pytest.mark.parametrize(
    "hub",
    [
        EclModbusHub(transport="tcp", slave_id=5, host=None),
        EclModbusHub(transport="serial", slave_id=5, port=None),
    ],
    ids=["tcp-no-host", "serial-no-port"],
)
def test_missing_connection_details(fake_client, hub: EclModbusHub) -> None:
    with pytest.raises(EclCommunicationError):
        hub.read_int16(1)
    assert fake_client.instances == []


def test_addresses_are_one_based(fake_client) -> None:
    """Danfoss docs number registers from 1; pymodbus addresses from 0."""
    hub = _tcp_hub()
    hub.read_int16(21000)
    hub.write_int16(21000, 1)
    client = fake_client.instances[0]
    # The fake converts back to 1-based, so a correct hub records the manual address.
    assert client.reads == [21000]
    assert client.writes == [(21000, [1])]


def test_connection_is_reused(fake_client) -> None:
    hub = _tcp_hub()
    for _ in range(5):
        hub.read_int16(1)
    assert len(fake_client.instances) == 1


def test_close_and_reopen(fake_client) -> None:
    hub = _tcp_hub()
    hub.read_int16(1)
    hub.close()
    assert fake_client.instances[0].connected is False
    hub.read_int16(1)
    assert len(fake_client.instances) == 2


def test_close_without_connection_is_safe(fake_client) -> None:
    _tcp_hub().close()


# --- Decoding -----------------------------------------------------------------


@pytest.mark.parametrize("value", [21.5, -12.25, 0.0, 85.0, 1234.5678])
def test_read_float(fake_client, value: float) -> None:
    fake_client.set_words(4000, float_words(value))
    assert _tcp_hub().read_float(4000) == pytest.approx(value, rel=1e-6)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_read_non_finite_float_is_none(fake_client, value: float) -> None:
    """Home Assistant rejects NaN/inf as a numeric state, so they read as unknown."""
    fake_client.set_words(4000, float_words(value))
    assert _tcp_hub().read_float(4000) is None


@pytest.mark.parametrize(
    ("word", "signed", "expected"),
    [
        (0, True, 0),
        (0x7FFF, True, 32767),
        (0x8000, True, -32768),
        (0xFFFF, True, -1),
        (0xFFFF, False, 65535),
        (0x8000, False, 32768),
    ],
)
def test_read_int16(fake_client, word: int, signed: bool, expected: int) -> None:
    fake_client.values[100] = word
    assert _tcp_hub().read_int16(100, signed=signed) == expected


@pytest.mark.parametrize(
    ("words", "expected"),
    [([0, 0], 0), ([0, 1], 1), ([1, 0], 65536), ([0xFFFF, 0xFFFF], 0xFFFFFFFF)],
)
def test_read_uint32(fake_client, words: list[int], expected: int) -> None:
    fake_client.set_words(500, words)
    assert _tcp_hub().read_uint32(500) == expected


def test_read_string(fake_client) -> None:
    fake_client.set_words(2100, string_words("192.168.1.20", 8))
    assert _tcp_hub().read_string(2100, reg_count=8) == "192.168.1.20"


def test_read_string_strips_padding(fake_client) -> None:
    fake_client.set_words(1, string_words("  087H3040 ", 8))
    assert _tcp_hub().read_string(1, reg_count=8) == "087H3040"


def test_read_empty_string_is_none(fake_client) -> None:
    fake_client.set_words(1, [0] * 8)
    assert _tcp_hub().read_string(1, reg_count=8) is None


def test_read_string_ignores_non_ascii(fake_client) -> None:
    fake_client.set_words(1, [ord("A") << 8 | 0xFF, ord("B") << 8] + [0] * 6)
    assert _tcp_hub().read_string(1, reg_count=8) == "AB"


# --- Encoding -----------------------------------------------------------------


def test_write_int16_masks_negative(fake_client) -> None:
    hub = _tcp_hub()
    hub.write_int16(21000, -1)
    assert fake_client.instances[0].writes == [(21000, [0xFFFF])]


@pytest.mark.parametrize("value", [20.0, -5.5, 0.1, 65.0])
def test_write_float_round_trips(fake_client, value: float) -> None:
    hub = _tcp_hub()
    hub.write_float(21202, value)
    address, words = fake_client.instances[0].writes[0]
    assert address == 21202
    assert words == float_words(value)

    fake_client.set_words(21202, words)
    assert hub.read_float(21202) == pytest.approx(value, rel=1e-6)


@pytest.mark.parametrize("value", [0, 1, 3600, 65536, 864000, 0xFFFFFFFF])
def test_write_uint32_round_trips(fake_client, value: int) -> None:
    hub = _tcp_hub()
    hub.write_uint32(21438, value)
    address, words = fake_client.instances[0].writes[0]
    assert address == 21438
    fake_client.set_words(21438, words)
    assert hub.read_uint32(21438) == value


@pytest.mark.parametrize("value", [-1, 0x1_0000_0000])
def test_write_uint32_out_of_range(fake_client, value: int) -> None:
    with pytest.raises(ValueError):
        _tcp_hub().write_uint32(21438, value)
    assert fake_client.instances == []


# --- Errors and recovery ------------------------------------------------------


def test_no_response_retries_once_on_a_new_connection(fake_client) -> None:
    fake_client.io_errors = 1
    fake_client.values[100] = 42
    assert _tcp_hub().read_int16(100) == 42
    assert len(fake_client.instances) == 2
    assert fake_client.instances[0].closed


def test_no_response_twice_raises(fake_client) -> None:
    fake_client.io_errors = 2
    with pytest.raises(EclCommunicationError):
        _tcp_hub().read_int16(100)


def test_unreachable_raises_communication_error(fake_client) -> None:
    fake_client.reachable = False
    with pytest.raises(EclCommunicationError):
        _tcp_hub().read_float(4000)


def test_recovers_after_outage(fake_client) -> None:
    hub = _tcp_hub()
    fake_client.reachable = False
    with pytest.raises(EclCommunicationError):
        hub.read_int16(1)
    fake_client.reachable = True
    fake_client.values[1] = 9
    assert hub.read_int16(1) == 9


def test_rejected_register_returns_none_and_warns_once(fake_client, caplog) -> None:
    fake_client.rejected_addresses = {21500}
    hub = _tcp_hub()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert hub.read_float(21500) is None
            assert hub.read_int16(21500) is None
            assert hub.read_uint32(21500) is None
            assert hub.read_string(21500, 8) is None
    assert caplog.text.count("rejected register 21500") == 1
    # A rejected register is the controller answering: no reconnect.
    assert len(fake_client.instances) == 1


def test_write_error_response_raises_and_reconnects(fake_client) -> None:
    fake_client.write_error = True
    hub = _tcp_hub()
    with pytest.raises(ModbusIOException):
        hub.write_int16(21000, 1)
    assert fake_client.instances[0].connected is False

    fake_client.write_error = False
    hub.write_int16(21000, 1)
    assert len(fake_client.instances) == 2


def test_write_exception_propagates(fake_client) -> None:
    fake_client.write_raises = True
    with pytest.raises(ModbusException):
        _tcp_hub().write_float(21202, 20.0)


def test_write_when_unreachable_raises_modbus_error(fake_client) -> None:
    """Platforms only catch ModbusException/OSError, so writes must raise one of those."""
    fake_client.reachable = False
    with pytest.raises((ModbusException, OSError)):
        _tcp_hub().write_int16(21000, 1)
