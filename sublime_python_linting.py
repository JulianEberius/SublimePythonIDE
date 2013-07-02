import sublime_plugin
import sublime
import os
import sys
import pickle
import re
from collections import defaultdict
from functools import cmp_to_key
from SublimePythonIDE.sublime_python import proxy_for, get_setting

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import pyflakes
from linter import Pep8Error, Pep8Warning, OffsetError


class PythonLintingListener(sublime_plugin.EventListener):
    '''Copies a lot of logic from SublimeLinter (https://github.com/SublimeLinter/SublimeLinter)

    Specifically, the Python-Linting parts (PEP8, PyFlakes) are included partly here, and
    partly in server/linter.py.

    Furthermore, the error highlighting code is also adapted from there.'''
    error_underlines = defaultdict(list)
    violation_underlines = defaultdict(list)
    warning_underlines = defaultdict(list)
    error_messages = defaultdict(dict)
    violation_messages = defaultdict(dict)
    warning_messages = defaultdict(dict)

    # Select one of the predefined gutter mark themes, the options are:
    # "alpha", "bright", "dark", "hard" and "simple"
    MARK_THEMES = ('alpha', 'bright', 'dark', 'hard', 'simple')
    # The path to the built-in gutter mark themes
    MARK_THEMES_PATH = os.path.join("Packages", "SublimePythonIDE", 'gutter_mark_themes')
    # The original theme for anyone interested the previous minimalist approach
    ORIGINAL_MARK_THEME = {
        'violation': 'dot',
        'warning': 'dot',
        'illegal': 'circle'
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_selected_line_number = -1

    def is_python_syntax(self, view):
        syntax = view.settings().get('syntax')
        return bool(syntax and ("Python" in syntax))

    def on_load_async(self, view):
        '''Check the file syntax on load'''
        if not self.is_python_syntax(view) or view.is_scratch():
            return
        self._check(view)

    def on_activated_async(self, view):
        '''Check the file syntax on activated'''
        if not self.is_python_syntax(view) or view.is_scratch():
            return
        self._check(view)

    def on_post_save_async(self, view):
        '''Check the file syntax on save'''
        if not self.is_python_syntax(view) or view.is_scratch():
            return
        self._check(view)

    def on_selection_modified_async(self, view):
        if (not self.is_python_syntax(view)
                or not get_setting('python_linting', view, True)):
            return
        self.update_statusbar(view)

    def _check(self, view):
        if not get_setting('python_linting', view, True):
            return

        filename = view.file_name()
        proxy = proxy_for(view)
        lint_settings = {
            'pep8': get_setting('pep8', view, default_value=True),
            'pep8_ignore': get_setting('pep8_ignore', view, default_value=[]),
            'pyflakes_ignore': get_setting('pyflakes_ignore', view, default_value=[]),
        }

        errors = proxy.check_syntax(view.substr(
            sublime.Region(0, view.size())), lint_settings, filename)
        try:
            errors = pickle.loads(errors.data)
        except Exception as e:
            print("SublimePythonIDE: No server respose")
            print(e)
            return

        vid = view.id()

        lines = set()
        self.error_underlines[vid] = []  # leave this here for compatibility with original plugin
        self.error_messages[vid] = {}
        self.violation_underlines[vid] = []
        self.violation_messages[vid] = {}
        self.warning_underlines[vid] = []
        self.warning_messages[vid] = {}

        self.parse_errors(
            view,
            errors,
            lines,
            self.error_underlines[vid],
            self.violation_underlines[vid],
            self.warning_underlines[vid],
            self.error_messages[vid],
            self.violation_messages[vid],
            self.warning_messages[vid],
        )

        # the result can be a list of errors, or single syntax exception
        self.add_lint_marks(view, lines, self.error_underlines[vid], self.violation_underlines[vid], self.warning_underlines[vid])
        self.on_selection_modified_async(view)

    def update_statusbar(self, view):
        vid = view.id()
        lineno = view.rowcol(view.sel()[0].end())[0] + 0
        if self.last_selected_line_number == lineno:
            return
        self.last_selected_line_number = lineno
        errors_msg = []

        if lineno is not None:
            if vid in self.error_messages and lineno in self.error_messages[vid]:
                errors_msg.extend(self.error_messages[vid][lineno])
            if vid in self.violation_messages and lineno in self.violation_messages[vid]:
                errors_msg.extend(self.violation_messages[vid][lineno])
            if vid in self.warning_messages and lineno in self.warning_messages[vid]:
                errors_msg.extend(self.warning_messages[vid][lineno])

        if errors_msg:
            view.set_status('Linter', '; '.join(errors_msg))
        else:
            view.erase_status('Linter')

    def erase_lint_marks(self, view):
        '''erase all "lint" error marks from view'''
        view.erase_regions('lint-underline-illegal')
        view.erase_regions('lint-underline-violation')
        view.erase_regions('lint-underline-warning')
        view.erase_regions('lint-outlines-illegal')
        view.erase_regions('lint-outlines-violation')
        view.erase_regions('lint-outlines-warning')
        view.erase_regions('lint-annotations')

    def add_lint_marks(self, view, lines, error_underlines, violation_underlines, warning_underlines):
        '''Adds lint marks to view.'''
        try:
            vid = view.id()
            self.erase_lint_marks(view)

            types = {'warning': warning_underlines, 'violation': violation_underlines, 'illegal': error_underlines}

            for type_name, underlines in list(types.items()):
                if underlines:
                    view.add_regions('lint-underline-' + type_name, underlines, 'python_linter.underline.' + type_name, flags=sublime.DRAW_EMPTY_AS_OVERWRITE)

            if lines:
                outline_style = get_setting('python_linter_mark_style', view, 'outline')
                gutter_mark_enabled = get_setting('python_linter_gutter_marks', view, True)
                gutter_mark_theme = get_setting('python_linter_gutter_marks_theme', view, 'simple')

                outlines = {'warning': [], 'violation': [], 'illegal': []}
                for line in self.error_messages[vid]:
                    outlines['illegal'].append(view.full_line(view.text_point(line, 0)))
                for line in self.warning_messages[vid]:
                    outlines['warning'].append(view.full_line(view.text_point(line, 0)))
                for line in self.violation_messages[vid]:
                    outlines['violation'].append(view.full_line(view.text_point(line, 0)))

                for lint_type in outlines:
                    if outlines[lint_type]:
                        args = [
                            'lint-outlines-{0}'.format(lint_type),
                            outlines[lint_type],
                            'python_linter.outline.{0}'.format(lint_type)
                        ]

                        if gutter_mark_enabled:
                            if gutter_mark_theme == 'original':
                                gutter_mark_image = self.ORIGINAL_MARK_THEME[lint_type]
                            elif gutter_mark_theme in self.MARK_THEMES:
                                gutter_mark_image = os.path.join(self.MARK_THEMES_PATH, "{0}-{1}.png".format(gutter_mark_theme, lint_type))
                            else:
                                gutter_mark_image = "{0}-{1}.png".format(gutter_mark_theme, lint_type)

                        args.append(gutter_mark_image)

                        if outline_style == 'none':
                            args.append(sublime.HIDDEN)
                        else:
                            args.append(sublime.DRAW_OUTLINED)
                        view.add_regions(*args)
        except Exception as e:
            print("SublimePythonIDE: Add lint marks failed")
            print(e)

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

    def underline_regex(self, view, lineno, regex, lines, underlines, wordmatch=None, linematch=None):
        # Assume lineno is one-based, ST2 wants zero-based line numbers
        lineno -= 1
        lines.add(lineno)
        offset = 0
        line = view.full_line(view.text_point(lineno, 0))
        lineText = view.substr(line)

        if linematch:
            match = re.match(linematch, lineText)

            if match:
                lineText = match.group('match')
                offset = match.start('match')
            else:
                return

        iters = re.finditer(regex, lineText)

        iters = re.finditer(regex, lineText)
        results = [(result.start('underline'), result.end('underline')) for result in iters if not wordmatch or result.group('underline') == wordmatch]

        # Make the lineno one-based again for underline_range
        lineno += 1

        for start, end in results:
            self.underline_range(view, lineno, start + offset, underlines, end - start)

    def underline_range(self, view, lineno, position, underlines, length=1):
        # Assume lineno is one-based, ST2 wants zero-based line numbers
        lineno -= 1
        line = view.full_line(view.text_point(lineno, 0))
        position += line.begin()

        for i in range(length):
            underlines.append(sublime.Region(position + i))

    def parse_errors(self, view, errors, lines, errorUnderlines, violationUnderlines, warningUnderlines, errorMessages, violationMessages, warningMessages):
        def underline_word(lineno, word, underlines):
            regex = r'((and|or|not|if|elif|while|in)\s+|[+\-*^%%<>=\(\{{])*\s*(?P<underline>[\w\.]*{0}[\w]*)'.format(re.escape(word))
            self.underline_regex(view, lineno, regex, lines, underlines, word)

        def underline_import(lineno, word, underlines):
            linematch = '(from\s+[\w_\.]+\s+)?import\s+(?P<match>[^#;]+)'
            regex = '(^|\s+|,\s*|as\s+)(?P<underline>[\w]*{0}[\w]*)'.format(re.escape(word))
            self.underline_regex(view, lineno, regex, lines, underlines, word, linematch)

        def underline_for_var(lineno, word, underlines):
            regex = 'for\s+(?P<underline>[\w]*{0}[\w*])'.format(re.escape(word))
            self.underline_regex(view, lineno, regex, lines, underlines, word)

        def underline_duplicate_argument(lineno, word, underlines):
            regex = 'def [\w_]+\(.*?(?P<underline>[\w]*{0}[\w]*)'.format(re.escape(word))
            self.underline_regex(view, lineno, regex, lines, underlines, word)

        errors.sort(key=cmp_to_key(lambda a, b: a.lineno < b.lineno))
        ignoreImportStar = view.settings().get('pyflakes_ignore_import_*', True)

        for error in errors:
            try:
                error_level = error.level
            except AttributeError:
                error_level = 'W'
            if error_level == 'E':
                messages = errorMessages
                underlines = errorUnderlines
            elif error_level == 'V':
                messages = violationMessages
                underlines = violationUnderlines
            elif error_level == 'W':
                messages = warningMessages
                underlines = warningUnderlines

            if isinstance(error, pyflakes.messages.ImportStarUsed) and ignoreImportStar:
                continue

            self.add_message(error.lineno, lines, str(error), messages)

            if isinstance(error, (Pep8Error, Pep8Warning, OffsetError)):
                self.underline_range(view, error.lineno, error.offset, underlines)

            elif isinstance(error, (pyflakes.messages.RedefinedWhileUnused,
                                    pyflakes.messages.UndefinedName,
                                    pyflakes.messages.UndefinedExport,
                                    pyflakes.messages.UndefinedLocal,
                                    pyflakes.messages.Redefined,
                                    pyflakes.messages.UnusedVariable)):
                underline_word(error.lineno, error.message_args[0], underlines)

            elif isinstance(error, pyflakes.messages.ImportShadowedByLoopVar):
                underline_for_var(error.lineno, error.message_args[0], underlines)

            elif isinstance(error, pyflakes.messages.UnusedImport):
                underline_import(error.lineno, error.message_args[0], underlines)

            elif isinstance(error, pyflakes.messages.ImportStarUsed):
                underline_import(error.lineno, '*', underlines)

            elif isinstance(error, pyflakes.messages.DuplicateArgument):
                underline_duplicate_argument(error.lineno, error.message_args[0], underlines)

            elif isinstance(error, pyflakes.messages.LateFutureImport):
                pass
            else:
                print('Oops, we missed an error type!', type(error))
