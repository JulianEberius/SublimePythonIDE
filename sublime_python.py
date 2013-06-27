import sys
import os
import socket
import time
import subprocess
import pipes
import threading
import xmlrpc.client
import sublime
import sublime_plugin
import pickle
import re
from collections import defaultdict
from functools import cmp_to_key

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))

# for error type definitions
import pyflakes
from linter import Pep8Error, Pep8Warning, OffsetError

# contains root paths for each view, see root_folder_for()
ROOT_PATHS = {}
# contains proxy objects for external Python processes, by interpreter used
PROXIES = {}
# lock for aquiring proxy instances
PROXY_LOCK = threading.RLock()
# contains errors found by PyFlask
ERRORS_BY_LINE = {}
# saves positions on goto_definition
GOTO_STACK = []

# for debugging the server, start it manually, e.g., "python <path_to_>/server.py <port>" and set the port here
DEBUG_PORT = None


# Constants
SERVER_SCRIPT = pipes.quote(os.path.join(
    os.path.dirname(__file__), "server/server.py"))
RETRY_CONNECTION_LIMIT = 5
HEARTBEAT_FREQUENCY = 9
DRAW_TYPE = 4 | 32
NO_ROOT_PATH = -1


def get_setting(key, view=None, default_value=None):
    if view is None:
        view = sublime.active_window().active_view()
    try:
        settings = view.settings()
        if settings.has(key):
            return settings.get(key)
    except:
        pass
    s = sublime.load_settings('SublimePython.sublime-settings')
    return s.get(key, default_value)


class SimpleClearAndInsertCommand(sublime_plugin.TextCommand):
    '''utility command class for writing into the documentation view'''
    def run(self, edit, block=False, **kwargs):
        doc = kwargs['insert_string']
        r = sublime.Region(0, self.view.size())
        self.view.erase(edit, r)
        self.view.insert(edit, 0, doc)


class DebugProcDummy(object):
    '''used only for debugging, when the server process is started externally'''
    def poll(*args):
        return None

    def terminate():
        pass


