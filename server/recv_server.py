from proxy.server import Server


if __name__ == '__main__':
    server = Server('/dev/pts/1')
    # server = Server('/dev/ttyS0')
    server.start_internal()
