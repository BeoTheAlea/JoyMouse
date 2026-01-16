import asyncio
import gc
from bleak import BleakScanner, BleakClient

# Constants
JOYCON_MANUFACTURER_ID = 1363
JOYCON_MANUFACTURER_PREFIX = bytes([0x01, 0x00, 0x03, 0x7E])

# BLE GATT Characteristics UUID
INPUT_REPORT_UUID = "ab7de9be-89fe-49ad-828f-118f09df7fd2"
WRITE_COMMAND_UUID = "649d4ac9-8eb7-4e6c-af44-1ea54fe5f005"

# COMMANDS
COMMAND_LEDS = 0x09
COMMAND_VIBRATION = 0x0A

# SUBCOMMANDS
SUBCOMMAND_SET_PLAYER_LEDS = 0x07
SUBCOMMAND_PLAY_VIBRATION_PRESET = 0x02

used_addresses = set()

class SimpleGamepad:
    def __init__(self):
        self._last_inputs = {
            "RIGHT": {"buttons": {}},
            "LEFT": {"buttons": {}},
        }

class Player:
    def __init__(self, number, controller_type, side=None):
        self.number = number
        self.type = controller_type
        self.side = side
        self.clients = []

        # Explicit garbage collection to prevent reuse issues
        gc.collect()
        self.gamepad = SimpleGamepad()

def decode_joystick(data):
    try:
        if len(data) != 3:
            return 0, 0
        x = ((data[1] & 0x0F) << 8) | data[0]
        y = (data[2] << 4) | ((data[1] & 0xF0) >> 4)
        x = (x - 2048) / 2048.0
        y = (y - 2048) / 2048.0
        deadzone = 0.08
        if abs(x) < deadzone and abs(y) < deadzone:
            return 0, 0
        x = max(-1.0, min(1.0, x * 1.7))
        y = max(-1.0, min(1.0, y * 1.7))
        return int(x * 32767), int(y * 32767)
    except:
        return 0, 0

async def scan_device(prompt="controller"):
    print(f"\n🔍 Searching for your {prompt} (press sync)...")
    found_devices = []
    device_event = asyncio.Event()

    def callback(device, adv):
        if device.address in used_addresses:
            return
        data = adv.manufacturer_data.get(JOYCON_MANUFACTURER_ID)
        if data and data.startswith(JOYCON_MANUFACTURER_PREFIX):
            if not any(d.address == device.address for d in found_devices):
                found_devices.append(device)
                print(f"  Found {device.name or 'Unknown'} ({device.address})")
                device_event.set()

    scanner = BleakScanner(callback)
    await scanner.start()

    selected_device = None
    try:
        while True:
            await device_event.wait()
            device_event.clear()
            if found_devices:
                selected_device = found_devices[0]
                break
    finally:
        await scanner.stop()

    if selected_device:
        print(f"🎮 Selected {selected_device.name or 'Unknown'} ({selected_device.address})")
    else:
        print("❌ No device found.")

    return selected_device

def to_bytes_auto_length(integer, byteorder='big', signed=False):
    length = (integer.bit_length() + 7) // 8
    if length == 0:
        length = 1 # Represent 0 as a single byte b'\x00'
    return integer.to_bytes(length, byteorder=byteorder, signed=signed)

async def write_command(client, command_id, subcommand_id, buffer):
    # Pad buffer to 8 bytes minimum because some buffer lengths seems to crash
    buffer = buffer.ljust(8, b'\0')
    command = to_bytes_auto_length(command_id) + b"\x91\x01" + to_bytes_auto_length(subcommand_id) + b"\x00" + to_bytes_auto_length(len(buffer)) + b"\x00\x00" + buffer
    await client.write_gatt_char(WRITE_COMMAND_UUID, command)

async def play_vibration_preset(client, preset_id):
    await write_command(client, COMMAND_VIBRATION, SUBCOMMAND_PLAY_VIBRATION_PRESET, preset_id.to_bytes())

