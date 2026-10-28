import argparse
import asyncio
import itertools
import json
import logging
import math
import random
import socket
import time


class UDPProtocol(asyncio.DatagramProtocol):
    """Transport UDP asincron compatibil cu Python 3.9."""

    def __init__(self):
        self.transport = None
        self.received = asyncio.Queue()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, payload, source):
        # Măsurăm recepția înainte de așteptarea task-ului care procesează ACK-ul.
        self.received.put_nowait((payload, source, time.perf_counter_ns()))

    def error_received(self, exc):
        self.received.put_nowait(exc)

    async def receive(self):
        item = await self.received.get()
        if isinstance(item, Exception):
            raise item
        return item


async def send_message(endpoint, destination, sensor_id, sequence, message_type, **fields):
    message = {
        "sensor_id": sensor_id,
        "type": message_type,
        "seq": next(sequence),
        **fields,
    }
    payload = json.dumps(message).encode("utf-8")
    try:
        sent_at = time.perf_counter_ns()
        endpoint.transport.sendto(payload, destination)
    except OSError as exc:
        logging.warning("Send failed (%s): %s", message_type, exc)
        return None
    logging.info("Sent: %s", payload.decode("utf-8"))
    return message["seq"], sent_at

async def transmit_data(endpoint, destination, sensor_id, sequence):
    while True:
        await send_message(
            endpoint, destination, sensor_id, sequence, "DATA",
            humidity=round(random.uniform(30.0, 80.0), 2), unit="%",
        )
        await asyncio.sleep(random.uniform(2.3, 2.7))

async def receive_ack(endpoint, destination, sensor_id, probe_seq, t1, timeout):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        payload, source, t4 = await asyncio.wait_for(
            endpoint.receive(), timeout=remaining,
        )
        if source != destination:
            continue
        try:
            message = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            logging.warning("Ignored invalid JSON response")
            continue
        if not isinstance(message, dict):
            continue
        if (message.get("type") != "PROBE_ACK"
                or message.get("sensor_id") != sensor_id
                or message.get("seq") != probe_seq):
            continue
        t2, t3 = message.get("t2"), message.get("t3")
        if (type(t2) is not int or type(t3) is not int
                or not 0 <= t2 <= t3 or t3 - t2 > t4 - t1):
            logging.warning("Ignored PROBE_ACK with invalid gateway timestamps")
            continue
        return ((t4 - t1) - (t3 - t2)) / 1_000_000

async def transmit_probes(endpoint, destination, sensor_id, sequence, interval, timeout):
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        probe = await send_message(endpoint, destination, sensor_id, sequence, "PROBE")
        if probe is not None:
            probe_seq, t1 = probe
            try:
                rtt_ms = await receive_ack(
                    endpoint, destination, sensor_id, probe_seq, t1, timeout,
                )
            except asyncio.TimeoutError:
                logging.warning("PROBE timeout: seq=%s, timeout=%.3f s", probe_seq, timeout)
            except OSError as exc:
                logging.warning("Receive failed: %s", exc)
            else:
                await send_message(
                    endpoint, destination, sensor_id, sequence, "RTT",
                    probe_seq=probe_seq, rtt_ms=rtt_ms,
                )
        await asyncio.sleep(max(0.0, interval - (loop.time() - started)))


async def run(args, destination):
    sequence = itertools.count(1)
    loop = asyncio.get_running_loop()
    transport, endpoint = await loop.create_datagram_endpoint(
        UDPProtocol, local_addr=("0.0.0.0", 0), family=socket.AF_INET,
    )
    tasks = [
        asyncio.create_task(transmit_data(endpoint, destination, args.sensor_id, sequence)),
        asyncio.create_task(transmit_probes(
            endpoint, destination, args.sensor_id, sequence,
            args.probe_interval, args.timeout,
        )),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        transport.close()

def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("Value must be a finite, positive number of seconds.")
    return seconds

def main():
    parser = argparse.ArgumentParser(
        description="Sensor B: simulated humidity and corrected UDP RTT measurements."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Gateway address")
    parser.add_argument("--port", type=int, default=9999, help="Gateway UDP port")
    parser.add_argument("--sensor-id", default="B", help="Sensor identifier")
    parser.add_argument("--probe-interval", type=positive_seconds, default=5.0,
                        help="Interval between probes in seconds (default: 5)")
    parser.add_argument("--timeout", type=positive_seconds, default=2.0,
                        help="PROBE_ACK timeout in seconds (default: 2)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        destination = (socket.gethostbyname(args.host), args.port)
    except OSError as exc:
        parser.error(f"Cannot resolve gateway address: {exc}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(run(args, destination))
    except KeyboardInterrupt:
        logging.info("Stopping sensor B.")

if __name__ == "__main__":
    main()
