from watchdog.events import FileSystemEventHandler
from threading import Thread
from ntpath import basename


class FileCreationHandler(FileSystemEventHandler):
    handler = None
    prefix = None

    def __init__(self, handler, prefix):
        self.handler = handler
        self.prefix = prefix

    def on_created(self, event):
        filename = basename(event.src_path)
        if not event.is_directory and filename[:len(self.prefix)] == self.prefix:
            thread = Thread(target=self.handler, args=(event.src_path,))
            thread.start()