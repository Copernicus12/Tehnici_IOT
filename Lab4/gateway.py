import argparse
import asyncio
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import socket
import time

from common import (
    DEFAULT_PORT, MessageFactory, decode_message, encode_message,
    integer_field, port_number, setup_logging,
)


HEARTBEAT_TIMEOUT = 12.0
CSV_FIELDS = [
    "timestamp_utc", "gateway_session_id", "sensor_session_id", "source_ip",
    "source_port", "sample_count", "probe_seq", "elapsed_ms", "processing_ms",
    "rtt_ms", "mean_rtt_ms", "min_rtt_ms", "max_rtt_ms", "jitter_rtt_ms",
]


@dataclass
class RTTStats:
    count: int = 0
    total_ns: int = 0
    min_ns: int = 0
    max_ns: int = 0
    previous_ns: int = 0
    difference_total_ns: int = 0
    seen_probes: set = field(default_factory=set)

    def add(self, probe_seq, rtt_ns):
        if probe_seq in self.seen_probes:
            return False
        self.seen_probes.add(probe_seq)
        if self.count:
            self.difference_total_ns += abs(rtt_ns - self.previous_ns)
            self.min_ns = min(self.min_ns, rtt_ns)
            self.max_ns = max(self.max_ns, rtt_ns)
        else:
            self.min_ns = self.max_ns = rtt_ns
        self.count += 1
        self.total_ns += rtt_ns
        self.previous_ns = rtt_ns
        return True

    @property
    def mean_ms(self):
        return self.total_ns / self.count / 1_000_000

    @property
    def jitter_ms(self):
        if self.count < 2:
            return None
        return self.difference_total_ns / (self.count - 1) / 1_000_000


