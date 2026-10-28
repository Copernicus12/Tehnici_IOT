import argparse
import json
import logging
import math
import threading
import uuid


DEFAULT_PORT = 9999
MAX_DATAGRAM = 8192
MESSAGE_TYPES = {"DATA", "HEARTBEAT", "HEARTBEAT_ACK", "PROBE", "PROBE_ACK", "RTT_RESULT"}


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Intervalul trebuie să fie finit și pozitiv.")
    return number


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("Portul trebuie să fie între 1 și 65535.")
    return number


def setup_logging(name):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [" + name + "] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def integer_field(message, key, minimum=0):
    value = message.get(key)
    if type(value) is not int or value < minimum:
        raise ValueError("Câmp invalid: " + key)
    return value


def encode_message(message):
    return json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode_message(data):
    if len(data) > MAX_DATAGRAM:
        raise ValueError("Datagramă prea mare")
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Mesajul nu este JSON UTF-8 valid") from exc
    if not isinstance(message, dict):
        raise ValueError("Mesajul trebuie să fie un obiect JSON")
    if message.get("sensor_id") not in ("A", "B", "G"):
        raise ValueError("Identificator de senzor necunoscut")
    if not isinstance(message.get("type"), str) or message["type"] not in MESSAGE_TYPES:
        raise ValueError("Tip de mesaj necunoscut")
    session = message.get("session_id")
    if not isinstance(session, str) or not 1 <= len(session) <= 64:
        raise ValueError("Identificator de sesiune invalid")
    integer_field(message, "seq", minimum=1)
    return message


class MessageFactory:

    def __init__(self, sensor_id):
        self.sensor_id = sensor_id
        self.session_id = uuid.uuid4().hex
        self._seq = 0
        self._lock = threading.Lock()

    def create(self, message_type, **fields):
        with self._lock:
            self._seq += 1
            message = {
                "sensor_id": self.sensor_id,
                "session_id": self.session_id,
                "type": message_type,
                "seq": self._seq,
            }
        message.update(fields)
        return message
