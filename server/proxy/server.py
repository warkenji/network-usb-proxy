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

    def __init__(self, serial_port):
        self.server_serial = serial.Serial(serial_port)
        self.threads_stopped = []
        self.threads_started = []
        self.pipes = {}
        self.read_lock = Lock()
        self.write_lock = Lock()

    @staticmethod
    def load_headers(raw_data, str_headers=""):
        content_separation = "\r\n\r\n"
        data = raw_data.decode()

        str_headers += data
        loaded = str_headers.find(content_separation) == -1 and len(raw_data) > 0

        return loaded, str_headers

    @staticmethod
    def create_headers(code=200, message='OK'):
        weekdayname = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

        monthname = [None,
                     'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

        timestamp = time.time()

        year, month, day, hh, mm, ss, wd, y, z = time.gmtime(timestamp)
        timestamp_server = "{}, {:02d} {:3s} {:4d} {:02d}:{:02d}:{:02d} GMT".format(
            weekdayname[wd],
            day, monthname[month], year,
            hh, mm, ss)

        str_headers = "{} {} {}\r\nServer: {}\r\nDate: {}\r\n\r\n".format(Server.protocol, code, message,
                                                                          Server.server_version, timestamp_server)

        return Headers(str_headers)

    def start_send(self):
        try:
            self.process_send_broadcast()
        except KeyboardInterrupt:
            self.server_serial.close()

            for thread in self.threads_started + self.threads_stopped:
                thread.join()

    def start_recv(self, address='', port=8081):
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

    def pipe_write(self, fd_w, data):
        os.write(fd_w, data)

    def process_broadcast(self, pipe_type):

        while True:
            try:
                name, data = self.serial_read()
                if name not in self.pipes and pipe_type == "send":
                    r, w = os.pipe()

                    self.pipes[name] = {"r": r, "w": w}

                    thread = Thread(target=self.process_send, args=(name,), daemon=Server.daemon_threads)
                    thread.start()
                    self.threads_started.append(thread)

                if name in self.pipes:
                    self.pipe_write(self.pipes[name]["w"], data)
                    """if pipe_type == "send":
                        thread = Thread(target=self.pipe_write, args=(self.pipes[name]["w"], data), daemon=Server.daemon_threads)
                        thread.start()
                        self.threads_started.append(thread)

                    else:
                        os.write(self.pipes[name]["w"], data)"""

            except OSError as e:
                print(e)

            for thread_stopped in self.threads_stopped:
                thread_stopped.join()

            self.threads_stopped.clear()

    def process_send_broadcast(self):
        self.process_broadcast("send")

    def process_recv_broadcast(self):
        max_users = 32
        server_socket = socket.socket()
        server_socket.bind((self.address, self.port))

        thread = Thread(target=self.process_broadcast, args=("recv",), daemon=Server.daemon_threads)
        thread.start()

        self.threads_started.append(thread)

        while True:
            server_socket.listen(max_users)
            (client_socket, client_address) = server_socket.accept()
            thread = Thread(target=self.process_recv, args=(client_socket,), daemon=Server.daemon_threads)
            thread.start()
            self.threads_started.append(thread)

            for thread_stopped in self.threads_stopped:
                thread_stopped.join()

            self.threads_stopped.clear()

    def process_send(self, name):
        Server.id += 1
        process_id = Server.id
        print("Process n°{}: started".format(process_id))

        headers, client_socket = self.headers_send(name)

        if headers is not None:

            print(headers.request_line)

            str_headers_response = b""

            if headers.request_line.startswith("CONNECT"):
                headers = self.create_headers(message="Connection Established")
                self.serial_write(name, headers.headers_encoded)
                str_headers_response = headers.headers_encoded

            else:
                client_socket.send(headers.headers_encoded)

            fd_r = self.pipes[name]["r"]
            conns = [fd_r, client_socket]
            close_connection = False

            while not close_connection:
                rlist, wlist, xlist = select.select(conns, [], conns, Server.timeout)

                if xlist or not rlist:
                    close_connection = True
                else:
                    for r in rlist:
                        if r == fd_r:
                            print("send:", headers.request_line)

                            data = os.read(fd_r, Server.buffer_size)
                            client_socket.sendall(data)
                        else:
                            print("receive:", headers.request_line)

                            data = r.recv(Server.buffer_size)
                            self.serial_write(name, data)

                            if str_headers_response.find(b"\r\n\r\n") == -1:
                                str_headers_response += data

                        if not data:
                            close_connection = True

            headers_response = Headers(str_headers_response[:str_headers_response.find(b"\r\n\r\n") + 4].decode())
            print(headers_response.request_line)

            client_socket.close()

        pipe = self.pipes[name]
        del(self.pipes[name])
        os.close(pipe["r"])
        os.close(pipe["w"])

        Server.id -= 1
        print("Process n°{}: stopped".format(process_id))

    def process_recv(self, client_socket):
        Server.id += 1
        process_id = Server.id
        print("Process n°{}: started".format(process_id))

        headers, name = self.headers_recv(client_socket)

        if headers is not None:
            print(headers.request_line)

            try:
                self.serial_write(name, headers.headers_encoded)
                fd_r = self.pipes[name]["r"]
                conns = [client_socket, fd_r]
                close_connection = False

                while not close_connection:
                    rlist, wlist, xlist = select.select(conns, [], conns, Server.timeout)
                    if xlist or not rlist:
                        close_connection = True
                    else:
                        for r in rlist:
                            if r == client_socket:
                                print("send:", headers.request_line)

                                data = r.recv(Server.buffer_size)
                                self.serial_write(name, data)

                            else:
                                print("receive:", headers.request_line)

                                data = os.read(fd_r, Server.buffer_size)
                                client_socket.sendall(data)

                            if not data:
                                close_connection = True

            except OSError:
                headers = self.create_headers(404, 'Not Found')
                print("{}{}{}".format(BColors.FAIL, headers.request_line, BColors.ENDC))

        client_socket.close()

        pipe = self.pipes[name]
        del (self.pipes[name])
        os.close(pipe["r"])
        os.close(pipe["w"])

        Server.id -= 1
        print("Process n°{}: stopped".format(process_id))
        self.threads_stopped.append(current_thread())

    def headers_send(self, name):
        str_headers = ""
        check = True

        while check:
            raw_data = os.read(self.pipes[name]["r"], 1)
            check, str_headers = Server.load_headers(raw_data, str_headers)

        if len(str_headers) > 0 is not None:
            headers = Headers(str_headers)

            host = headers.headers.get("Host", "")
            pos_sep = host.find(":")

            if pos_sep == -1:
                pos_sep = len(host)
                host += ":80"

            remote_address = host[:pos_sep]
            remote_port = int(host[pos_sep + 1:])

            try:
                client_socket = socket.create_connection((remote_address, remote_port))
            except (socket.gaierror, OSError):
                if headers is not None and headers.request_line.startswith('CONNECT'):
                    headers = self.create_headers(502, 'Bad Gateway')
                else:
                    headers = self.create_headers(404, 'Not Found')

                print("{}{}{}".format(BColors.FAIL, headers.request_line, BColors.ENDC))

                pipe = self.pipes[name]
                del (self.pipes[name])
                os.close(pipe["r"])
                os.close(pipe["w"])

                self.serial_write(name, headers.request_line.encode() + b"\r\n\r\n")

                return None, None

            return headers, client_socket

        headers = self.create_headers(404, 'Not Found')
        print("{}{}{}".format(BColors.FAIL, headers.request_line, BColors.ENDC))

        pipe = self.pipes[name]
        del (self.pipes[name])
        os.close(pipe["r"])
        os.close(pipe["w"])

        self.serial_write(name, headers.request_line.encode() + b"\r\n\r\n")

        return None, None

    def headers_recv(self, client_socket):
        str_headers = ""
        check = True

        while check:
            raw_data = client_socket.recv(1)
            check, str_headers = Server.load_headers(raw_data, str_headers)

        if len(str_headers) > 0 is not None:
            headers = Headers(str_headers)
            name = str(uuid3(uuid1(), "{}{}".format(headers.request_line, client_socket)))

            r, w = os.pipe()
            self.pipes[name] = {'r': r, 'w': w}

            return headers, name

        headers = self.create_headers(404, 'Not Found')
        print("{}{}{}".format(BColors.FAIL, headers.request_line, BColors.ENDC))

        client_socket.send(headers.request_line.encode() + b"\r\n\r\n")

        return None, None
