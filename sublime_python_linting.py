"""
This module copies a lot of logic from SublimeLinter:
    (https://github.com/SublimeLinter/SublimeLinter)

Specifically, the Python-Linting parts (PEP8, PyFlakes)
are included partly here, and partly in server/linter.py.

Furthermore, the error highlighting code is also adapted from there.
"""

import os
import re
import pickle
from collections import defaultdict
from functools import cmp_to_key

import sublime
import sublime_plugin

from SublimePythonIDE import pyflakes
from SublimePythonIDE.sublime_python_errors import OffsetError, Pep8Error, Pep8Warning, PythonLintError
from SublimePythonIDE.sublime_python import proxy_for, get_setting,\
    file_or_buffer_name, override_view_setting, get_current_active_view, python_only

error_underlines = defaultdict(list)
violation_underlines = defaultdict(list)
warning_underlines = defaultdict(list)
error_messages = defaultdict(dict)
violation_messages = defaultdict(dict)
warning_messages = defaultdict(dict)

erroneous_lines = dict()

error_level_mapper = {
    'E': (error_messages, error_underlines),
    'W': (warning_messages, warning_underlines),
    'V': (violation_messages, violation_underlines)
}

# Select one of the predefined gutter mark themes, the options are:
# "alpha", "bright", "dark", "hard" and "simple"
MARK_THEMES = ('alpha', 'bright', 'dark', 'hard', 'simple')


'''The path to the built-in gutter mark themes. this API does not
expect OS-specific paths, but only forward-slashes'''
MARK_THEMES_PATH = "/".join(
    [
        "Packages",
        os.path.basename(os.path.dirname(__file__)),
        "gutter_mark_themes"
    ]
)

# The original theme for anyone interested the previous minimalist approach
ORIGINAL_MARK_THEME = {
    'violation': 'dot',
    'warning': 'dot',
    'illegal': 'circle'
}


def check(view=None):
    """Perform a linter check on the view
    """
    if view is None:
        view = get_current_active_view()

    if not get_setting('python_linting', view, True):
        return

    filename = file_or_buffer_name(view)
    proxy = proxy_for(view)
    if not proxy:
        return

    lint_settings = {
        'pep8': get_setting('pep8', view, default_value=True),
        'pep8_ignore': get_setting('pep8_ignore', view, default_value=[]),
        'pep8_max_line_length': get_setting(
            'pep8_max_line_length', view, default_value=None),
        'pyflakes_ignore': get_setting(
            'pyflakes_ignore', view, default_value=[]),
    }

    code = view.substr(sublime.Region(0, view.size()))
    encoding = view.encoding()
    if encoding.lower() == "undefined":
        encoding = "utf-8"
    errors = proxy.check_syntax(code, encoding, lint_settings, filename)
    try:
        if errors:
            errors = pickle.loads(errors.data)

        vid = view.id()
        lines = set()

        # leave this here for compatibility with original plugin
        error_underlines[vid] = []
        error_messages[vid] = {}
        violation_underlines[vid] = []
        violation_messages[vid] = {}
        warning_underlines[vid] = []
        warning_messages[vid] = {}

        if errors:
            parse_errors(view, errors, lines, vid)

        erroneous_lines[vid] = ListWithPointer(sorted(set(
            list(error_messages[vid].keys()) +
            list(violation_messages[vid].keys()) +
            list(warning_messages[vid].keys()))))

        # the result can be a list of errors, or single syntax exception
        try:
            _update_lint_marks(view, lines)
        except Exception as e:
            print('SublimePythonIDE: Add lint marks failed\n{0}'.format(e))

        update_statusbar(view)
    except Exception as error:
        print("SublimePythonIDE: No server response\n{0}".format(error))


@python_only
def update_statusbar(view):
    """Updates the view status bar
    """
    if get_setting('python_linting', view, True):
        lineno = view.rowcol(view.sel()[0].end())[0] + 0
        errors_msg = _get_lineno_msgs(view, lineno)

        if len(errors_msg) > 0:
            view.set_status('Linter', '; '.join(errors_msg))
        else:
            view.erase_status('Linter')


def _get_lineno_msgs(view, lineno):
    """Get lineno error messages and return it back
    """

    errors_msg = []
    if lineno is not None:
        vid = view.id()
        errors_msg.extend(error_messages[vid].get(lineno, []))
        errors_msg.extend(warning_messages[vid].get(lineno, []))
        errors_msg.extend(violation_messages[vid].get(lineno, []))

    return errors_msg


