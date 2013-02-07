import sys
import os
import socket
import time
import itertools
import subprocess
import pipes
import xmlrpc.client
import sublime
import sublime_plugin

# contains root paths per view, see root_folder_for()
ROOT_PATHS = {}
# contains proxy objects representing external python processes, per interpreter used
PROXIES = {}
# Stors errors found by PyFlask
ERRORS_BY_LINE = {}

# Constants
SERVER_SCRIPT = pipes.quote(os.path.join(os.path.dirname(__file__), "completion_server.py"))
HEARTBEAT_FREQUENCY = 9
DRAW_TYPE = 4 | 32


def get_setting(key, default_value=None):
    try:
        settings = sublime.active_window().active_view().settings()
        if settings.has(key):
            return settings.get(key)
    except:
        pass
    s = sublime.load_settings('SublimePython.sublime-settings')
    return s.get(key, default_value)

class Proxy(object):
    '''Abstracts the external Python processes that perform the actual
    functionality. SublimePython just calls local methods on Proxy objects.
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
        self.port = self.get_free_port()
        self.proc = subprocess.Popen(
                "%s %s %i" % (self.python, SERVER_SCRIPT, self.port),
                shell=True)
        self.proxy = xmlrpc.client.ServerProxy('http://localhost:%i' % self.port)
        sublime.set_timeout_async(self.send_heartbeat, HEARTBEAT_FREQUENCY * 1000)

    def stop(self):
        self.proxy = None
        self.proc.terminate()

    def send_heartbeat(self):
        if self.proxy:
            self.proxy.heartbeat()
            sublime.set_timeout_async(self.send_heartbeat, HEARTBEAT_FREQUENCY * 1000)

    def __getattr__(self, attr):
        '''deletegate all other calls to the xmlrpc client.
        wait if the server process is still runnning, but not responding
        if the server process has died, restart it'''
        def wrapper(*args, **kwargs):
            method = getattr(self.proxy, attr)
            result = None
            tries = 0
            while tries < 5:
                try:
                    result = method(*args, **kwargs)
                    break
                except Exception as e:
                    tries += 1
                    print(e)
                    if self.proc.poll() is None:
                        # just retry
                        print("retrying in 0.5s")
                        time.sleep(0.1)
                    else:
                        # died, restart and retry
                        print("restarting")
                        self.restart()
            return result
        return wrapper

def proxy_for(view):
    python = get_setting("python_interpreter", "")
    if python == "":
        python = "python"
    if python in PROXIES:
        print("existing proxy for python %s" % python)
        proxy = PROXIES[python]
    else:
        print("started server for %s" % python)
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
    print("Root for %s is %s" % (file_name, root_path))
    return root_path


class PythonStopServerCommand(sublime_plugin.WindowCommand):
    '''TODO: update to Proxy'''
    def run(self, *args):
        python = get_setting("python_interpreter", "")
        if python == "":
            python = "python"
        proxy = PROXIES.get(python, None)
        if proxy:
            proxy.stop()
            del proxy[python]
            print("terminated server for %s" % python)

class PythonCheckSyntaxListener(sublime_plugin.EventListener):
    def on_load_async(self, view):
        '''Check the file syntax on load'''
        if not 'Python' in view.settings().get('syntax') or view.is_scratch():
            return
        self._check(view)

    def on_post_save_async(self, view):
        """
        Check file syntax on save if autoimport improvements are setted on.
        Updates Rope's database in response to events (e.g. post_save)
        """
        if not 'Python' in view.settings().get('syntax') or view.is_scratch():
            return
        self._check(view)

    def on_selection_modified_async(self, view):
        if (not 'Python' in view.settings().get('syntax')
                or not get_setting('pyflakes_linting', True)):
            return

        vid = view.id()
        errors_by_line = ERRORS_BY_LINE.get(vid, None)

        if not errors_by_line:
            view.erase_status('sublimerope-errors')
            return

        lineno = view.rowcol(view.sel()[0].end())[0] + 1
        if lineno in errors_by_line.keys():
            view.set_status('sublimerope-errors', '; '.join(
                [m['message'] % m['message_args'] for m in errors_by_line[lineno]]
            ))
        else:
            view.erase_status('sublimerope-errors')

    def _check(self, view):
        if not get_setting('pyflakes_linting', True):
            return

        proxy = proxy_for(view)
        check_result = proxy.check_syntax(view.substr(sublime.Region(0, view.size())))
        # the result of a flakes check can be a list of errors, or single syntax exception
        if isinstance(check_result, list):
            by_line = lambda e: e['lineno']
            errors = sorted(check_result, key=by_line)
            errors_by_line = {}
            for k, g in itertools.groupby(errors, by_line):
                errors_by_line[k] = list(g)
            ERRORS_BY_LINE[view.id()] = errors_by_line
            self.visualize_errors(view, errors)
        else:
            self.handle_syntax_exception(view, check_result)

    def visualize_errors(self, view, errors):
        view.erase_regions('sublimerope-errors')
        errors_by_line = ERRORS_BY_LINE[view.id()]

        outlines = [view.line(view.text_point(lineno - 1, 0))
                    for lineno in errors_by_line.keys()]

        if outlines:
            view.add_regions(
                'sublimerope-errors', outlines, 'keyword', 'dot',
                DRAW_TYPE)
        else:
            view.erase_regions("sublimerope-errors")

    def handle_syntax_exception(self, view, e):
        if not get_setting('pyflakes_linting', True):
            return
        (lineno, offset, text) = e.lineno, e.offset, e.text

        if text is None:
            print >> sys.stderr, "SublimeRope problem decoding src file %s" % (
                self.filename,)
        else:
            line = text.splitlines()[-1]
            if offset is not None:
                offset = offset - (len(text) - len(line))

            view.erase_regions('sublimerope-errors')
            if offset is not None:
                text_point = view.text_point(lineno - 1, 0) + offset
                view.add_regions(
                    'sublimerope-errors',
                    [sublime.Region(text_point, text_point + 1)],
                    'keyword', 'dot', DRAW_TYPE)
            else:
                view.add_regions(
                    'sublimerope-errors',
                    [view.line(view.text_point(lineno - 1, 0))],
                    'keyword', 'dot', DRAW_TYPE)

class PythonCompletionsListener(sublime_plugin.EventListener):
    def on_query_completions(self, view, prefix, locations):
        if not view.match_selector(locations[0], 'source.python'):
            return []
        path = view.file_name()
        source = view.substr(sublime.Region(0, view.size()))
        loc = locations[0]
        t0 = time.time()
        proxy = proxy_for(view)
        print("proxy for %s is %s" % (view.id(), str(proxy)))
        proposals = proxy.completions(source, root_folder_for(view), path, loc)
        # proposals = proxy.profile_completions(source, root_folder_for(view), path, loc)
        print("+++", time.time() - t0)
        if proposals:
            completion_flags = sublime.INHIBIT_WORD_COMPLETIONS
            return (proposals, completion_flags)
        return proposals
