import argparse
import os
import select
import socket
import sys

HOST = "127.0.0.1"
PORT = 5001
MAX_LINE = 4096


def valid_name(value):
    name = value.strip()
    if not name or len(name) > 64 or not name.isprintable():
        raise argparse.ArgumentTypeError("Numele trebuie să aibă 1–64 de caractere vizibile.")
    return name


def main(name):
    name = valid_name(name)
    registered = False
    keyboard = b""
    incoming = b""
    outgoing = (name + "\n").encode("utf-8")
    exiting = False

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.connect((HOST, PORT))
        client.setblocking(False)
        print(f"Se confirmă numele {name}... (poți scrie exit)", flush=True)
        while True:
            readers = [client] if exiting else [client, sys.stdin]
            ready, writable, _ = select.select(readers, [client] if outgoing else [], [])
            if client in ready:
                data = client.recv(4096)
                if not data:
                    print("\nServer disconnected.")
                    return
                incoming += data
                while b"\n" in incoming:
                    raw, incoming = incoming.split(b"\n", 1)
                    if len(raw) > MAX_LINE:
                        raise ValueError("Răspuns prea lung.")
                    message = raw.decode("utf-8")
                    if not registered:
                        if not message.startswith("OK "):
                            raise ValueError("Serverul nu a confirmat numele.")
                        registered = True
                        print(f"Connected to {HOST}:{PORT} as [{message[3:]}] {name}")
                        print("Scrie mesaje sau exit pentru deconectare.")
                    else:
                        print(f"\nServer: {message}")
                        if message.strip().lower() == "exit":
                            return
                    if not exiting:
                        print(f"{name}: ", end="", flush=True)
                if len(incoming) > MAX_LINE:
                    raise ValueError("Răspuns prea lung.")
            if sys.stdin in ready:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    return
                keyboard += data
                while b"\n" in keyboard:
                    raw, keyboard = keyboard.split(b"\n", 1)
                    message = raw.decode("utf-8")
                    if message.strip().lower() == "exit":
                        outgoing += b"exit\n"
                        exiting = True
                        break
                    if not registered:
                        print("Așteaptă confirmarea numelui sau scrie exit.")
                        continue
                    if not message.strip():
                        print(f"{name}: ", end="", flush=True)
                        continue
                    if len(raw) > MAX_LINE:
                        print(f"Mesaj prea lung (maximum {MAX_LINE} de octeți).")
                        continue
                    outgoing += raw + b"\n"
                    print(f"{name}: ", end="", flush=True)
                if len(outgoing) > 65536:
                    raise ValueError("Prea multe mesaje în așteptare.")
            if client in writable:
                sent = client.send(outgoing)
                outgoing = outgoing[sent:]
            if exiting and not outgoing:
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Client TCP cu nume dat la pornire.")
    parser.add_argument("name", type=valid_name, metavar="nume", help='numele clientului (exemplu: Razvi sau "Razvi Pop")')
    args = parser.parse_args()
    try:
        main(args.name)
    except (KeyboardInterrupt, ConnectionError, OSError, UnicodeDecodeError, ValueError) as error:
        if not isinstance(error, KeyboardInterrupt):
            print(f"\nConexiune închisă: {error}")
    finally:
        print("\nClient disconnected.")
