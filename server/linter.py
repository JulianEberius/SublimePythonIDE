# -*- coding: utf-8 -*-
import _ast
from SublimePythonIDE import pep8
from SublimePythonIDE.sublime_python_errors import OffsetError, Pep8Error, Pep8Warning, PythonError
import SublimePythonIDE.pyflakes.checker as pyflakes


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
