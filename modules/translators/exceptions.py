class BaseError(Exception):
    """base error structure class"""

    def __init__(self, val, message):
        self.val = val
        self.message = message
        super().__init__()

    def __str__(self):
        return "{} --> {}".format(self.val, self.message)


class LanguageNotSupportedException(BaseError):
    """exception thrown if the user uses a language that is not supported"""

    def __init__(self, val, message="There is no support for the chosen language"):
        super().__init__(val, message)


class InvalidSourceOrTargetLanguage(BaseError):
    """exception thrown if source and target language are the same"""

    def __init__(self, val, message="source and target language can't be the same"):
        super().__init__(val, message)


class RequestError(Exception):
    """exception thrown if an error occurred during the request call, e.g a connection problem."""

    def __init__(self, message="Request exception can happen due to an api connection error. "
                               "Please check your connection and try again"):
        self.message = message

    def __str__(self):
        return self.message


class TranslatorSetupFailure(Exception):
    pass


class MissingTranslatorParams(Exception):
    pass


class TranslatorNotValid(Exception):
    pass
