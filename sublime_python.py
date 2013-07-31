import sys
import os
import socket
import time
import subprocess
import threading
import xmlrpc.client
import sublime
import sublime_plugin
from queue import Queue

from SublimePythonIDE.util import AsynchronousFileReader, DebugProcDummy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))

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

# debugging, see documentation of Proxy.restart()
DEBUG_PORT = None
SERVER_DEBUGGING = False


# Constants
SERVER_SCRIPT = os.path.join(
    os.path.dirname(__file__), "server", "server.py")

RETRY_CONNECTION_LIMIT = 5
HEARTBEAT_INTERVALL = 9
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
        self.stderr_reader = None
        self.queue = None
        self.restart()

    def get_free_port(self):
        s = socket.socket()
        s.bind(('', 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def restart(self):
        ''' (re)starts a Python IDE-server
        this method is complicated by SublimePythonIDE having two different debug modes,
            - one in which the server is started manually by the developer, in which case this
            developer has to set the DEBUG_PORT constant
            - and one case where the server is started automatically but in a verbose mode,
            in which it prints to its stderr, which is copied to ST3's console by an
            AsynchronousFileReader. For this the developer has to set SERVER_DEBUGGING to True
        '''
        try:
            if DEBUG_PORT is not None:
                # debug mode one
                self.port = DEBUG_PORT
                self.proc = DebugProcDummy()
                print("started server on user-defined FIXED port %i with %s" % (self.port, self.python))
            elif SERVER_DEBUGGING:
                # debug mode two
                self.port = self.get_free_port()
                proc_args = '"%s" "%s" %i' % (self.python, SERVER_SCRIPT, self.port)
                proc_args += " --debug"
                self.proc = subprocess.Popen(proc_args, shell=True, stderr=subprocess.PIPE)
                self.queue = Queue()
                self.stderr_reader = AsynchronousFileReader("Server on port %i - STDERR" % self.port, self.proc.stderr, self.queue)
                self.stderr_reader.start()
                sublime.set_timeout_async(self.debug_consume, 1000)
                print("started server on port %i with %s IN DEBUG MODE" % (self.port, self.python))
            else:
                # standard run of the server in end-user mode
                self.port = self.get_free_port()
                proc_args = '"%s" "%s" %i' % (self.python, SERVER_SCRIPT, self.port)
                self.proc = subprocess.Popen(proc_args, shell=True)
                print("started server on port %i with %s" % (self.port, self.python))

            # in any case, we also need a local client object
            self.proxy = xmlrpc.client.ServerProxy(
                'http://localhost:%i' % self.port, allow_none=True)
            self.set_heartbeat_timer()
        except OSError as e:
            print("error starting server:", e)
            raise e

    def debug_consume(self):
        '''
        If SERVER_DEBUGGING is enabled, is called by ST every 1000ms and prints
        output from server debugging readers.
        '''
        # Check the queues if we received some output (until there is nothing more to get).
        while not self.queue.empty():
            line = self.queue.get
            ()
            print(str(line))
        # Sleep a bit before asking the readers again.
        sublime.set_timeout_async(self.debug_consume, 1000)

    def set_heartbeat_timer(self):
        sublime.set_timeout_async(
            self.send_heartbeat, HEARTBEAT_INTERVALL * 1000)

    def stop(self):
        self.proxy = None
        self.queue = Queue()
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
            try:
                proxy = Proxy(python)
            except OSError:
                pass
            else:
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
        window = view.window()
        for folder in window.folders():
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
        if not proxy:
            return []
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
        if not proxy:
            return
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
        if not proxy:
            return
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
        if not proxy:
            return
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
