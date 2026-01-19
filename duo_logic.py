import json
import os
import pynput

BUTTON_CODES = {
    "RIGHT": {
        "A":         0x000800,
        "B":         0x000400,
        "X":         0x000200,
        "Y":         0x000100,
        "PLUS":      0x000002,
        "STICK":     0x000004,
        "SHOULDER":  0x004000,
        "TRIGGER":   0x008000,
    },
    "LEFT": {
        "UP":     0x000002,
        "DOWN":   0x000001,
        "LEFT":   0x000008,
        "RIGHT":  0x000004,
        "MINUS":  0x000100,
        "STICK":  0x000800,
        "SHOULDER":  0x000040,
        "TRIGGER":   0x000080,
    }
}

LOCAL_DIR = os.path.dirname(os.path.realpath(__file__))
BUTTONS_MAP_FILE = os.path.join(LOCAL_DIR, 'button_map.json')
BUTTONS_MAP = {}
with open(BUTTONS_MAP_FILE, 'r') as file:
    BUTTONS_MAP = json.loads(file.read())

mouse = pynput.mouse.Controller()
MOUSE_MAP = {
        'MOUSE_LEFT': pynput.mouse.Button.left,
        'MOUSE_RIGHT': pynput.mouse.Button.right,
    }
keyboard = pynput.keyboard.Controller()
KEYBOARD_MAP = {
        'CTRL': pynput.keyboard.Key.ctrl,
        'SHIFT': pynput.keyboard.Key.shift,
        'SPACE': pynput.keyboard.Key.space,
    }

DEAD_ZONE = 10000
NDEAD_ZONE = -1 * DEAD_ZONE

def decode_joystick(data):
    x = ((data[1] & 0x0F) << 8) | data[0]
    y = (data[2] << 4) | ((data[1] & 0xF0) >> 4)
    x = max(-1.0, min(1.0, (x - 2048) / 2048.0 * 1.7))
    y = max(-1.0, min(1.0, (y - 2048) / 2048.0 * 1.7))
    return int(x * 32767), int(y * 32767)

def Press(button: str):
    if button in MOUSE_MAP:
        mouse.press(MOUSE_MAP[button])
    elif button in KEYBOARD_MAP:
        keyboard.press(KEYBOARD_MAP[button])
    else:
        keyboard.press(button)

def Release(button: str):
    if button in MOUSE_MAP:
        mouse.release(MOUSE_MAP[button])
    elif button in KEYBOARD_MAP:
        keyboard.release(KEYBOARD_MAP[button])
    else:
        keyboard.release(button)

def DecodeMouseCoords(buffer, index = 0):
    if (len(buffer) < 0x18):
        return (960, 466)

    raw_x = buffer[0x11] << 8 | buffer[0x10]
    raw_y = buffer[0x13] << 8 | buffer[0x12]

    raw_x = buffer[index+1] << 8 | buffer[index]
    raw_y = buffer[index+3] << 8 | buffer[index+2]
    
    norm_x = max(-1.0, min(raw_x / 32767, 1.0))
    norm_y = max(-1.0, min(raw_y / 32767, 1.0))

    x = (norm_x + 1) * 0.5 * 100
    y = (1 - (norm_y + 1) * 0.5) * 100

    return (x, y)

async def handle_duo_notification(sender, data, side, gamepad):
    offset = 4 if side == "LEFT" else 3
    state = int.from_bytes(data[offset:offset+3], 'big')

    # This is how the C++ version does state. Might bee important.
    # uint32_t state = (buffer[btnOffset] << 16) | (buffer[btnOffset + 1] << 8) | buffer[btnOffset + 2];

    last = gamepad._last_inputs

    # Mouse
    if side == "RIGHT" :
        mouse_x, mouse_y = DecodeMouseCoords(data, last['mouse_index'])
        if (mouse_x, mouse_y) != last['mouse_cords']:
            print(f'I think the mouse is {mouse_x}, {mouse_y}')
        last['mouse_cords'] = (mouse_x, mouse_y)
##        mouse.position = (mouse_x, mouse_y)


    # Joystick
    stick = data[10:13] if side == "LEFT" else data[13:16]
    x, y = decode_joystick(stick)
    joysticks = {
            'UP': y > DEAD_ZONE,
            'DOWN': y < NDEAD_ZONE,
            'LEFT': x < NDEAD_ZONE,
            'RIGHT': x > DEAD_ZONE,
        }
    for direction, val in joysticks.items():
        key = f'STICK_{direction}'
        last_val = last[side].get(key, None)
        if val != last_val:
            last[side][key] = val
            button = BUTTONS_MAP[side][key]
            if button == '' or button is None:
                continue

            if val:
                Press(button)
            else:
                Release(button)

    # Digital Buttons
    for name, mask in BUTTON_CODES[side].items():
        pressed = bool(state & mask)
        button = BUTTONS_MAP[side][name]
        if button == '' or button is None:
            continue

        if last[side]["buttons"].get(name) != pressed:
            last[side]["buttons"][name] = pressed
            if pressed:
                Press(button)
                if  name == 'X':
                    last['mouse_index'] += 1
                    print(last['mouse_index'])
                if  name == 'Y':
                    last['mouse_index'] -= 1
                    print(last['mouse_index'])
            else:
                Release(button)
