from .proxy.server import Server


if __name__ == '__main__':
    server = Server()
    server.start(False)