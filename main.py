# Import libraries
import onewire, ds18x20, time, bme280, utime, _thread
import urequests, network
import struct
from machine import Pin, I2C, ADC, UART, WDT
from ota_updater import OTAUpdater

# --- VERSION & REMOTE OTA CONFIGURATION ---
CURRENT_VERSION = "1.1.1"
RAW_GITHUB_URL = "https://raw.githubusercontent.com/bcpocketrocket-cell/weather-station/main"

# --- HARDWARE WATCHDOG TIMER (8-second max limit on Pico W) ---
wdt = WDT(timeout=8000)
wdt.feed()

# Define System Pins & Core Locks
led = Pin("LED", Pin.OUT)
spLock = _thread.allocate_lock()

# --- NETWORK CREDENTIALS ---
ssid = 'Project Blackpool Sat'
password = 'Satellite1!'

# --- THINGSPEAK API KEYS & URLS ---
HTTP_HEADERS = {'Content-Type': 'application/json'}

# Channel 1: Primary Weather Channel
WEATHER_WRITE_API_KEY = 'XWRBLMWRW22O36AY'
WEATHER_URL = 'http://api.thingspeak.com/update?api_key=' + WEATHER_WRITE_API_KEY

# Channel 2: Solar & Power Telemetry Channel
SOLAR_WRITE_API_KEY = 'QGGKJYUG8MFJRG73'
SOLAR_URL = 'http://api.thingspeak.com/update?api_key=' + SOLAR_WRITE_API_KEY

# Global Rain & Wind Counters (Core 1)
windCount = 0
rainCount = 0

# Assign I2C Bus for BME280 (Pins 20, 21)
i2c = I2C(0, sda=Pin(20), scl=Pin(21), freq=400000)

# Wi-Fi Interface Setup
sta_if = network.WLAN(network.STA_IF)
sta_if.active(True)

def connect_wifi():
    if not sta_if.isconnected():
        print("Connecting to Starlink Wi-Fi...")
        sta_if.connect(ssid, password)
        timeout = 20
        while not sta_if.isconnected() and timeout > 0:
            wdt.feed()
            utime.sleep(1)
            timeout -= 1

    if sta_if.isconnected():
        print("Connected. Network Configuration:", sta_if.ifconfig())
        return True
    else:
        print("Wi-Fi Connection Failed.")
        return False

# Initial Wi-Fi Connection Attempt
connect_wifi()

# --- RUN BOOT TIME OTA UPDATE CHECK ---
wdt.feed()
if sta_if.isconnected():
    updater = OTAUpdater(RAW_GITHUB_URL, CURRENT_VERSION, wdt=wdt)
    update_needed, new_version = updater.check_for_updates()
    if update_needed:
        print("New version detected (" + new_version + "). Applying update...")
        updater.safe_download_and_install(["main.py"])

# --- CORE 0 WEATHER SENSORS ---
tempSensor = Pin(2, Pin.IN)
sensor = ds18x20.DS18X20(onewire.OneWire(tempSensor))
roms = sensor.scan()

bme = bme280.BME280(i2c=i2c, address=0x76)
windDir = ADC(Pin(26))

def compass_direction():
    d_reading = windDir.read_u16() / 64
    if 250 <= d_reading <= 284: d = 112.5
    elif 285 <= d_reading <= 304: d = 67.5
    elif 305 <= d_reading <= 324: d = 90
    elif 325 <= d_reading <= 374: d = 157.5
    elif 375 <= d_reading <= 450: d = 135
    elif 451 <= d_reading <= 509: d = 202.5
    elif 510 <= d_reading <= 549: d = 180
    elif 550 <= d_reading <= 649: d = 22.5
    elif 650 <= d_reading <= 724: d = 45
    elif 725 <= d_reading <= 797: d = 247.5
    elif 798 <= d_reading <= 824: d = 225
    elif 825 <= d_reading <= 874: d = 337.5
    elif 875 <= d_reading <= 909: d = 0
    elif 910 <= d_reading <= 934: d = 292.5
    elif 935 <= d_reading <= 974: d = 315
    elif 975 <= d_reading <= 1023: d = 270
    else: d = 360
    return d

def calculate_wind_direction():
    reading = windDir.read_u16() / 64
    if 250 <= reading <= 284: s = "ESE"
    elif 285 <= reading <= 304: s = "ENE"
    elif 305 <= reading <= 324: s = "E"
    elif 325 <= reading <= 374: s = "SSE"
    elif 375 <= reading <= 450: s = "SE"
    elif 451 <= reading <= 509: s = "SSW"
    elif 510 <= reading <= 549: s = "S"
    elif 550 <= reading <= 649: s = "NNE"
    elif 650 <= reading <= 724: s = "NE"
    elif 725 <= reading <= 797: s = "WSW"
    elif 798 <= reading <= 824: s = "SW"
    elif 825 <= reading <= 874: s = "NNW"
    elif 875 <= reading <= 909: s = "N"
    elif 910 <= reading <= 934: s = "WNW"
    elif 935 <= reading <= 974: s = "NW"
    elif 975 <= reading <= 1023: s = "W"
    else: s = "N/A"
    return s

# --- RS485 & PZEM-017 SOLAR MONITOR SETUP ---
uart = UART(1, baudrate=9600, tx=Pin(4), rx=Pin(5), bits=8, parity=None, stop=2)
de_pin = Pin(6, Pin.OUT)
de_pin.value(0) # Default to RX Mode

def calculate_crc(data):
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for _ in range(8):
            if (crc & 0x0001) != 0:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return struct.pack('<H', crc)

