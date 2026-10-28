import argparse
import logging
import random
import socket
import threading
import time

from common import (
    DEFAULT_PORT, MAX_DATAGRAM, MessageFactory, decode_message, encode_message,
    integer_field, port_number, positive_seconds, setup_logging,
)


def periodic_worker(interval, action, stop):
    deadline = time.monotonic()
    while not stop.is_set():
        action()
        deadline = max(deadline + interval, time.monotonic())
        stop.wait(max(0.0, deadline - time.monotonic()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IP/nume Gateway")
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT)
    parser.add_argument(
        "--gateway-timeout", type=positive_seconds, default=12.0,
        help="Secunde fără confirmare heartbeat înainte de Gateway OFFLINE (implicit: 12)",
    )
    args = parser.parse_args()
    setup_logging("A")

    try:
        gateway = (socket.gethostbyname(args.host), args.port)
    except OSError as exc:
        parser.error("Adresa Gateway nu poate fi rezolvată: " + str(exc))

    factory = MessageFactory("A")
    stop = threading.Event()
    send_lock = threading.Lock()
    health_lock = threading.Lock()
    pending_heartbeats = {}
    last_ack = None
    gateway_state = "UNKNOWN"
    monitor_started = time.monotonic()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # Limitează așteptarea receptorului pentru a permite oprirea cu Ctrl+C.
        sock.settimeout(0.5)

        def send(message_type, **fields):
            with send_lock:
                message = factory.create(message_type, **fields)
                if message_type == "HEARTBEAT":
                    now = time.monotonic()
                    with health_lock:
                        expired = [
                            seq for seq, sent in pending_heartbeats.items()
                            if now - sent >= args.gateway_timeout
                        ]
                        for seq in expired:
                            pending_heartbeats.pop(seq)
                        # Înregistrăm proba înainte ca răspunsul să poată sosi.
                        pending_heartbeats[message["seq"]] = now
                try:
                    sock.sendto(encode_message(message), gateway)
                except OSError as exc:
                    if message_type == "HEARTBEAT":
                        with health_lock:
                            pending_heartbeats.pop(message["seq"], None)
                    logging.warning("Trimitere eșuată pentru %s: %s", message_type, exc)
                    return
                detail = ""
                if message_type == "DATA":
                    detail = " umiditate=%.2f %%" % fields["value"]
                logging.info("Trimis %s seq=%d%s", message_type, message["seq"], detail)

        def send_data():
            send("DATA", measurement="humidity", value=round(random.uniform(35, 75), 2), unit="%")

        def send_heartbeat():
            send("HEARTBEAT")

        def receive_acks():
            nonlocal last_ack, gateway_state
            while not stop.is_set():
                try:
                    data, source = sock.recvfrom(MAX_DATAGRAM + 1)
                except socket.timeout:
                    continue
                except OSError as exc:
                    logging.warning("Recepție ACK eșuată: %s", exc)
                    stop.wait(0.5)
                    continue
                if source != gateway:
                    continue
                try:
                    message = decode_message(data)
                    if message["sensor_id"] != "G" or message["type"] != "HEARTBEAT_ACK":
                        continue
                    if message.get("sensor_session_id") != factory.session_id:
                        continue
                    heartbeat_seq = integer_field(message, "heartbeat_seq", minimum=1)
                except ValueError as exc:
                    logging.warning("ACK invalid ignorat: %s", exc)
                    continue
                with health_lock:
                    now = time.monotonic()
                    sent = pending_heartbeats.pop(heartbeat_seq, None)
                    if sent is None or now - sent >= args.gateway_timeout:
                        continue
                    last_ack = now
                    if gateway_state != "ONLINE":
                        gateway_state = "ONLINE"
                        logging.info("Gateway -> ONLINE (confirmare heartbeat primită)")
                    logging.info("Primit HEARTBEAT_ACK heartbeat_seq=%d", heartbeat_seq)

        threads = [
            threading.Thread(target=receive_acks, name="receptie-ACK-A"),
            threading.Thread(target=periodic_worker, args=(2.0, send_data, stop), name="date-A"),
            threading.Thread(target=periodic_worker, args=(5.0, send_heartbeat, stop), name="heartbeat-A"),
        ]
        logging.info("Gateway=%s:%d; sesiune=%s", *gateway, factory.session_id)
        logging.info("Gateway=UNKNOWN; timeout confirmare=%.1f s", args.gateway_timeout)
        try:
            for thread in threads:
                thread.start()
            while not stop.wait(0.5):
                with health_lock:
                    reference = monitor_started if last_ack is None else last_ack
                    elapsed = time.monotonic() - reference
                    if elapsed >= args.gateway_timeout and gateway_state != "OFFLINE":
                        gateway_state = "OFFLINE"
                        logging.warning(
                            "Gateway -> OFFLINE / INDISPONIBIL (fără confirmare de %.1f s)",
                            elapsed,
                        )
        except KeyboardInterrupt:
            logging.info("Oprire senzor A")
        finally:
            stop.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join()


if __name__ == "__main__":
    main()
