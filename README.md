# ECL 120/220 Modbus for Home Assistant

A custom Home Assistant integration for reading data from **Danfoss ECL 120 and ECL 220 heating controllers** using **Modbus RTU or Modbus TCP**.

This integration is created by an enthusiast (not a professional developer) and is actively evolving.
It is tested on **ECL 120**, but **ECL 220 uses the same register map**, so it *should* work identically — feedback is welcome!

---

## 🔧 Features
Current supported features:

The list of supported registers is continuously expanding.

The authoritative source for supported registers is:
- `custom_components/ecl_modbus/registers.py`

Each register definition includes:
- Modbus address
- Read / Write support
- Data type
- Unit
- Scaling
- Min / Max / Step (where applicable)

### Fully configurable
- Enable/disable each sensor individually  
- Supports **USB (Modbus RTU)** and **IP (Modbus TCP)**  
- Automatic reconnect if connection is lost  
- Friendly naming and unified device info in Home Assistant  

---

## 🚀 Installation

### Install via HACS

The easiest way to install this integration is through HACS.

Click the button below:

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=nichlasrohde&repository=ha-ecl-modbus&category=integration)

---

### Manual installation
1. Download the latest release ZIP  
2. Extract it into: /config/custom_components/ecl_modbus/
3. Restart Home Assistant  
4. Add integration via:  
**Settings → Devices & Services → Add Integration → ECL Modbus**

---

## ⚙️ Configuration

During setup you choose **how the controller is connected**:

### 🔌 Connection type

#### Serial (USB / Modbus RTU)
- Direct USB-to-RS485 adapter
- Typical port: `/dev/ttyUSB0` or `/dev/serial/by-id/...`
- Baudrate: **38400**
- Parity: **Even**
- Stop bits: **1**
- Slave ID: **5** (default)

#### 🌐 TCP (Modbus TCP via gateway)
- Uses a Modbus TCP ⇄ Modbus RTU gateway
- Typical port: **502**
- Unit ID = ECL Modbus slave address (default: **5**)

---

### Choosing registers
Every register is a normal Home Assistant entity, and only a useful core set starts enabled. Registers you don't enable are not polled.

- **Single registers:** open the device page, or **Settings → Devices & Services → Entities**, and enable or disable entities there. The Entities page can change many at once.
- **Whole groups:** open the integration's **Configure** dialog and tick or untick a group. A group is only ticked when all of its registers are enabled, and groups you don't change are left as they are.

| Group | Contents |
|-------|----------|
| Temperatures | S1–S6, outside, flow, return, room, supply, DHW |
| Status and alarms | Valve, pump, operation mode and status, heat demand, bypass, alarms |
| Boost | Boost on/off, remaining time, period, added temperature |
| Setpoints | Flow limits and references, room, summer cut-out, frost protection, bypass |
| Weather curve | Curve slope and curve points |
| PID tuning | Xp, Tn, deadband |
| Return and room limiters | Limiter influence and adaption time |
| Pump and valve exercise | After-run and exercise timing |
| Feature switches | Limiters, summer cut-out, frost protection, exercise, bypass, external override |
| Sensor health | S1–S6 OK, invalid, open circuit or short circuit |
| Device information | Serial, firmware, IP and MAC address, runtime, Modbus message counters |

Entities appear about 30 seconds after you enable them, when Home Assistant reloads the integration.

The new registers follow the **ECL 120 application A130** map. Other applications use some of these addresses differently.

### Upgrading from 2.3.0 or older
Your existing entities keep their entity IDs, names and history, and your register selection is carried over automatically. A few new registers start enabled: heat flow temperature, return temperature and the two alarms. Take a Home Assistant backup before upgrading, as with any update.

### Polling interval
The polling interval is set in the same **Configure** dialog.

No YAML required.

---

## 🧩 How it works
- Uses `pymodbus` for Modbus communication
- Supports both **Modbus RTU** and **Modbus TCP**
- One shared coordinator polls enabled registers
- Automatic reconnect on communication errors; entities show as unavailable until the connection is back
- Groups all sensors under a single device in Home Assistant