def _update_lint_marks(view, lines):
    """Update lint marks to view on the given lines.
    """

    style = get_setting('python_linter_mark_style', view, 'outline')
    outline_style = {'none': sublime.HIDDEN}

    _erase_lint_marks(view)

    # for name, underlines in _get_types(view).items():
    #     if len(underlines) > 0:
    #         view.add_regions(
    #             'lint-underline-{name}'.format(name=name),
    #             underlines,
    #             scope_name(name),
    #             flags=sublime.DRAW_EMPTY_AS_OVERWRITE
    #         )

    if len(lines) > 0:
        outlines = _get_outlines(view)

        for lint_type, lints in outlines.items():
            args = [
                'lint-outlines-{0}'.format(lint_type),
                outlines[lint_type],
                scope_name(lint_type),
                _get_gutter_mark_theme(view, lint_type),
                outline_style.get(style, sublime.DRAW_OUTLINED)
            ]

            view.add_regions(*args)


def scope_name(error_name):
    result = {
        "violation": "sublimepythonide.mark.error",
        "illegal": "sublimepythonide.mark.error",
        "warning": "sublimepythonide.mark.warning",
    }.get(error_name)
    return result


def add_message(lineno, lines, message, messages):
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


def underline_regex(view, **kwargs):
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
        underline_range(
            view, lineno, start + offset, kwargs['underlines'], end - start
        )


def underline_range(view, lineno, position, underlines, length=1):
    # Assume lineno is one-based, ST2 wants zero-based line numbers
    lineno -= 1
    line = view.full_line(view.text_point(lineno, 0))
    position += line.begin()

    for i in range(length):
        underlines.append(sublime.Region(position + i))


def parse_errors(view, errors, lines, vid):
    """Parse errors returned from the Pyflakes library
    """

    def underline_word(lineno, word, underlines):
        regex = (
            r'((and|or|not|if|elif|while|in)\s+|[+\-*^%%<>=\(\{{])*\s'
            '*(?P<underline>[\w\.]*{0}[\w]*)'.format(re.escape(word))
        )
        underline_regex(
            view, lineno=lineno, regex=regex, lines=lines,
            underlines=underlines, wordmatch=word
        )

    def underline_import(lineno, word, underlines):
        linematch = '(from\s+[\w_\.]+\s+)?import\s+(?P<match>[^#;]+)'
        regex = '(^|\s+|,\s*|as\s+)(?P<underline>[\w]*{0}[\w]*)'.format(
            re.escape(word)
        )
        underline_regex(
            view, lineno=lineno, regex=regex, lines=lines,
            underlines=underlines, wordmatch=word, linematch=linematch
        )

    def underline_for_var(lineno, word, underlines):
        regex = 'for\s+(?P<underline>[\w]*{0}[\w*])'.format(
            re.escape(word)
        )
        underline_regex(
            view, lineno=lineno, regex=regex, lines=lines,
            underlines=underlines, wordmatch=word
        )

    def underline_duplicate_argument(lineno, word, underlines):
        regex = 'def [\w_]+\(.*?(?P<underline>[\w]*{0}[\w]*)'.format(
            re.escape(word)
        )
        underline_regex(
            view, lineno=lineno, regex=regex, lines=lines,
            underlines=underlines, wordmatch=word
        )

    errors.sort(key=cmp_to_key(lambda a, b: a.lineno < b.lineno))
    ignore_star = view.settings().get('pyflakes_ignore_import_*', True)

    for error in errors:
        error_level = 'W' if not hasattr(error, 'level') else error.level
        messages, underlines = error_level_mapper.get(error_level)
        messages, underlines = (messages[vid], underlines[vid])

        if type(error) is pyflakes.messages.ImportStarUsed and ignore_star:
            continue

        add_message(error.lineno, lines, str(error), messages)
        if isinstance(error, (Pep8Error, Pep8Warning, OffsetError,
                              PythonLintError)):
            underline_range(
                view, error.lineno, error.offset, underlines
            )
        elif isinstance(
            error, (
                pyflakes.messages.RedefinedWhileUnused,
                pyflakes.messages.UndefinedName,
                pyflakes.messages.UndefinedExport,
                pyflakes.messages.UndefinedLocal,
                pyflakes.messages.RedefinedWhileUnused,
                pyflakes.messages.UnusedVariable,
                pyflakes.messages.ReturnOutsideFunction,
                pyflakes.messages.ReturnWithArgsInsideGenerator,
                pyflakes.messages.RedefinedInListComp)):
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


