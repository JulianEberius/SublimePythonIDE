import SublimePythonIDE.pyflakes
import SublimePythonIDE.pyflakes.messages


SublimePythonIDE.pyflakes.messages.Message.__str__ = (
    lambda self: self.message % self.message_args
)


class PyflakesLoc:
    """ Error location data for pyflakes.

    pyflakes 0.7 wants loc as {lineno, col_offset} object
    we ducktype it here. Apparently AST code
    has been upgraded in some point?

    Online lineno attribute is required.
    """

    def __init__(self, lineno):
        self.lineno = lineno


class PythonLintError(SublimePythonIDE.pyflakes.messages.Message):

    def __init__(
        self, filename, loc, level, message,
            message_args, offset=0, text=None):

        super(PythonLintError, self).__init__(filename, PyflakesLoc(loc))
        self.level = level
        self.message = message
        self.message_args = message_args
        self.offset = offset
        if text is not None:
            self.text = text


class Pep8Error(PythonLintError):

    def __init__(self, filename, loc, offset, code, text):
        # PEP 8 Errors are downgraded to "warnings"
        super(Pep8Error, self).__init__(
            filename, loc, 'W', '[W] PEP 8 (%s): %s',
            (code, text), offset=offset, text=text
        )


class Pep8Warning(PythonLintError):

    def __init__(self, filename, loc, offset, code, text):
        # PEP 8 Warnings are downgraded to "violations"
        super(Pep8Warning, self).__init__(
            filename, loc, 'V', '[V] PEP 8 (%s): %s',
            (code, text), offset=offset, text=text
        )


class OffsetError(PythonLintError):

    def __init__(self, filename, loc, text, offset):
        super(OffsetError, self).__init__(
            filename, loc, 'E', '[E] %r', (text,), offset=offset + 1, text=text
        )


class PythonError(PythonLintError):

    def __init__(self, filename, loc, text):
        super(PythonError, self).__init__(
            filename, loc, 'E', '[E] %r', (text,), text=text
        )