async def enable_imu(client):
    ENABLE_IMU_1 = bytes([0x0c, 0x91, 0x01, 0x02, 0x00, 0x04, 0x00, 0x00, 0x2f, 0x00, 0x00, 0x00])
    ENABLE_IMU_2 = bytes([0x0c, 0x91, 0x01, 0x04, 0x00, 0x04, 0x00, 0x00, 0x2f, 0x00, 0x00, 0x00])
    await client.write_gatt_char(WRITE_COMMAND_UUID, ENABLE_IMU_1)
    await asyncio.sleep(0.5)
    await client.write_gatt_char(WRITE_COMMAND_UUID, ENABLE_IMU_2)

async def set_leds(client, player_number):
    #Repoduce switch led patterns for up to 8 players https://en-americas-support.nintendo.com/app/answers/detail/a_id/22424
    led_pattern_by_played_id = {
        1: b'\x01',
        2: b'\x03',
        3: b'\x07',
        4: b'\x0F',
        5: b'\x09',
        6: b'\x05',
        7: b'\x0D',
        8: b'\x06',
    }

    if player_number > 8:
        player_number = 8

    await write_command(client, COMMAND_LEDS, SUBCOMMAND_SET_PLAYER_LEDS, led_pattern_by_played_id[player_number])

async def connect_and_setup(device, player, handler_func, *handler_args):
    client = BleakClient(device.address)
    await client.connect()
    client._device = device
    await set_leds(client, player.number)
    await enable_imu(client)
    await handler_func(client, player, *handler_args)
    player.clients.append(client)
    print(f"✅ Connected to {device.address}")
    return client

async def maintain_connection_loop(client, device, player, handler_func, *handler_args):
    while True:
        try:
            if not client.is_connected:
                await client.connect()
                await handler_func(client, player, *handler_args)
                print(f"🔄 Reconnected to {device.address}")
            await asyncio.sleep(1)
        except Exception as e:
            print(f"⚠️ Connection lost or error: {e}")
            if client.is_connected:
                await client.disconnect()
            await asyncio.sleep(5)

async def handle_dual_joycon(client, player: Player, side: str):
    from duo_logic import handle_duo_notification
    async def cb(sender, data):
        await handle_duo_notification(sender, data, side, player.gamepad)
    await client.start_notify(INPUT_REPORT_UUID, cb)

async def setup_player(number):
    print(f"\n🎮 Setting up Player {number}")
    while True:
        right = await scan_device(f"Player {number} RIGHT Joy-Con")
        if not right:
            return None
        used_addresses.add(right.address)

        left = await scan_device(f"Player {number} LEFT Joy-Con")
        if not left:
            return None
        used_addresses.add(left.address)

        player = Player(number, "DUAL_JOYCON")
        right_client = await connect_and_setup(right, player, handle_dual_joycon, "RIGHT")
        left_client = await connect_and_setup(left, player, handle_dual_joycon, "LEFT")
        asyncio.create_task(maintain_connection_loop(right_client, right, player, handle_dual_joycon, "RIGHT"))
        asyncio.create_task(maintain_connection_loop(left_client, left, player, handle_dual_joycon, "LEFT"))
        return player

async def main():
    try:
        players = []
        count = int(input("How many players? ").strip())
        for i in range(1, count + 1):
            player = await setup_player(i)
            if not player:
                print("❌ Setup failed. Exiting.")
                return
            players.append(player)

        print("🎮 All players connected. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nExiting...")

    finally:
        for p in players:
            for c in p.clients:
                if c.is_connected:
                    await c.disconnect()

            # NEW: Explicitly remove virtual gamepad
            # if hasattr(p, "gamepad") and p.gamepad:
            #    try:
            #        p.gamepad.reset()
            #        del p.gamepad
            #    except Exception as e:
            #        print(f"Error removing gamepad for player {p.number}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