def read_pzem017(slave_addr=0x01):
    msg = bytes([slave_addr, 0x04, 0x00, 0x00, 0x00, 0x08])
    msg += calculate_crc(msg)
    
    de_pin.value(1) # TX Mode
    uart.write(msg)
    utime.sleep_ms(10)
    de_pin.value(0) # RX Mode
    
    utime.sleep_ms(150)
    if uart.any():
        response = uart.read()
        if response and len(response) >= 21 and response[1] == 0x04:
            voltage_raw = struct.unpack('>H', response[3:5])[0]
            current_raw = struct.unpack('>H', response[5:7])[0]
            power_raw   = struct.unpack('>I', response[7:11])[0]
            energy_raw  = struct.unpack('>I', response[11:15])[0]
            
            voltage = round(voltage_raw * 0.01, 2)
            current = round(current_raw * 0.01, 2)
            power   = round(power_raw * 0.1, 1)
            energy  = energy_raw
            return voltage, current, power, energy
            
    return None, None, None, None

def get_current_timestamp(version_str):
    t = utime.localtime()
    date_str = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(t[0], t[1], t[2], t[3], t[4], t[5])
    return date_str + " (v" + version_str + ")"

# --- CORE 1 TASK (Rain Gauge & Anemometer Pulses) ---
def core1_task():
    rainInput = Pin(3, Pin.IN, Pin.PULL_UP)
    rainFlag = 0
    windInput = Pin(4, Pin.IN, Pin.PULL_UP)
    windFlag = 0

    while True:
        spLock.acquire()
        
        # Rain Gauge
        if rainInput.value() == 0 and rainFlag == 1:
            global rainCount
            rainCount += 1
        rainFlag = rainInput.value()

        # Anemometer
        if windInput.value() == 0 and windFlag == 1:
            global windCount
            windCount += 1
        windFlag = windInput.value()

        utime.sleep(0.01)
        spLock.release()

# Start Background Core 1 Pulse Counter
_thread.start_new_thread(core1_task, ())

# --- MAIN RUNTIME LOOP ---
print("Weather Station Active - Version " + CURRENT_VERSION)
loop_count = 0

while True:
    wdt.feed()
    led.value(0)

    # 60-second delay loop (feeds Watchdog every 1s)
    for _ in range(60):
        wdt.feed()
        utime.sleep(1)

    spLock.acquire()

    try:
        # 1. DS18B20 Indoor Temp (with error handling)
        IndoorTemp = 0.0
        try:
            sensor.convert_temp()
            IndoorTemp = round(sensor.read_temp(roms[0]), 1)
        except Exception as e_ds:
            print("DS18B20 Read Error:", e_ds)

        # 2. BME280 Environmental Data (with error handling)
        humidity, pressure, OutdoorTemp = 0.0, 0.0, 0.0
        try:
            bme_data = bme.read_compensated_data()
            humidity = round(bme_data[2] / 1024, 1)
            pressure = round((bme_data[1] // 256) / 1000, 2)
            OutdoorTemp = round(bme_data[0] / 100, 1)
        except Exception as e_bme:
            print("BME280 Read Error:", e_bme)

        # 3. Wind & Rain
        windHeading = calculate_wind_direction()
        compassDir = compass_direction()
        measuredWind = (windCount / 15) * 2.4
        windCount = 0

        rainfall = rainCount * 0.2794
        rainCount = 0

        # 4. Read Solar Telemetry from PZEM-017 (returns None if unpopulated)
        batt_v, batt_i, batt_p, batt_e = read_pzem017()

        timestamp_str = get_current_timestamp(CURRENT_VERSION)

        print("Timestamp:", timestamp_str)
        print("Indoor Temp:", IndoorTemp, "°C | Outdoor Temp:", OutdoorTemp, "°C")
        print("Humidity:", humidity, "% | Pressure:", pressure, "kPa")
        print("Wind:", compassDir, windHeading, measuredWind, "km/h | Rain:", rainfall, "mm")
        if batt_v is not None:
            print("Battery:", batt_v, "V | Current:", batt_i, "A | Power:", batt_p, "W\n")

        # 5. Connect and Post Payload Data
        if connect_wifi():
            wdt.feed()
            
            # Post Channel 1: Primary Weather Feed (Field 8 used for Payload Timestamp)
            weather_payload = {
                'field1': IndoorTemp,
                'field2': humidity,
                'field3': pressure,
                'field4': measuredWind,
                'field5': compassDir,
                'field6': rainfall,
                'field7': OutdoorTemp,
                'field8': timestamp_str  # e.g., "2026-10-16 12:10:00 (v1.1.0)"
            }

            try:
                req_w = urequests.post(WEATHER_URL, json=weather_payload, headers=HTTP_HEADERS)
                req_w.close()
                print("Weather channel updated successfully.")
            except Exception as ew:
                print("Failed to update weather channel:", ew)

            # Post Channel 2: Solar & Power Telemetry (ONLY if hardware reads valid values)
            if batt_v is not None and SOLAR_WRITE_API_KEY != 'YOUR_SOLAR_WRITE_API_KEY':
                solar_payload = {
                    'field1': batt_v,
                    'field2': batt_i,
                    'field4': batt_p,
                    'field7': batt_e
                }
                try:
                    req_s = urequests.post(SOLAR_URL, json=solar_payload, headers=HTTP_HEADERS)
                    req_s.close()
                    print("Solar channel updated successfully.")
                except Exception as es:
                    print("Failed to update solar channel:", es)

    except Exception as e:
        print("Critical error in main loop execution:", e)

    led.value(1)
    utime.sleep(1)
    spLock.release()

    # Track 24-Hour Elapsed Time (1440 iterations @ 60s each)
    loop_count += 1
    if loop_count >= 1440:
        print("24 hours elapsed. Resetting board to perform scheduled OTA update check...")
        utime.sleep(1)
        machine.reset()
