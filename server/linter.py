# -*- coding: utf-8 -*-
import sys
import os
import os.path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lib"))

import _ast
import pep8
import pyflakes.checker as pyflakes

pyflakes.messages.Message.__str__ = (
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


class PythonLintError(pyflakes.messages.Message):

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


def pyflakes_check(code, encoding, filename, ignore=None):
    try:
        tree = compile(code.encode(encoding), filename, "exec", _ast.PyCF_ONLY_AST)
    except (SyntaxError, IndentationError) as value:
        msg = value.args[0]

        (lineno, offset, text) = value.lineno, value.offset, value.text

        # If there's an encoding problem with the file, the text is None.
        if text is None:
            # Avoid using msg, since for the only known case, it contains a
            # bogus message that claims the encoding the file declared was
            # unknown.
            if msg.startswith('duplicate argument'):
                arg = msg.split('duplicate argument ', 1)[1].split(' ', 1)[0]
                arg = arg.strip('\'"')
                error = pyflakes.messages.DuplicateArgument(
                    filename, lineno, arg
                )
            else:
                error = PythonError(filename, lineno, msg)
        else:
            line = text.splitlines()[-1]

            if offset is not None:
                offset = offset - (len(text) - len(line))

            if offset is not None:
                error = OffsetError(filename, lineno, msg, offset)
            else:
                error = PythonError(filename, lineno, msg)
        return [error]
    except ValueError as e:
        return [PythonError(filename, 1, e.args[0])]
    else:
        # Okay, it's syntactically valid.  Now check it.
        w = pyflakes.Checker(tree, filename, builtins=ignore)
        return w.messages


def pep8_check(code, filename, ignore=None, max_line_length=pep8.MAX_LINE_LENGTH):
    messages = []
    _lines = code.split('\n')

    if _lines:
        class SublimeLinterReport(pep8.BaseReport):
            def error(self, line_number, offset, text, check):
                """Report an error, according to options."""
                code = text[:4]
                message = text[5:]

                if self._ignore_code(code):
                    return
                if code in self.counters:
                    self.counters[code] += 1
                else:
                    self.counters[code] = 1
                    self.messages[code] = message

                # Don't care about expected errors or warnings
                if code in self.expected:
                    return

                self.file_errors += 1
                self.total_errors += 1

                if code.startswith('E'):
                    messages.append(Pep8Error(
                        filename, line_number, offset, code, message)
                    )
                else:
                    messages.append(Pep8Warning(
                        filename, line_number, offset, code, message)
                    )

                return code

        _ignore = ignore + pep8.DEFAULT_IGNORE.split(',')

        options = pep8.StyleGuide(
            reporter=SublimeLinterReport, ignore=_ignore).options
        options.max_line_length = max_line_length

        good_lines = [l + '\n' for l in _lines]
        good_lines[-1] = good_lines[-1].rstrip('\n')

        if not good_lines[-1]:
            good_lines = good_lines[:-1]

        try:
            pep8.Checker(filename, good_lines, options=options).check_all()
        except Exception as e:
            print("An exception occured when running pep8 checker: %s" % e)

    return messages


def do_linting(lint_settings, code, encoding, filename):

    errors = []

    if lint_settings.get("pep8", True):
        params = {
            'ignore': lint_settings.get('pep8_ignore', []),
            'max_line_length': lint_settings.get(
                'pep8_max_line_length', None) or pep8.MAX_LINE_LENGTH,
        }
        errors.extend(pep8_check(
            code, filename, **params)
        )

    pyflakes_ignore = lint_settings.get('pyflakes_ignore', None)
    pyflakes_disabled = lint_settings.get('pyflakes_disabled', False)

    if not pyflakes_disabled:
        errors.extend(pyflakes_check(code, encoding, filename, pyflakes_ignore))

    return errors
