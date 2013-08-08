import os
import re
import sys
import pickle
from collections import defaultdict
from functools import cmp_to_key, wraps

import sublime
import sublime_plugin


sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import pyflakes
from linter import Pep8Error, Pep8Warning, OffsetError
from sublime_python import proxy_for, get_setting


def python_only(func):
    """Decorator that make sure we call the given function in python only
    """

    @wraps(func)
    def wrapper(self, view):
        if self.is_python_syntax(view) and not view.is_scratch():
            return func(self, view)

    return wrapper


def mark_themes_path():
    '''The path to the built-in gutter mark themes. this API does not
    expect OS-specific paths, but only forward-slashes'''
    plugin_dir = os.path.basename(os.path.dirname(__file__))
    return "/".join(["Packages", plugin_dir, "gutter_mark_themes"])


class PythonLintingListener(sublime_plugin.EventListener):
    """
    Copies a lot of logic from SublimeLinter:
        (https://github.com/SublimeLinter/SublimeLinter)

    Specifically, the Python-Linting parts (PEP8, PyFlakes)
    are included partly here, and partly in server/linter.py.

    Furthermore, the error highlighting code is also adapted from there.
    """
    error_underlines = defaultdict(list)
    violation_underlines = defaultdict(list)
    warning_underlines = defaultdict(list)
    error_messages = defaultdict(dict)
    violation_messages = defaultdict(dict)
    warning_messages = defaultdict(dict)

    # Select one of the predefined gutter mark themes, the options are:
    # "alpha", "bright", "dark", "hard" and "simple"
    MARK_THEMES = ('alpha', 'bright', 'dark', 'hard', 'simple')

    MARK_THEMES_PATH = mark_themes_path()

    # The original theme for anyone interested the previous minimalist approach
    ORIGINAL_MARK_THEME = {
        'violation': 'dot',
        'warning': 'dot',
        'illegal': 'circle'
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_selected_line_number = -1

        self.error_level_mapper = {
            'E': (self.error_messages, self.error_underlines),
            'W': (self.warning_messages, self.warning_underlines),
            'V': (self.violation_messages, self.violation_underlines)
        }

    def is_python_syntax(self, view):
        """Return true if we are in a Python syntax defined view
        """

        syntax = view.settings().get('syntax')
        return bool(syntax and ("Python" in syntax))

    @python_only
    def on_load_async(self, view):
        """Check the file syntax on load
        """

        self.check(view)

    @python_only
    def on_activated_async(self, view):
        """Check the file syntax on activated
        """
        self.check(view)

    @python_only
    def on_post_save_async(self, view):
        """Check the file syntax on save
        """

        self.check(view)

    def on_selection_modified_async(self, view):
        if (self.is_python_syntax(view)
                and get_setting('python_linting', view, True)):
            self.update_statusbar(view)

    def check(self, view):
        """Perform a linter check on the view
        """

        if not get_setting('python_linting', view, True):
            return

        filename = view.file_name()
        proxy = proxy_for(view)
        if not proxy:
            return

        lint_settings = {
            'pep8': get_setting('pep8', view, default_value=True),
            'pep8_ignore': get_setting('pep8_ignore', view, default_value=[]),
            'pyflakes_ignore': get_setting(
                'pyflakes_ignore', view, default_value=[]),
        }

        errors = proxy.check_syntax(view.substr(
            sublime.Region(0, view.size())), lint_settings, filename)
        try:
            errors = pickle.loads(errors.data)

            vid = view.id()
            lines = set()

            # leave this here for compatibility with original plugin
            self.error_underlines[vid] = []
            self.error_messages[vid] = {}
            self.violation_underlines[vid] = []
            self.violation_messages[vid] = {}
            self.warning_underlines[vid] = []
            self.warning_messages[vid] = {}

            self.parse_errors(view, errors, lines, vid)

            # the result can be a list of errors, or single syntax exception
            try:
                self.add_lint_marks(view, lines)
            except Exception as e:
                print('SublimePythonIDE: Add lint marks failed\n{0}'.format(e))

            self.on_selection_modified_async(view)
        except Exception as error:
            print("SublimePythonIDE: No server respose\n{0}".format(error))

    def update_statusbar(self, view):
        """Updates the view status bar
        """

        lineno = view.rowcol(view.sel()[0].end())[0] + 0

        if self.last_selected_line_number != lineno:
            self.last_selected_line_number = lineno
            errors_msg = self._get_lineno_msgs(view, lineno)

            if len(errors_msg) > 0:
                view.set_status('Linter', '; '.join(errors_msg))
            else:
                view.erase_status('Linter')

    def _get_lineno_msgs(self, view, lineno):
        """Get lineno error messages and return it back
        """

        errors_msg = []
        if lineno is not None:
            vid = view.id()
            errors_msg.extend(self.error_messages[vid].get(lineno, []))
            errors_msg.extend(self.warning_messages[vid].get(lineno, []))
            errors_msg.extend(self.violation_messages[vid].get(lineno, []))

        return errors_msg

    def add_lint_marks(self, view, lines):
        """Adds lint marks to view on the given lines.
        """

        style = get_setting('python_linter_mark_style', view, 'outline')
        outline_style = {'none': sublime.HIDDEN}

        self._erase_lint_marks(view)

        for name, underlines in self._get_types(view).items():
            if len(underlines) > 0:
                view.add_regions(
                    'lint-underline-{name}'.format(name=name),
                    underlines,
                    'python_linter.underline.{name}'.format(name=name),
                    flags=sublime.DRAW_EMPTY_AS_OVERWRITE
                )

        if len(lines) > 0:
            outlines = self._get_outlines(view)

            for lint_type, lints in outlines.items():
                args = [
                    'lint-outlines-{0}'.format(lint_type),
                    outlines[lint_type],
                    'python_linter.outline.{0}'.format(lint_type),
                    self._get_gutter_mark_theme(view, lint_type),
                    outline_style.get(style, sublime.DRAW_OUTLINED)
                ]

                view.add_regions(*args)

    def add_message(self, lineno, lines, message, messages):
        # Assume lineno is one-based, ST2 wants zero-based line numbers
        lineno -= 1
        lines.add(lineno)
        message = message[0].upper() + message[1:]

        # Remove trailing period from error message
        if message[-1] == '.':
            message = message[:-1]

        if lineno in messages:
            messages[lineno].append(message)
        else:
            messages[lineno] = [message]

    def underline_regex(self, view, **kwargs):
        # Assume lineno is one-based, ST2 wants zero-based line numbers
        offset = 0
        lineno = kwargs.get('lineno', 1) - 1
        kwargs.get('lines', set()).add(lineno)
        line = view.full_line(view.text_point(lineno, 0))
        line_text = view.substr(line)

        if kwargs.get('linematch') is not None:
            match = re.match(kwargs['linematch'], line_text)

            if match is not None:
                line_text = match.group('match')
                offset = match.start('match')
            else:
                return

        iters = re.finditer(kwargs.get('regex'), line_text)
        results = [
            (r.start('underline'), r.end('underline')) for r in iters if (
                kwargs.get('wordmatch') is None
                or r.group('underline') == kwargs.get('wordmatch')
            )
        ]

        # make the lineno one-based again for underline_range
        lineno += 1
        for start, end in results:
            self.underline_range(
                view, lineno, start + offset, kwargs['underlines'], end - start
            )

    def underline_range(self, view, lineno, position, underlines, length=1):
        # Assume lineno is one-based, ST2 wants zero-based line numbers
        lineno -= 1
        line = view.full_line(view.text_point(lineno, 0))
        position += line.begin()

        for i in range(length):
            underlines.append(sublime.Region(position + i))

    def parse_errors(self, view, errors, lines, vid):
        """Parse errors returned from the Pyflakes library
        """

        def underline_word(lineno, word, underlines):
            regex = (
                r'((and|or|not|if|elif|while|in)\s+|[+\-*^%%<>=\(\{{])*\s'
                '*(?P<underline>[\w\.]*{0}[\w]*)'.format(re.escape(word))
            )
            self.underline_regex(
                view, lineno=lineno, regex=regex, lines=lines,
                underlines=underlines, wordmatch=word
            )

        def underline_import(lineno, word, underlines):
            linematch = '(from\s+[\w_\.]+\s+)?import\s+(?P<match>[^#;]+)'
            regex = '(^|\s+|,\s*|as\s+)(?P<underline>[\w]*{0}[\w]*)'.format(
                re.escape(word)
            )
            self.underline_regex(
                view, lineno=lineno, regex=regex, lines=lines,
                underlines=underlines, wordmatch=word, linematch=linematch
            )

        def underline_for_var(lineno, word, underlines):
            regex = 'for\s+(?P<underline>[\w]*{0}[\w*])'.format(
                re.escape(word)
            )
            self.underline_regex(
                view, lineno=lineno, regex=regex, lines=lines,
                underlines=underlines, wordmatch=word
            )

        def underline_duplicate_argument(lineno, word, underlines):
            regex = 'def [\w_]+\(.*?(?P<underline>[\w]*{0}[\w]*)'.format(
                re.escape(word)
            )
            self.underline_regex(
                view, lineno=lineno, regex=regex, lines=lines,
                underlines=underlines, wordmatch=word
            )

        errors.sort(key=cmp_to_key(lambda a, b: a.lineno < b.lineno))
        ignore_star = view.settings().get('pyflakes_ignore_import_*', True)

        for error in errors:
            error_level = 'W' if not hasattr(error, 'level') else error.level
            messages, underlines = self.error_level_mapper.get(error_level)
            messages, underlines = (messages[vid], underlines[vid])

            if type(error) is pyflakes.messages.ImportStarUsed and ignore_star:
                continue

            self.add_message(error.lineno, lines, str(error), messages)
            if isinstance(error, (Pep8Error, Pep8Warning, OffsetError)):
                self.underline_range(
                    view, error.lineno, error.offset, underlines
                )
            elif isinstance(
                error, (
                    pyflakes.messages.RedefinedWhileUnused,
                    pyflakes.messages.UndefinedName,
                    pyflakes.messages.UndefinedExport,
                    pyflakes.messages.UndefinedLocal,
                    pyflakes.messages.Redefined,
                    pyflakes.messages.UnusedVariable)):
                underline_word(error.lineno, error.message_args[0], underlines)
            elif isinstance(error, pyflakes.messages.ImportShadowedByLoopVar):
                underline_for_var(
                    error.lineno, error.message_args[0], underlines)
            elif isinstance(error, pyflakes.messages.UnusedImport):
                underline_import(
                    error.lineno, error.message_args[0], underlines)
            elif isinstance(error, pyflakes.messages.ImportStarUsed):
                underline_import(error.lineno, '*', underlines)
            elif isinstance(error, pyflakes.messages.DuplicateArgument):
                underline_duplicate_argument(
                    error.lineno, error.message_args[0], underlines)
            elif isinstance(error, pyflakes.messages.LateFutureImport):
                pass
            else:
                print('Oops, we missed an error type!', type(error))

    def _get_outlines(self, view):
        """Return outlines for the given view
        """

        vid = view.id()

        errors = self.error_messages[vid]
        warnings = self.warning_messages[vid]
        violation = self.violation_messages[vid]

        return {
            'warning': [self._mark_lines(view, l) for l in warnings],
            'illegal': [self._mark_lines(view, l) for l in errors],
            'violation': [self._mark_lines(view, l) for l in violation]
        }

    def _erase_lint_marks(self, view):
        """Erase all "lint" error marks from view
        """

        view.erase_regions('lint-underline-illegal')
        view.erase_regions('lint-underline-violation')
        view.erase_regions('lint-underline-warning')
        view.erase_regions('lint-outlines-illegal')
        view.erase_regions('lint-outlines-violation')
        view.erase_regions('lint-outlines-warning')
        view.erase_regions('lint-annotations')

    def _get_types(self, view):
        """Get lint types
        """

        vid = view.id()
        return {
            'warning': self.warning_underlines[vid],
            'violation': self.violation_underlines[vid],
            'illegal': self.error_underlines[vid]
        }

    def _mark_lines(self, view, line):
        """Return lines where to set marks
        """

        return view.full_line(view.text_point(line, 0))

    def _get_gutter_mark_theme(self, view, lint_type):
        """Return the right gutter mark theme icons
        """

        image = ''
        if get_setting('python_linter_gutter_marks', view, True):
            theme = get_setting(
                'python_linter_gutter_marks_theme', view, 'simple'
            )

            image = '{0}-{1}.png'.format(theme, lint_type)
            if theme == 'original':
                image = self.ORIGINAL_MARK_THEME[lint_type]
            elif theme in self.MARK_THEMES:
                # this API does not expect OS-specific paths, but only forward-slashes
                image = self.MARK_THEMES_PATH + '/' + '{0}-{1}.png'.format(
                    theme, lint_type)

        return image
