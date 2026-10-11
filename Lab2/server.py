import os
import select
import socket
import sys

HOST = "0.0.0.0"
PORT = 5001
MAX_LINE = 4096


def main():
    clients = {}
    next_id = 1
    keyboard = b""

    def disconnect(conn):
        state = clients.pop(conn, None)
        if state is not None:
            print(f"Client disconnected: [{state['id']}] {state['name'] or 'fără nume'}")
            conn.close()

    def queue(conn, message):
        state = clients[conn]
        state["out"] += (message + "\n").encode("utf-8")
        if len(state["out"]) > 65536:
            print("Clientul nu citește răspunsurile; închid conexiunea.")
            disconnect(conn)

    def receive_line(conn, raw):
        state = clients[conn]
        text = raw.decode("utf-8")
        if state["name"] is None:
            name = text.strip()
            if not name or len(name) > 64 or not name.isprintable():
                disconnect(conn)
                return
            state["name"] = name
            queue(conn, f"OK {state['id']}")
            print(f"Client connected: [{state['id']}] {name} ({state['addr'][0]}:{state['addr'][1]})")
            return
        print(f"[{state['id']}] {state['name']}: {text}")
        if text.strip().lower() == "exit":
            disconnect(conn)

    def reply(command):
        if not command.strip():
            return
        if command.strip() == "/clients":
            for state in clients.values():
                print(f"[{state['id']}] {state['name'] or 'în curs de identificare'}")
            if not clients:
                print("Niciun client conectat.")
            return
        registered = {s["id"]: c for c, s in clients.items() if s["name"] is not None and not s["closing"]}
        target, separator, message = command.partition(" ")
        if separator and target.isdecimal():
            conn = registered.get(int(target))
            if conn is None:
                print("Clientul indicat nu este conectat. Folosește /clients.")
                return
            recipients = [conn]
        else:
            recipients = list(registered.values())
            message = command
            if not recipients:
                print("Niciun client conectat.")
                return
        if not message.strip():
            return
        if len(message.encode("utf-8")) > MAX_LINE:
            print(f"Mesaj prea lung (maximum {MAX_LINE} de octeți).")
            return
        for conn in recipients:
            state = clients[conn]
            queue(conn, message)
            print(f"Server → [{state['id']}] {state['name']}: {message}")
            if conn in clients and message.strip().lower() == "exit":
                state["closing"] = True

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()
        server.setblocking(False)
        print(f"Server listening on {HOST}:{PORT}", flush=True)
        print("Mesaj fără număr → toți; <id> <mesaj> → un client. /clients listează clienții; Ctrl+C oprește serverul.", flush=True)
        stdin_open = True
        try:
            while True:
                readers = [server] + [c for c, s in clients.items() if not s["closing"]]
                if stdin_open:
                    readers.append(sys.stdin)
                writers = [c for c, s in clients.items() if s["out"]]
                ready, writable, failed = select.select(readers, writers, list(clients))
                for conn in failed:
                    disconnect(conn)
                for source in ready:
                    if source is server:
                        conn, addr = server.accept()
                        conn.setblocking(False)
                        clients[conn] = {"id": next_id, "addr": addr, "name": None, "in": b"", "out": b"", "closing": False}
                        next_id += 1
                    elif source is sys.stdin:
                        data = os.read(sys.stdin.fileno(), 4096)
                        if not data:
                            stdin_open = False
                        keyboard += data
                        while b"\n" in keyboard:
                            line, keyboard = keyboard.split(b"\n", 1)
                            reply(line.decode("utf-8"))
                    elif source in clients:
                        try:
                            data = source.recv(4096)
                            if not data:
                                disconnect(source)
                                continue
                            state = clients[source]
                            state["in"] += data
                            while source in clients and b"\n" in state["in"]:
                                raw, state["in"] = state["in"].split(b"\n", 1)
                                if len(raw) > MAX_LINE:
                                    disconnect(source)
                                    break
                                receive_line(source, raw)
                            if source in clients and len(state["in"]) > MAX_LINE:
                                disconnect(source)
                        except BlockingIOError:
                            pass
                        except (ConnectionError, UnicodeDecodeError):
                            disconnect(source)
                for conn in writable:
                    if conn not in clients:
                        continue
                    try:
                        state = clients[conn]
                        sent = conn.send(state["out"])
                        state["out"] = state["out"][sent:]
                        if not state["out"] and state["closing"]:
                            disconnect(conn)
                    except BlockingIOError:
                        pass
                    except ConnectionError:
                        disconnect(conn)
        finally:
            for conn in list(clients):
                disconnect(conn)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nServer stopped.")
