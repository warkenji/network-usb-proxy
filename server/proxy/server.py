import socket
import sys
import time
import os
import select

from .bcolors import BColors
from .request import Headers
from threading import Thread, current_thread
from uuid import uuid1, uuid3


class Server:
    id = 0
    address = None
    thread_stopped = None
    thread_started = None
    port = None
    timeout = 5
    fd_fifo = None
    fifo_path = None
    daemon_threads = True
    recv_prefix = "recv"
    send_prefix = "send"
    current_dir = 'tmp'
    buffer_size = None
    protocol = "HTTP/1.1"
    server_version = "BaseHTTP/0.1"

    def __init__(self, address='', port=8081, buffer_size=8192):
        self.address = address
        self.port = port
        self.buffer_size = buffer_size
        self.thread_stopped = []
        self.thread_started = []
        self.fifo_path = "{}{}{}".format(Server.current_dir, os.path.sep, "mkfifo")

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

    def start(self, internal_process=True):
        try:
            os.mkfifo(self.fifo_path)
            os.system("chmod 0666 {}".format(self.fifo_path))
        except OSError as e:
            print("{}{}{}".format(BColors.FAIL, e, BColors.ENDC))

        try:
            if internal_process:
                self.process_recv_broadcast()
            else:
                self.process_send_broadcast()
        except KeyboardInterrupt:

            for thread in self.thread_started + self.thread_stopped:
                thread.join()

    def process_send_broadcast(self):
        fd = os.open(self.fifo_path, os.O_RDONLY)
        filenames = b""

        while True:
            filenames += os.read(fd, self.buffer_size)
            pos = filenames.find(b'\0')

            while pos != -1:
                filename = filenames[:pos].decode()
                filenames = filenames[pos + 1:]
                pos = filenames.find(b'\0')
                thread = Thread(target=self.process_send, args=(filename,), daemon=Server.daemon_threads)
                thread.start()
                self.thread_started.append(thread)

                for thread_stopped in self.thread_stopped:
                    thread_stopped.join()

                self.thread_stopped.clear()

    def process_recv_broadcast(self):
        max_users = 32
        server_socket = socket.socket()
        server_socket.bind((self.address, self.port))

        if not sys.platform.startswith("cygwin"):
            os.system("rm -f {}{}*_*".format(Server.current_dir, os.path.sep))

        self.fd_fifo = os.open(self.fifo_path, os.O_WRONLY | os.O_SYNC)

        while True:
            server_socket.listen(max_users)
            (client_socket, client_address) = server_socket.accept()
            thread = Thread(target=self.process_recv, args=(client_socket,), daemon=Server.daemon_threads)
            thread.start()
            self.thread_started.append(thread)

            for thread_stopped in self.thread_stopped:
                thread_stopped.join()

            self.thread_stopped.clear()

    def process_send(self, filename):
        Server.id += 1
        process_id = Server.id
        print("Process n°{}: started".format(process_id))

        headers, client_socket, fd_send, fd_recv = self.headers_send(filename)

        if headers is not None:
            if headers.request_line.startswith("CONNECT"):
                headers = self.create_headers(message="Connection Established")

                os.write(fd_recv, headers.headers_encoded)

            else:
                client_socket.send(headers.headers_encoded)

            conns = [fd_send, client_socket]
            close_connection = False

            while not close_connection:
                rlist, wlist, xlist = select.select(conns, [], conns, Server.timeout)
                if xlist or not rlist:
                    break
                for r in rlist:
                    w = conns[1] if r is conns[0] else conns[0]

                    if r == conns[0]:
                        data = os.read(fd_send, self.buffer_size)
                        w.sendall(data)
                    else:
                        data = r.recv(self.buffer_size)
                        os.write(fd_recv, data)

                    if not data:
                        close_connection = True

            os.close(fd_send)
            os.close(fd_recv)
            client_socket.close()

        path = "{}{}{}{}".format(Server.current_dir, os.path.sep, '{}_'.format(Server.send_prefix), filename)
        os.unlink(path)

        Server.id -= 1
        print("Process n°{}: stopped".format(process_id))

    def process_recv(self, client_socket):
        Server.id += 1
        process_id = Server.id
        print("Process n°{}: started".format(process_id))

        headers, filename, fd_send, fd_recv = self.headers_recv(client_socket)

        if headers is not None:
            try:
                conns = [client_socket, fd_recv]
                close_connection = False

                while not close_connection:
                    rlist, wlist, xlist = select.select(conns, [], conns, Server.timeout)
                    if xlist or not rlist:
                        break
                    for r in rlist:
                        w = conns[1] if r is conns[0] else conns[0]

                        if r == conns[0]:
                            data = r.recv(self.buffer_size)
                            os.write(fd_send, data)
                        else:
                            data = os.read(fd_recv, self.buffer_size)
                            w.sendall(data)

                        if not data:
                            close_connection = True
            except BrokenPipeError:
                headers = self.create_headers(404, 'Not Found')
                print("{}{}{}".format(BColors.FAIL, headers, BColors.ENDC))

            os.close(fd_send)
            os.close(fd_recv)

        client_socket.close()

        path = "{}{}{}{}".format(Server.current_dir, os.path.sep, '{}_'.format(Server.recv_prefix), filename)
        os.unlink(path)

        Server.id -= 1
        print("Process n°{}: stopped".format(process_id))
        self.thread_stopped.append(current_thread())

    def headers_send(self, filename):
        str_headers = ""
        check = True
        prefix = '{}_'.format(Server.send_prefix)
        path = "{}{}{}{}".format(Server.current_dir, os.path.sep, prefix, filename)
        fd_send = os.open(path, os.O_RDONLY)

        while check:
            raw_data = os.read(fd_send, 1)
            check, str_headers = Server.load_headers(raw_data, str_headers)

        prefix = '{}_'.format(Server.recv_prefix)
        path = "{}{}{}{}".format(Server.current_dir, os.path.sep, prefix, filename)
        fd_recv = os.open(path, os.O_WRONLY)

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
                    os.write(fd_recv, headers.headers_encoded)
                else:
                    headers = self.create_headers(404, 'Not Found')

                print("{}{}{}".format(BColors.FAIL, headers.request_line, BColors.ENDC))

                os.write(fd_recv, headers.request_line.encode() + b"\r\n\r\n")
                os.close(fd_send)
                os.close(fd_recv)

                return None, None, None, None

            print(headers.request_line)

            return headers, client_socket, fd_send, fd_recv

        headers = self.create_headers(404, 'Not Found')
        print("{}{}{}".format(BColors.FAIL, headers.request_line, BColors.ENDC))

        os.write(fd_recv, headers.request_line.encode() + b"\r\n\r\n")
        os.close(fd_send)
        os.close(fd_recv)

        return None, None, None, None

    def headers_recv(self, client_socket):
        str_headers = ""
        check = True

        while check:
            raw_data = client_socket.recv(1)
            check, str_headers = Server.load_headers(raw_data, str_headers)

        if len(str_headers) > 0 is not None:
            headers = Headers(str_headers)
            filename = str(uuid3(uuid1(), "{}{}".format(headers.request_line, client_socket)))

            os.write(self.fd_fifo, filename.encode() + b"\0")

            send_prefix = '{}_'.format(Server.send_prefix)
            recv_prefix = '{}_'.format(Server.recv_prefix)

            path = "{}{}{}{}".format(Server.current_dir, os.path.sep, send_prefix, filename)
            os.mkfifo(path)
            os.system("chmod 0666 {}".format(path))

            path = "{}{}{}{}".format(Server.current_dir, os.path.sep, recv_prefix, filename)
            os.mkfifo(path)
            os.system("chmod 0666 {}".format(path))

            path = "{}{}{}{}".format(Server.current_dir, os.path.sep, send_prefix, filename)
            fd_send = os.open(path, os.O_WRONLY)

            os.write(fd_send, headers.headers_encoded)

            path = "{}{}{}{}".format(Server.current_dir, os.path.sep, recv_prefix, filename)
            fd_recv = os.open(path, os.O_RDONLY)

            print(headers.request_line)

            return headers, filename, fd_send, fd_recv

        headers = self.create_headers(404, 'Not Found')
        print("{}{}{}".format(BColors.FAIL, headers.request_line, BColors.ENDC))

        client_socket.send(headers.request_line.encode() + b"\r\n\r\n")

        return None, None, None, None
