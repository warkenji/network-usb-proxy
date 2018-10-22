import socket
import serial
import os
import select
from ipaddress import IPv4Address, IPv6Address

from threading import Thread, current_thread, Lock, main_thread
from uuid import uuid1, uuid3


class Server:
    id = 0
    max_requests = None
    address = None
    threads_stopped = None
    threads_started = None
    port = None
    server_serial = None
    read_lock = None
    write_lock = None
    daemon_threads = True
    buffer_size = 8192  # Always superior to basic SOCKS5 request (515 bytes)
    pipes = None
    external = None

    def __init__(self, serial_port):
        self.server_serial = serial.Serial(serial_port, 9600)
        self.threads_stopped = []
        self.threads_started = []
        self.pipes = {}
        self.read_lock = Lock()
        self.write_lock = Lock()

    def start_external(self):
        self.external = True

        try:
            self.global_process()
        except KeyboardInterrupt:
            self.server_serial.close()

            for thread in self.threads_started + self.threads_stopped:
                thread.join()

    def start_internal(self, address='', port=8081, max_requests=32):
        self.external = False

        self.address = address
        self.port = port
        self.max_requests = max_requests

        try:
            self.global_internal_process()
        except KeyboardInterrupt:
            self.server_serial.close()

            for thread in self.threads_started + self.threads_stopped:
                thread.join()

    def serial_read(self):
        self.read_lock.acquire()
        name = self.server_serial.read(36).decode()
        size = self.server_serial.read_until()

        data = size + self.server_serial.read(int(size[:-1]))

        self.read_lock.release()

        return name, data

    def serial_write(self, name, data):
        self.write_lock.acquire()
        self.server_serial.write(name.encode())
        self.server_serial.write(str(len(data)).encode() + b"\n")

        self.server_serial.write(data)

        self.write_lock.release()

    def global_process(self):
        try:
            while True:
                try:
                    name, data = self.serial_read()
                    size = int(data.split(b'\n', 1)[0])

                    if name not in self.pipes and self.external and size:
                        r, w = os.pipe()

                        self.pipes[name] = {"r": r, "w": w}

                        thread = Thread(target=self.local_external_request, args=(name,), daemon=Server.daemon_threads)
                        thread.start()
                        self.threads_started.append(thread)

                    if name in self.pipes:
                        os.write(self.pipes[name]["w"], data)

                except OSError as e:
                    print(e)

                for thread_stopped in self.threads_stopped:
                    thread_stopped.join()

                self.threads_stopped.clear()

        except KeyboardInterrupt:
            pass

    def global_internal_process(self):
        server_socket = socket.socket()
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.address, self.port))
        server_socket.listen(self.max_requests)

        thread = Thread(target=self.global_process, daemon=Server.daemon_threads)
        thread.start()

        self.threads_started.append(thread)

        while True:
            (client_socket, client_address) = server_socket.accept()
            thread = Thread(target=self.local_internal_process, args=(client_socket,), daemon=Server.daemon_threads)
            thread.start()
            self.threads_started.append(thread)

            for thread_stopped in self.threads_stopped:
                thread_stopped.join()

            self.threads_stopped.clear()

    def local_external_request(self, name):
        try:
            Server.id += 1
            process_id = Server.id
            print("Process n°{}: Connected".format(process_id))

            pipe = self.pipes[name]
            fd_r = pipe["r"]
            fd_w = pipe["w"]
            result = self.external_request(fd_r, name)

            if result:
                print("Process n°{}: Request Accepted".format(process_id))

                command_code, s, address = result

                if command_code == b'\x01':
                    conns = [fd_r, s]
                    close_connection = False

                    while not close_connection:
                        rlist, wlist, xlist = select.select(conns, [], conns)

                        if xlist or not rlist:
                            close_connection = True
                            self.serial_write(name, b'')
                        else:
                            for r in rlist:
                                if r == fd_r:
                                    raw_data = os.read(fd_r, Server.buffer_size + len(str(Server.buffer_size)) + 1)
                                    size, data = raw_data.split(b'\n', 1)
                                    s.sendall(data)

                                    if not int(size):
                                        close_connection = True

                                else:
                                    data = r.recv(Server.buffer_size)
                                    self.serial_write(name, data)

                                    if not data:
                                        close_connection = True
                                        self.serial_write(name, b'')

                elif command_code == b'\x03':
                    conns = [fd_r]
                    close_connection = False

                    while not close_connection:
                        rlist, wlist, xlist = select.select(conns, [], conns)

                        if xlist or not rlist:
                            close_connection = True
                        else:
                            raw_data = os.read(fd_r, Server.buffer_size + len(str(Server.buffer_size)) + 1)
                            size, data = raw_data.split(b'\n', 1)
                            s.sendto(data, address)

                            if not int(size):
                                close_connection = True

                s.close()

            else:
                print("Process n°{}: Request Refused".format(process_id))

            try:
                del(self.pipes[name])
                os.close(fd_r)
                os.close(fd_w)
            except KeyError:
                pass

            Server.id -= 1
            self.threads_stopped.append(current_thread())
            print("Process n°{}: Disconnected".format(process_id))
        except (KeyboardInterrupt, KeyError) as e:
            print(e)
            self.threads_stopped.append(current_thread())

    def local_internal_process(self, client):
        try:
            Server.id += 1
            process_id = Server.id

            name = str(uuid3(uuid1(), "{}:{}".format(client, process_id)))

            fd_r, fd_w = os.pipe()
            self.pipes[name] = {'r': fd_r, 'w': fd_w}

            print("Process n°{}: Connected".format(process_id))
            if self.internal_request(client):
                print("Process n°{}: Authenticated".format(process_id))

                conns = [client, fd_r]
                close_connection = False

                while not close_connection:
                    rlist, wlist, xlist = select.select(conns, [], conns)
                    if xlist or not rlist:
                        close_connection = True
                        self.serial_write(name, b'')
                    else:
                        for r in rlist:
                            if r == client:
                                data = r.recv(Server.buffer_size)
                                self.serial_write(name, data)

                                if not data:
                                    close_connection = True
                                    self.serial_write(name, b'')

                            else:
                                raw_data = os.read(fd_r, Server.buffer_size + len(str(Server.buffer_size)) + 1)
                                size, data = raw_data.split(b'\n', 1)
                                client.sendall(data)

                                if not int(size):
                                    close_connection = True

            else:
                print("Process n°{}: Authentication not allowed".format(process_id))

            client.close()

            try:
                del(self.pipes[name])
                os.close(fd_r)
                os.close(fd_w)
            except KeyError:
                pass

            self.threads_stopped.append(current_thread())

            Server.id -= 1
            print("Process n°{}: Disconnected".format(process_id))
        except (KeyboardInterrupt, KeyError):
            self.threads_stopped.append(current_thread())

    def internal_request(self, client):
        version = client.recv(1)
        nb_auth = client.recv(1)
        auths = client.recv(int.from_bytes(nb_auth, 'big'))

        if version == b'\x05':
            if b'\x00' in auths:
                client.sendall(b"\x05\x00")
                return True

        client.sendall(b"\x05\xFF")
        return False

    # TODO send connection information to serial port
    def external_request(self, fd_r, name):
        current_byte = b''

        while current_byte != b'\n':
            current_byte = os.read(fd_r, 1)

        version = os.read(fd_r, 1)

        if version != b'\x05':
            self.serial_write(name, b'\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        command_code = os.read(fd_r, 1)

        if command_code != b'\x01' and command_code != b'\x03':
            self.serial_write(name, b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        reserved = os.read(fd_r, 1)

        if reserved != b'\x00':
            self.serial_write(name, b'\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        address_type = os.read(fd_r, 1)

        if address_type == b'\x01':
            destination_address = str(IPv4Address(os.read(fd_r, 4)))

        elif address_type == b'\x03':
            destination_address_length = int.from_bytes(os.read(fd_r, 1), 'big')
            destination_address = os.read(fd_r, destination_address_length).decode()

        elif address_type == b'\x04':
            destination_address = str(IPv6Address(os.read(fd_r, 16)))

        else:
            self.serial_write(name, b'\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        port = int.from_bytes(os.read(fd_r, 2), 'big')

        address = (destination_address, port)

        try:
            if command_code == b'\x01':
                if address_type == b'\x01' or address_type == b'\x03':
                    s = socket.socket()

                else:
                    s = socket.socket(socket.AF_INET6)

                s.connect(address)
            else:
                if address_type == b'\x01' or address_type == b'\x03':
                    s = socket.socket(type=socket.SOCK_DGRAM)

                else:
                    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

        except socket.error:
            self.serial_write(name, b'\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        self.serial_write(name, b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        return command_code, s, address
