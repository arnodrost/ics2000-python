import enum
import requests
import json
import ast
import logging

from ics2000.Command import decrypt, Command
from ics2000.Devices import Device, Dimmer, Optional


def constraint_int(inp, min_val, max_val) -> int:
    if inp < min_val:
        return min_val
    elif inp > max_val:
        return max_val
    else:
        return inp


class CoreException(Exception):
    pass


class Hub:
    aes = None
    mac = None
    base_url = "https://trustsmartcloud2.com/ics2000_api/"

    def __init__(self, mac, email, password):
        """Initialize an ICS2000 hub."""
        self.mac = mac
        self._email = email
        self._password = password
        self._connected = False
        self._homeId = -1
        self._devices = []
        self.login_user()
        self.pull_devices()

    def login_user(self):
        logging.debug("Logging in user")
        url = f'{Hub.base_url}/account.php'
        params = {"action": "login", "email": self._email, "mac": self.mac.replace(":", ""),
                  "password_hash": self._password, "device_unique_id": "android", "platform": "Android"}
        req = requests.get(url, params=params)
        if req.status_code == 200:
            resp = req.json()
            self.aes = resp["homes"][0]["aes_key"]
            self._homeId = resp["homes"][0]["home_id"]
            if self.aes is not None:
                logging.debug("Successfully got AES key")
                self._connected = True
            else:
                raise CoreException(f'Could not get AES key for user {self._email}')
        else:
            raise CoreException(f'Could not login user {self._email}')

    @property
    def connected(self):
        return self._connected

    def pull_devices(self):
        device_type_values = [item.value for item in DeviceType]
        url = f'{Hub.base_url}/gateway.php'
        params = {"action": "sync", "email": self._email, "mac": self.mac.replace(":", ""),
                  "password_hash": self._password, "home_id": self._homeId}
        resp = requests.get(url, params=params)
        self._devices = []
        for device in resp.json():
            decrypted = json.loads(decrypt(device["data"], self.aes))
            if "module" in decrypted and "info" in decrypted["module"]:
                decrypted = decrypted["module"]
                name = decrypted["name"]
                entity_id = decrypted["id"]
                if decrypted["device"] not in device_type_values:
                    self._devices.append(Device(name, entity_id, self))
                    continue
                dev = DeviceType(decrypted["device"])
                if dev == DeviceType.LAMP:
                    self._devices.append(Device(name, entity_id, self))
                if dev == DeviceType.DIMMER:
                    self._devices.append(Dimmer(name, entity_id, self))
                if dev == DeviceType.OPEN_CLOSE:
                    self._devices.append(Device(name, entity_id, self))
                if dev == DeviceType.DIMMABLE_LAMP:
                    self._devices.append(Dimmer(name, entity_id, self))
            else:
                pass  # TODO: log something here

    @property
    def devices(self):
        return self._devices

    def send_command(self, command):
        url = f'{Hub.base_url}/command.php'
        params = {"action": "add", "email": self._email, "mac": self.mac.replace(":", ""),
                  "password_hash": self._password, "device_unique_id": "android", "command": command}
        response = requests.get(url, params=params)
        if 200 != response.status_code:
            raise CoreException(f'Could not send command {command}: {response.text}')

    def turn_off(self, entity):
        cmd = self.simple_command(entity, 0, 0)
        self.send_command(cmd.getcommand())

    def turn_on(self, entity):
        cmd = self.simple_command(entity, 0, 1)
        self.send_command(cmd.getcommand())

    def blinds_up(self, entity):
        cmd = self.simple_command(entity, 0, 2)
        self.send_command(cmd.getcommand())

    def blinds_down(self, entity):
        cmd = self.simple_command(entity, 2, 2)
        self.send_command(cmd.getcommand())

    def blinds_stop(self, entity):
        cmd = self.simple_command(entity, 1, 2)
        self.send_command(cmd.getcommand())

    def dim(self, entity, level):
        # level is in range 1-10
        cmd = self.simple_command(entity, 1, level)
        self.send_command(cmd.getcommand())

    def zigbee_color_temp(self, entity, color_temp):
        color_temp = constraint_int(color_temp, 0, 600)
        cmd = self.simple_command(entity, 9, color_temp)
        self.send_command(cmd.getcommand())

    def zigbee_dim(self, entity, dim_lvl):
        dim_lvl = constraint_int(dim_lvl, 1, 254)
        cmd = self.simple_command(entity, 4, dim_lvl)
        self.send_command(cmd.getcommand())

    def zigbee_switch(self, entity, power):
        cmd = self.simple_command(entity, 3, (str(1) if power else str(0)))
        self.send_command(cmd.getcommand())

    def get_device_status(self, entity) -> []:
        url = f'{Hub.base_url}/entity.php'
        params = {
            "action": "get-multiple",
            "email": self._email,
            "mac": self.mac.replace(":", ""),
            "password_hash": self._password,
            "home_id": self._homeId,
            "entity_id": f'[{str(entity)}]'
        }
        arr = requests.get(url, params=params).json()
        if len(arr) == 1 and "status" in arr[0] and arr[0]["status"] is not None:
            obj = arr[0]
            status = json.loads(decrypt(obj["status"], self.aes))
            if "module" in status and "functions" in status["module"]:
                return status["module"]["functions"]
        return []

    def get_lamp_status(self, entity) -> Optional[bool]:
        status = self.get_device_status(entity)
        if len(status) >= 1:
            return True if status[0] == 1 else False
        return False

    def simple_command(self, entity, function, value):
        cmd = Command()
        cmd.setmac(self.mac)
        cmd.settype(128)
        cmd.setmagic()
        cmd.setentityid(entity)
        cmd.setdata(
            json.dumps({'module': {'id': entity, 'function': function, 'value': value}}, separators=(',', ':')),
            self.aes
        )
        return cmd


class DeviceType(enum.Enum):
    LAMP = 1
    DIMMER = 2
    OPEN_CLOSE = 3
    DIMMABLE_LAMP = 24


def get_hub(mac, email, password) -> Optional[Hub]:
    url = f'{Hub.base_url}/gateway.php'
    params = {"action": "check", "email": email, "mac": mac.replace(":", ""), "password_hash": password}
    resp = requests.get(url, params=params)
    if resp.status_code == 200:
        if ast.literal_eval(resp.text)[1] == "true":
            return Hub(mac, email, password)
    raise CoreException(f'Could not create a Hub object for mac/user {mac}/{email}')