---

## 🧪 Compatibility
| Controller | Status |
|------------|--------|
| **ECL 120** | ✔ Tested |
| **ECL 220** | ⚠ Untested, but register-compatible |

---

## 🧰 Tested hardware

This integration has been built and tested with the following hardware setups:

### USB → RS485 (Modbus RTU)
- **Industrial USB to RS485 Bidirectional Converter**  
  Vendor: raspberrypi.dk  
  Link: https://raspberrypi.dk/en/product/industrial-usb-to-rs485-bidirectional-converter/

Used for direct Modbus RTU communication via USB (e.g. `/dev/ttyUSB0` or `/dev/serial/by-id/...`).

---

### Ethernet / WiFi → RS485 (Modbus TCP via gateway)
- **WaveShare Industrial Grade Serial Server RS232/485 to WiFi/Ethernet**  
  Vendor: raspberrypi.dk  
  Link: https://raspberrypi.dk/en/product/waveshare-industrial-grade-serial-server-rs232-485-to-wifi-ethernet/

Used as a **Modbus TCP ⇄ Modbus RTU gateway**.  
Configured in *Modbus TCP ↔ Modbus RTU* mode and connected to the ECL controller via RS485.

- **Hi-Flying Protoss-PE11 RS485 to Ethernet Serial Server**  
  Vendor: Hi-Flying  
  Link: http://www.hi-flying.com/pe11

Tested and confirmed working as a **Modbus TCP ⇄ Modbus RTU gateway** connected to the ECL controller via RS485.

> When using a gateway, ensure that the RS485 parameters (baudrate, parity, stop bits) match the ECL controller configuration.

---

## 🛠️ Planned Features
This integration is actively evolving. Planned and possible future improvements include:
- Additional ECL registers and writable parameters
- Improved multi-controller support
- More advanced connection diagnostics

Suggestions, missing registers, and pull requests are very welcome!

### Running the tests
The tests run on real Home Assistant with a fake Modbus client. They need Linux or WSL, and the Python version required by `pytest-homeassistant-custom-component`.

```bash
pip install -r requirements_test.txt
```

```bash
pytest
```

---

## ❤️ Contributions
This project is community-driven — PRs, testing, and feedback are highly appreciated.

GitHub:  
👉 https://github.com/nichlasrohde/ha-ecl-modbus

---

## ☕ Support the project

This project is developed and maintained in my spare time as a hobby.

If you find it useful and would like to support continued development,  
you’re very welcome to do so — completely optional.

🇩🇰 **MobilePay (Denmark):** `9316PV`
[Support via MobilePay](https://qr.mobilepay.dk/box/82f3141c-2907-485e-b47f-518f9d789297/pay-in)

Thank you for the support ❤️

---

## 📘 Official Danfoss Modbus Documentation

This integration is based on the official Modbus register list from Danfoss.

📄 **Download PDF:**  
👉 [ECL120 & ECL220 Modbus Specification (Rev 023)](./docs/IS2007%2C%20ECL120%20and%20ECL220%20Modbus%20Specification%2C%20rev%20023.pdf)

The document is shared with permission from Danfoss.

---

## 📄 License
MIT License

## ⚠️ Disclaimer

This integration is provided **as-is**, without any guarantees or warranties of any kind.

- The author is **not affiliated with Danfoss**.
- The author is **not a professional developer**.
- This integration may contain bugs, incorrect register mappings, or incomplete implementations.
- Writing values to the controller **may affect system behavior** and could potentially cause malfunction or damage if used incorrectly.

**You are fully responsible for:**
- Verifying register addresses and values
- Testing changes in a safe environment
- Ensuring that critical heating functions remain safe and operational

The author takes **no responsibility or liability** for:
- Damaged equipment
- Incorrect heating behavior
- Increased energy consumption
- Loss of comfort or system downtime

If you are unsure about a parameter or register — **do not write to it**.

Use this integration **at your own risk**.