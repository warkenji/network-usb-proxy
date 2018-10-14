class Headers:
    request_line = None
    headers = None
    headers_encoded = None

    def __init__(self, str_headers):
        self.headers = {}
        lines = str_headers.split('\r\n')
        nb_lines = len(lines)
        self.headers_encoded = str_headers.encode()

        if nb_lines > 0:
            self.request_line = lines[0]

            if nb_lines > 1:
                for line in lines[1:]:
                    pos_sep = line.find(':')
                    name = line[:pos_sep].strip()
                    value = line[pos_sep + 1:].strip()

                    self.headers[name] = value

    def __str__(self):
        return self.headers_encoded.decode()