def _get_outlines(view):
    """Return outlines for the given view
    """

    vid = view.id()

    errors = error_messages[vid]
    warnings = warning_messages[vid]
    violation = violation_messages[vid]

    return {
        'warning': [_mark_lines(view, l) for l in warnings],
        'illegal': [_mark_lines(view, l) for l in errors],
        'violation': [_mark_lines(view, l) for l in violation]
    }


def _erase_lint_marks(view):
    """Erase all "lint" error marks from view
    """

    view.erase_regions('lint-underline-illegal')
    view.erase_regions('lint-underline-violation')
    view.erase_regions('lint-underline-warning')
    view.erase_regions('lint-outlines-illegal')
    view.erase_regions('lint-outlines-violation')
    view.erase_regions('lint-outlines-warning')
    view.erase_regions('lint-annotations')


def _get_types(view):
    """Get lint types
    """

    vid = view.id()
    return {
        'warning': warning_underlines[vid],
        'violation': violation_underlines[vid],
        'illegal': error_underlines[vid]
    }


def _mark_lines(view, line):
    """Return lines where to set marks
    """

    return view.full_line(view.text_point(line, 0))


def _get_gutter_mark_theme(view, lint_type):
    """Return the right gutter mark theme icons
    """

    image = ''
    if get_setting('python_linter_gutter_marks', view, True):
        theme = get_setting(
            'python_linter_gutter_marks_theme', view, 'simple'
        )

        image = '{0}-{1}.png'.format(theme, lint_type)
        if theme == 'original':
            image = ORIGINAL_MARK_THEME[lint_type]
        elif theme in MARK_THEMES:
            # this API does not expect OS-specific paths, but only
            # forward-slashes
            image = MARK_THEMES_PATH + '/' + '{0}-{1}.png'.format(
                theme, lint_type)

    return image


class PythonLintingListener(sublime_plugin.EventListener):

    """This class hooks into various Sublime Text events to check
    for lint and update status bar text.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_selected_line_number = -1

    @python_only
    def on_load_async(self, view):
        """Check the file syntax on load
        """

        check(view)

    @python_only
    def on_activated_async(self, view):
        """Check the file syntax on activated
        """
        check(view)

    @python_only
    def on_post_save_async(self, view):
        """Check the file syntax on save
        """

        check(view)

    @python_only
    def on_selection_modified_async(self, view):
        """Update status bar text when cursor
        changes spot.
        """
        lineno = view.rowcol(view.sel()[0].end())[0] + 0
        if self.last_selected_line_number != lineno:
            update_statusbar(view)


class PythonDisablePep8Command(sublime_plugin.ApplicationCommand):

    def run(self, *args):
        view = get_current_active_view()
        override_view_setting('pep8', False, view)
        check(view)


class PythonEnablePep8Command(sublime_plugin.ApplicationCommand):

    def run(self, *args):
        view = get_current_active_view()
        override_view_setting('pep8', True, view)
        check(view)


class PythonNextErrorCommand(sublime_plugin.ApplicationCommand):

    def run(self, *args):
        view = get_current_active_view()
        view_error_lines = erroneous_lines[view.id()]
        next_error_line = view_error_lines.next()

        path = "%s:%d" % (view.file_name(), next_error_line + 1)
        view.window().open_file(path, sublime.ENCODED_POSITION)


class PythonPreviousErrorCommand(sublime_plugin.ApplicationCommand):

    def run(self, *args):
        view = get_current_active_view()
        view_error_lines = erroneous_lines[view.id()]
        prev_error_line = view_error_lines.previous()

        path = "%s:%d" % (view.file_name(), prev_error_line + 1)
        view.window().open_file(path, sublime.ENCODED_POSITION)


''' Util '''


class ListWithPointer(list):

    FORWARD = 0
    BACKWARD = 1

    def __init__(self, data=[]):
        list.__init__(self, data)
        self.pointer = 0
        self.direction = self.FORWARD

    def next(self):
        if self.direction == self.BACKWARD:
            self.direction = self.FORWARD
            self.pointer = (self.pointer + 1) % len(self)
        result = self.__getitem__(self.pointer)
        self.pointer = (self.pointer + 1) % len(self)
        return result

    def previous(self):
        if self.direction == self.FORWARD:
            self.direction = self.BACKWARD
            self.pointer = (self.pointer - 1) % len(self)
        self.pointer = (self.pointer - 1) % len(self)
        result = self.__getitem__(self.pointer)
        return result