class Proxy(object):
    '''Abstracts the external Python processes that do the actual
    work. SublimePython just calls local methods on Proxy objects.
    The Proxy objects start external Python processes, send them heartbeat
    messages, communicate with them and restart them if necessary.'''
    def __init__(self, python):
        self.python = python
        self.proc = None
        self.proxy = None
        self.port = None
        self.restart()

    def get_free_port(self):
        s = socket.socket()
        s.bind(('', 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def restart(self):
        if DEBUG_PORT is None:
            self.port = self.get_free_port()
            self.proc = subprocess.Popen(
                "%s %s %i" % (self.python, SERVER_SCRIPT, self.port),
                shell=True
            )
            print("starting server on port %i with %s" % (self.port, self.python))
        else:
            self.port = DEBUG_PORT
            self.proc = DebugProcDummy()
        self.proxy = xmlrpc.client.ServerProxy(
            'http://localhost:%i' % self.port, allow_none=True)
        self.set_heartbeat_timer()

    def set_heartbeat_timer(self):
        sublime.set_timeout_async(
            self.send_heartbeat, HEARTBEAT_FREQUENCY * 1000)

    def stop(self):
        self.proxy = None
        self.proc.terminate()

    def send_heartbeat(self):
        if self.proxy:
            self.proxy.heartbeat()
            self.set_heartbeat_timer()

    def __getattr__(self, attr):
        '''deletegate all other calls to the xmlrpc client.
        wait if the server process is still runnning, but not responding
        if the server process has died, restart it'''
        def wrapper(*args, **kwargs):
            if not self.proxy:
                self.restart()
                time.sleep(0.2)
            method = getattr(self.proxy, attr)
            result = None
            tries = 0
            while tries < RETRY_CONNECTION_LIMIT:
                try:
                    result = method(*args, **kwargs)
                    break
                except Exception:
                    tries += 1
                    if self.proc.poll() is None:
                        # just retry
                        time.sleep(0.2)
                    else:
                        # died, restart and retry
                        self.restart()
                        time.sleep(0.2)
            return result
        return wrapper


def proxy_for(view):
    '''retrieve an existing proxy for an external Python process.
    will automatically create a new proxy if non exists for the
    requested interpreter'''
    proxy = None
    with PROXY_LOCK:
        python = get_setting("python_interpreter", view, "")
        if python == "":
            python = "python"
        if python in PROXIES:
            proxy = PROXIES[python]
        else:
            proxy = Proxy(python)
            PROXIES[python] = proxy
    return proxy


def root_folder_for(view):
    '''returns the folder open in ST which contains
    the file open in this view. Used to determine the
    rope project directory (assumes directories open in
    ST == project directory)'''
    def in_directory(file_path, directory):
        directory = os.path.realpath(directory)
        file_path = os.path.realpath(file_path)
        return os.path.commonprefix([file_path, directory]) == directory
    file_name = view.file_name()
    root_path = None
    if file_name in ROOT_PATHS:
        root_path = ROOT_PATHS[file_name]
    else:
        for folder in view.window().folders():
            if in_directory(file_name, folder):
                root_path = folder
                ROOT_PATHS[file_name] = root_path

        # no folders found -> single file project
        if root_path is None:
            root_path = NO_ROOT_PATH
    return root_path


class PythonStopServerCommand(sublime_plugin.WindowCommand):
    '''stops the server this view is connected to. unused'''
    def run(self, *args):
        with PROXY_LOCK:
            python = get_setting("python_interpreter", "")
            if python == "":
                python = "python"
            proxy = PROXIES.get(python, None)
            if proxy:
                proxy.stop()
                del PROXIES[python]


class PythonCompletionsListener(sublime_plugin.EventListener):
    '''Retrieves completion proposals from external Python
    processes running Rope'''
    def on_query_completions(self, view, prefix, locations):
        if not view.match_selector(locations[0], 'source.python'):
            return []
        path = view.file_name()
        source = view.substr(sublime.Region(0, view.size()))
        loc = locations[0]
        # t0 = time.time()
        proxy = proxy_for(view)
        proposals = proxy.completions(source, root_folder_for(view), path, loc)
        # proposals = (
        #   proxy.profile_completions(source, root_folder_for(view), path, loc)
        # )
        # print("+++", time.time() - t0)
        if proposals:
            completion_flags = (
                sublime.INHIBIT_WORD_COMPLETIONS |
                sublime.INHIBIT_EXPLICIT_COMPLETIONS
            )
            return (proposals, completion_flags)
        return proposals

    def on_post_save_async(self, view, *args):
        proxy = proxy_for(view)
        path = view.file_name()
        proxy.report_changed(root_folder_for(view), path)


class PythonGetDocumentationCommand(sublime_plugin.WindowCommand):
    '''Retrieves the docstring for the identifier under the cursor and
    displays it in a new panel.'''
    def run(self):
        view = self.window.active_view()
        row, col = view.rowcol(view.sel()[0].a)
        offset = view.text_point(row, col)
        path = view.file_name()
        source = view.substr(sublime.Region(0, view.size()))
        if view.substr(offset) in [u'(', u')']:
            offset = view.text_point(row, col - 1)

        proxy = proxy_for(view)
        doc = proxy.documentation(source, root_folder_for(view), path, offset)
        if doc:
            open_pydoc_in_view = get_setting("open_pydoc_in_view")
            if open_pydoc_in_view:
                self.display_docs_in_view(doc)
            else:
                self.display_docs_in_panel(view, doc)
        else:
            word = view.substr(view.word(offset))
            self.notify_no_documentation(view, word)

    def notify_no_documentation(self, view, word):
        view.set_status(
            "rope_documentation_error",
            "No documentation found for %s" % word
        )

        def clear_status_callback():
            view.erase_status("rope_documentation_error")
        sublime.set_timeout_async(clear_status_callback, 5000)

    def display_docs_in_panel(self, view, doc):
        out_view = view.window().get_output_panel(
            "rope_python_documentation")
        out_view.run_command("simple_clear_and_insert", {"insert_string": doc})
        view.window().run_command(
            "show_panel", {"panel": "output.rope_python_documentation"})

    def display_docs_in_view(self, doc):
        create_view_in_same_group = get_setting("create_view_in_same_group")

        v = self.find_pydoc_view()
        if not v:
            active_group = self.window.active_group()
            if not create_view_in_same_group:
                if self.window.num_groups() == 1:
                    self.window.run_command('new_pane', {'move': False})
                if active_group == 0:
                    self.window.focus_group(1)
                else:
                    self.window.focus_group(active_group-1)

            self.window.new_file(sublime.TRANSIENT)
            v = self.window.active_view()
            v.set_name("*pydoc*")
            v.set_scratch(True)

        v.set_read_only(False)
        v.run_command("simple_clear_and_insert", {"insert_string": doc})
        v.set_read_only(True)
        self.window.focus_view(v)

    def find_pydoc_view(self):
        '''
        Return view named *pydoc* if exists, None otherwise.
        '''
        for w in self.window.views():
            if w.name() == "*pydoc*":
                return w
        return None


class PythonGotoDefinitionCommand(sublime_plugin.WindowCommand):
    '''
    Shows the definition of the identifier under the cursor, project-wide.
    '''
    def run(self, *args):
        view = self.window.active_view()
        row, col = view.rowcol(view.sel()[0].a)
        offset = view.text_point(row, col)
        path = view.file_name()
        source = view.substr(sublime.Region(0, view.size()))
        if view.substr(offset) in [u'(', u')']:
            offset = view.text_point(row, col - 1)

        proxy = proxy_for(view)
        def_result = proxy.definition_location(
            source, root_folder_for(view), path, offset)

        if not def_result or def_result == [None, None]:
            return

        target_path, target_lineno = def_result
        current_lineno = view.rowcol(view.sel()[0].end())[0] + 1

        if None not in (path, target_path, target_lineno):
            self.save_pos(view.file_name(), current_lineno)
            path = target_path + ":" + str(target_lineno)
            self.window.open_file(path, sublime.ENCODED_POSITION)
        elif target_lineno is not None:
            self.save_pos(view.file_name(), current_lineno)
            path = view.file_name() + ":" + str(target_lineno)
            self.window.open_file(path, sublime.ENCODED_POSITION)
        else:
            # fail silently (user selected whitespace, etc)
            pass

    def save_pos(self, file_path, lineno):
        GOTO_STACK.append((file_path, lineno))


class PythonGoBackCommand(sublime_plugin.WindowCommand):
    def run(self, *args):
        if GOTO_STACK:
            file_name, lineno = GOTO_STACK.pop()
            path = file_name + ":" + str(lineno)
            self.window.open_file(path, sublime.ENCODED_POSITION)


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

