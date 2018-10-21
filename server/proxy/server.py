import socket
import serial
import time
import os
import select

from .bcolors import BColors
from .request import Headers
from threading import Thread, current_thread, Lock
from uuid import uuid1, uuid3


class Server:
    id = 0
    address = None
    threads_stopped = None
    threads_started = None
    port = None
    timeout = None
    server_serial = None
    read_lock = None
    write_lock = None
    daemon_threads = True
    buffer_size = 8192
    protocol = "HTTP/1.1"
    server_version = "BaseHTTP/0.1"
    pipes = None
    pipe_type = None

    def __init__(self, serial_port):
        self.server_serial = serial.Serial(serial_port, 9600)
        self.threads_stopped = []
        self.threads_started = []
        self.pipes = {}
        self.read_lock = Lock()
        self.write_lock = Lock()

    def start_send(self):
        self.pipe_type = "send"

        try:
            self.process_send_broadcast()
        except KeyboardInterrupt:
            self.server_serial.close()

            for thread in self.threads_started + self.threads_stopped:
                thread.join()

    def start_recv(self, address='', port=8081):
        self.pipe_type = "recv"
        self.address = address
        self.port = port

        try:
            self.process_recv_broadcast()
        except KeyboardInterrupt:
            self.server_serial.close()

            for thread in self.threads_started + self.threads_stopped:
                thread.join()

    def serial_read(self):
        self.read_lock.acquire()
        name = self.server_serial.read(36).decode()
        size = int(self.server_serial.read_until()[:-1])

        data = self.server_serial.read(size)

        self.read_lock.release()

        return name, data

    def serial_write(self, name, data):
        self.write_lock.acquire()
        self.server_serial.write(name.encode())
        self.server_serial.write(str(len(data)).encode() + b"\n")

        self.server_serial.write(data)

        self.write_lock.release()

    def process_broadcast(self):
        try:
            while True:
                try:
                    name, data = self.serial_read()
                    if name not in self.pipes and self.pipe_type == "send" and len(data) > 0:
                        r, w = os.pipe()

                        self.pipes[name] = {"r": r, "w": w}

                        thread = Thread(target=self.process_send, args=(name,), daemon=Server.daemon_threads)
                        thread.start()
                        self.threads_started.append(thread)

                    print(name, len(data))
                    if name in self.pipes:
                        pipe = self.pipes[name]
                        if len(data) > 0:
                            os.write(pipe["w"], data)
                        else:
                            try:
                                del (self.pipes[name])
                                os.close(pipe['r'])
                                os.close(pipe['w'])
                                print("closed")
                            except KeyError:
                                pass

                except OSError as e:
                    print(e)

                for thread_stopped in self.threads_stopped:
                    thread_stopped.join()

                self.threads_stopped.clear()

        except KeyboardInterrupt:
            pass

    def process_send_broadcast(self):
        self.process_broadcast()

    def process_recv_broadcast(self):
        max_users = 32
        server_socket = socket.socket()
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.address, self.port))
        server_socket.listen(max_users)

        thread = Thread(target=self.process_broadcast, daemon=Server.daemon_threads)
        thread.start()

        self.threads_started.append(thread)

        while True:
            (client_socket, client_address) = server_socket.accept()
            thread = Thread(target=self.process_recv, args=(client_socket,), daemon=Server.daemon_threads)
            thread.start()
            self.threads_started.append(thread)

            for thread_stopped in self.threads_stopped:
                thread_stopped.join()

            self.threads_stopped.clear()

    def process_send(self, name):
        try:
            Server.id += 1
            process_id = Server.id
            print("Process n°{}: started".format(process_id))

            pipe = self.pipes[name]
            fd_r = pipe["r"]
            fd_w = pipe["w"]
            result = self.req_2(fd_r, name)

            if result:
                command_code, s, address = result
                if command_code == b'\x01':
                    conns = [fd_r, s]
                    close_connection = False

                    while not close_connection:
                        rlist, wlist, xlist = select.select(conns, [], conns, Server.timeout)

                        if xlist or not rlist:
                            print("test fin 1 1")
                            close_connection = True
                        else:
                            print("test continue")
                            for r in rlist:
                                if r == fd_r:
                                    data = os.read(fd_r, Server.buffer_size)
                                    s.sendall(data)
                                else:
                                    data = r.recv(Server.buffer_size)
                                    self.serial_write(name, data)

                                if not data:
                                    print("test fin 1 2")
                                    close_connection = True

                elif command_code == b'\x02':
                    pass

                elif command_code == b'\x03':
                    conns = [fd_r]
                    close_connection = False

                    while not close_connection:
                        rlist, wlist, xlist = select.select(conns, [], conns, Server.timeout)

                        if xlist or not rlist:
                            close_connection = True
                        else:
                            data = os.read(fd_r, Server.buffer_size)
                            s.sendto(data, address)

                            if not data:
                                close_connection = True

                print("test fin 2")
                s.close()
                self.serial_write(name, b'')
            print("test fin 3")

            try:
                del(self.pipes[name])
                os.close(fd_r)
                os.close(fd_w)
            except KeyError:
                pass

            Server.id -= 1
            self.threads_stopped.append(current_thread())
            print("Process n°{}: stopped".format(process_id))
        except (KeyboardInterrupt, KeyError) as e:
            print(e)
            self.threads_stopped.append(current_thread())

    def process_recv(self, client):
        try:
            Server.id += 1
            process_id = Server.id

            name = str(uuid3(uuid1(), "{}:{}".format(client, process_id)))

            fd_r, fd_w = os.pipe()
            self.pipes[name] = {'r': fd_r, 'w': fd_w}

            print("Process n°{}: started".format(process_id))
            if self.req_1(client):
                print("Process n°{}:".format(process_id), "Request Accepted")

                try:
                    conns = [client, fd_r]
                    close_connection = False

                    while not close_connection:
                        rlist, wlist, xlist = select.select(conns, [], conns)
                        if xlist or not rlist:
                            close_connection = True
                        else:
                            for r in rlist:
                                if r == client:
                                    data = r.recv(Server.buffer_size)
                                    self.serial_write(name, data)

                                else:
                                    data = os.read(fd_r, Server.buffer_size)
                                    client.sendall(data)

                                if not data:
                                    close_connection = True

                except OSError as e:
                    print("{}Process n°{}: {}{}".format(BColors.FAIL, process_id, e, BColors.ENDC))

            client.close()
            self.serial_write(name, b'')

            try:
                del(self.pipes[name])
                os.close(fd_r)
                os.close(fd_w)
            except KeyError:
                pass

            self.threads_stopped.append(current_thread())

            Server.id -= 1
            print("Process n°{}: stopped".format(process_id))
        except (KeyboardInterrupt, KeyError):
            self.threads_stopped.append(current_thread())

    def req_1(self, client):
        version = client.recv(1)
        nb_auth = client.recv(1)
        auths = client.recv(int.from_bytes(nb_auth, 'big'))

        if version == b'\x05':
            if b'\x00' in auths:
                client.sendall(b"\x05\x00")
                return True

        client.sendall(b"\x05\xFF")
        return False

    def req_2(self, fd_r, name):
        version = os.read(fd_r, 1)

        if version != b'\x05':
            self.serial_write(name, b'\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        command_code = os.read(fd_r, 1)

        if not (0 < int.from_bytes(command_code, 'big') < 4):
            self.serial_write(name, b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        reserved = os.read(fd_r, 1)

        if reserved != b'\x00':
            self.serial_write(name, b'\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        address_type = os.read(fd_r, 1)

        if address_type == b'\x01':
            address = os.read(fd_r, 4)
            destination_address = ''

            for i in range(len(address)):
                if i > 0:
                    destination_address += '.'

                destination_address += str(address[i])

        elif address_type == b'\x02':
            destination_address_length = int.from_bytes(os.read(fd_r, 1), 'big')
            destination_address = str(os.read(fd_r, destination_address_length))

        elif address_type == b'\x03':
            address = os.read(fd_r, 16)
            destination_address = ''

            for i in range(0, len(address), 2):
                if i > 0:
                    destination_address += ':'

                destination_address += str(int.from_bytes(address[i:i + 1], 'big'))

        else:
            self.serial_write(name, b'\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        port = int.from_bytes(os.read(fd_r, 2), 'big')

        address = (destination_address, port)

        try:
            if command_code == b'\x01':
                if address_type == b'\x01' or address_type == b'\x02':
                    s = socket.socket()

                else:
                    s = socket.socket(socket.AF_INET6)

                s.connect(address)
            elif command_code == b'\x02':
                if address_type == b'\x01' or address_type == b'\x02':
                    s = socket.socket()

                else:
                    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

                s.bind(address)
            else:
                if address_type == b'\x01' or address_type == b'\x02':
                    s = socket.socket(type=socket.SOCK_DGRAM)

                else:
                    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

        except socket.error:
            self.serial_write(name, b'\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00')
            return None

        self.serial_write(name, b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        return command_code, s, address
