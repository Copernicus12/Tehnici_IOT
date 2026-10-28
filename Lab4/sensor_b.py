import argparse
import asyncio
import logging
import random
import socket
import time

from common import (
    DEFAULT_PORT, MessageFactory, decode_message, encode_message,
    integer_field, port_number, positive_seconds, setup_logging,
)


class SensorBProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.factory = MessageFactory("B")
        self.transport = None
        self.pending = {}

    def connection_made(self, transport):
        self.transport = transport

    def error_received(self, exc):
        logging.warning("Eroare UDP: %s", exc)

    def datagram_received(self, data, addr):
        t4 = time.perf_counter_ns()
        try:
            message = decode_message(data)
            if message["sensor_id"] != "G" or message["type"] != "PROBE_ACK":
                raise ValueError("Era așteptat PROBE_ACK de la Gateway")
            if message.get("sensor_session_id") != self.factory.session_id:
                logging.warning("ACK ignorat: aparține altei sesiuni B")
                return
            probe_seq = integer_field(message, "probe_seq", minimum=1)
            t2 = integer_field(message, "t2_ns")
            t3 = integer_field(message, "t3_ns")
            if t3 < t2:
                raise ValueError("Momente temporale Gateway invalide")
            future = self.pending.get(probe_seq)
            if future is None or future.done():
                logging.warning("ACK întârziat sau duplicat, probe_seq=%d", probe_seq)
                return
            future.set_result((t2, t3, t4))
        except ValueError as exc:
            logging.warning("Datagramă ignorată: %s", exc)

    def send(self, message_type, **fields):
        message = self.factory.create(message_type, **fields)
        self.transport.sendto(encode_message(message))
        return message

    async def send_data(self):
        while True:
            temperature = round(random.uniform(18, 30), 2)
            message = self.send("DATA", measurement="temperature", value=temperature, unit="C")
            logging.info("Trimis DATA seq=%d temperatură=%.2f C", message["seq"], temperature)
            await asyncio.sleep(random.uniform(2.3, 2.7))

    async def probe_once(self, timeout):
        message = self.factory.create("PROBE")
        packet = encode_message(message)
        probe_seq = message["seq"]
        future = asyncio.get_running_loop().create_future()
        self.pending[probe_seq] = future
        try:
            t1 = time.perf_counter_ns()
            self.transport.sendto(packet)
            logging.info("Trimis PROBE seq=%d", probe_seq)
            t2, t3, t4 = await asyncio.wait_for(future, timeout=timeout)
            elapsed_ns = t4 - t1
            processing_ns = t3 - t2
            rtt_ns = elapsed_ns - processing_ns
            if rtt_ns < 0:
                logging.warning("Măsurătoare invalidă, RTT negativ; probe_seq=%d", probe_seq)
                return
            result = self.send(
                "RTT_RESULT", probe_seq=probe_seq, rtt_ns=rtt_ns,
                elapsed_ns=elapsed_ns, processing_ns=processing_ns,
            )
            logging.info(
                "PROBE_ACK probe_seq=%d RTT=%.3f ms; trimis RTT_RESULT seq=%d",
                probe_seq, rtt_ns / 1_000_000, result["seq"],
            )
        except asyncio.TimeoutError:
            logging.warning("TIMEOUT probe_seq=%d: fără ACK în %.2f s", probe_seq, timeout)
        finally:
            self.pending.pop(probe_seq, None)
            if not future.done():
                future.cancel()

    async def send_probes(self, interval, timeout):
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await self.probe_once(timeout)
            await asyncio.sleep(max(0.0, interval - (loop.time() - started)))


async def run(args):
    loop = asyncio.get_running_loop()
    protocol = SensorBProtocol()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol,
        remote_addr=(args.host, args.port),
        family=socket.AF_INET,
    )
    logging.info(
        "Gateway=%s; sesiune=%s; interval PROBE=%.2f s; timeout=%.2f s",
        transport.get_extra_info("peername"), protocol.factory.session_id,
        args.probe_interval, args.timeout,
    )
    tasks = [
        asyncio.create_task(protocol.send_data()),
        asyncio.create_task(protocol.send_probes(args.probe_interval, args.timeout)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        transport.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IP/nume Gateway")
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT)
    parser.add_argument("--probe-interval", type=positive_seconds, default=3.0)
    parser.add_argument("--timeout", type=positive_seconds, default=1.0)
    args = parser.parse_args()
    setup_logging("B")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logging.info("Oprire senzor B")
    except OSError as exc:
        logging.error("Nu se poate porni senzorul B: %s", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