class GatewayProtocol(asyncio.DatagramProtocol):
    def __init__(self, csv_file, writer, failure):
        self.factory = MessageFactory("G")
        self.transport = None
        self.csv_file = csv_file
        self.writer = writer
        self.failure = failure
        self.started = time.monotonic()
        self.last_heartbeat = None
        self.a_state = "UNKNOWN"
        self.stats_by_session = {}

    def connection_made(self, transport):
        self.transport = transport

    def error_received(self, exc):
        logging.warning("Eroare UDP: %s", exc)

    def datagram_received(self, data, addr):
        t2 = time.perf_counter_ns()
        try:
            message = decode_message(data)
            sensor = message["sensor_id"]
            kind = message["type"]
            if kind == "DATA" and sensor in ("A", "B"):
                self.receive_data(message, addr)
            elif kind == "HEARTBEAT" and sensor == "A":
                self.last_heartbeat = time.monotonic()
                if self.a_state != "ONLINE":
                    self.a_state = "ONLINE"
                    logging.info("Senzor A -> ONLINE (heartbeat primit)")
                logging.info("A HEARTBEAT seq=%d de la %s:%d", message["seq"], *addr)
                reply = self.factory.create(
                    "HEARTBEAT_ACK", heartbeat_seq=message["seq"],
                    sensor_session_id=message["session_id"],
                )
                self.transport.sendto(encode_message(reply), addr)
            elif kind == "PROBE" and sensor == "B":
                self.reply_probe(message, addr, t2)
            elif kind == "RTT_RESULT" and sensor == "B":
                self.receive_rtt(message, addr)
            else:
                raise ValueError("Tip de mesaj nepermis pentru această sursă")
        except ValueError as exc:
            logging.warning("Datagramă ignorată de la %s: %s", addr, exc)
        except OSError as exc:
            logging.error("Eroare la procesarea mesajului: %s", exc)
            if not self.failure.done():
                self.failure.set_exception(exc)

    def receive_data(self, message, addr):
        expected = {"A": ("humidity", "%"), "B": ("temperature", "C")}
        measurement, unit = expected[message["sensor_id"]]
        value = message.get("value")
        if type(value) not in (int, float):
            raise ValueError("Valoare fizică invalidă")
        try:
            valid = math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid or message.get("measurement") != measurement or message.get("unit") != unit:
            raise ValueError("Mărime fizică, unitate sau valoare invalidă")
        logging.info(
            "%s DATA seq=%d %s=%.2f %s de la %s:%d",
            message["sensor_id"], message["seq"], measurement, value, unit, *addr,
        )

    def reply_probe(self, message, addr, t2):
        reply = self.factory.create(
            "PROBE_ACK", probe_seq=message["seq"],
            sensor_session_id=message["session_id"], t2_ns=t2,
        )
        reply["t3_ns"] = time.perf_counter_ns()
        self.transport.sendto(encode_message(reply), addr)
        logging.info("B PROBE seq=%d -> PROBE_ACK de la/către %s:%d", message["seq"], *addr)

    def receive_rtt(self, message, addr):
        probe_seq = integer_field(message, "probe_seq", minimum=1)
        rtt_ns = integer_field(message, "rtt_ns")
        elapsed_ns = integer_field(message, "elapsed_ns")
        processing_ns = integer_field(message, "processing_ns")
        if rtt_ns != elapsed_ns - processing_ns:
            raise ValueError("Rezultat RTT inconsistent")
        session = message["session_id"]
        if session not in self.stats_by_session:
            self.stats_by_session[session] = RTTStats()
            logging.info("Sesiune B nouă: %s; statistici inițializate", session)
        stats = self.stats_by_session[session]
        if not stats.add(probe_seq, rtt_ns):
            logging.warning("RTT_RESULT duplicat ignorat, probe_seq=%d", probe_seq)
            return
        jitter = stats.jitter_ms
        row = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "gateway_session_id": self.factory.session_id,
            "sensor_session_id": session,
            "source_ip": addr[0], "source_port": addr[1],
            "sample_count": stats.count, "probe_seq": probe_seq,
            "elapsed_ms": "%.6f" % (elapsed_ns / 1_000_000),
            "processing_ms": "%.6f" % (processing_ns / 1_000_000),
            "rtt_ms": "%.6f" % (rtt_ns / 1_000_000),
            "mean_rtt_ms": "%.6f" % stats.mean_ms,
            "min_rtt_ms": "%.6f" % (stats.min_ns / 1_000_000),
            "max_rtt_ms": "%.6f" % (stats.max_ns / 1_000_000),
            "jitter_rtt_ms": "" if jitter is None else "%.6f" % jitter,
        }
        self.writer.writerow(row)
        self.csv_file.flush()
        logging.info(
            "B RTT_RESULT seq=%d probe_seq=%d n=%d RTT=%.3f ms mediu=%.3f ms "
            "min=%.3f ms max=%.3f ms jitter=%s",
            message["seq"], probe_seq, stats.count, rtt_ns / 1_000_000,
            stats.mean_ms, stats.min_ns / 1_000_000, stats.max_ns / 1_000_000,
            "N/A (minimum 2 valori)" if jitter is None else "%.3f ms" % jitter,
        )

    async def monitor_heartbeat(self):
        while True:
            reference = self.started if self.last_heartbeat is None else self.last_heartbeat
            elapsed = time.monotonic() - reference
            if elapsed >= HEARTBEAT_TIMEOUT and self.a_state != "OFFLINE":
                self.a_state = "OFFLINE"
                logging.warning("Senzor A -> OFFLINE (fără heartbeat de %.1f s)", elapsed)
            await asyncio.sleep(0.5)


async def run(args):
    csv_path = Path(args.csv).expanduser()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a+", newline="", encoding="utf-8") as csv_file:
        csv_file.seek(0)
        header = next(csv.reader(csv_file), None)
        if header is not None and header != CSV_FIELDS:
            raise ValueError("Fișierul CSV existent are alt format; alege altă cale cu --csv")
        csv_file.seek(0, 2)
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        if header is None:
            writer.writeheader()
            csv_file.flush()
        loop = asyncio.get_running_loop()
        failure = loop.create_future()
        protocol = GatewayProtocol(csv_file, writer, failure)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol, local_addr=(args.host, args.port), family=socket.AF_INET,
        )
        heartbeat_task = asyncio.create_task(protocol.monitor_heartbeat())
        logging.info("Ascult pe %s; CSV=%s", transport.get_extra_info("sockname"), csv_path.resolve())
        logging.info("A=UNKNOWN; timeout heartbeat=12 s; sesiune Gateway=%s", protocol.factory.session_id)
        try:
            await failure
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            transport.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="Interfața locală de ascultare")
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT)
    parser.add_argument("--csv", default="results/rtt.csv", help="Calea fișierului CSV")
    args = parser.parse_args()
    setup_logging("G")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logging.info("Oprire Gateway")
    except (OSError, ValueError) as exc:
        logging.error("Gateway oprit: %s", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
