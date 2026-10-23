NAME = "factory"
VERSION = "0.4.0"


class FactoryError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.message = message
        self.code = code
