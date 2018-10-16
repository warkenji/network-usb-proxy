from proxy.server import Server


if __name__ == '__main__':
    server = Server('/dev/pts/2')
    server.start_send()
