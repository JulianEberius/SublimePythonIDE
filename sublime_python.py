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
from functools import wraps
from inspect import getargspec

from SublimePythonIDE import sublime_python_colors

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
# saves the path to the systems default python
SYSTEM_PYTHON = None
# When not using shell=True, Popen and friends
# will popup a console window on Windows.
# Use creationflags to suppress this
CREATION_FLAGS = 0 if os.name != "nt" else 0x08000000

# debugging, see documentation of Proxy.restart()
DEBUG_PORT = None
SERVER_DEBUGGING = False

LAST_ERROR_TIME = None

# Constants
SERVER_SCRIPT = os.path.join(
    os.path.dirname(__file__), "server", "server.py")

RETRY_CONNECTION_LIMIT = 5
HEARTBEAT_INTERVALL = 9
DRAW_TYPE = 4 | 32
NO_ROOT_PATH = -1
DEFAULT_VENV_DIR_NAME = "venv"


def plugin_loaded():
    _update_color_scheme()

    s = sublime.load_settings('SublimePython.sublime-settings')
    s.add_on_change('sublimepython-pref-settings', _update_color_scheme)


def get_setting(key, view=None, default_value=None):
    if view is None:
        view = get_current_active_view()
    try:
        settings = view.settings()
        if settings.has(key):
            return settings.get(key)
    except:
        pass
    s = sublime.load_settings('SublimePython.sublime-settings')
    return s.get(key, default_value)


def override_view_setting(key, value, view):
    view.settings().set(key, value)


def get_current_active_view():
    return sublime.active_window().active_view()


def file_or_buffer_name(view):
    filename = view.file_name()
    if filename:
        return filename
    else:
        return "BUFFER:%i" % view.buffer_id()


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
        self.rpc_lock = threading.Lock()
        self.restart()

    def get_free_port(self):
        s = socket.socket()
        s.bind(('', 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def resolve_localhost(self):
        return socket.gethostbyname("localhost")

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
                print("started server on user-defined FIXED port %i with %s" %
                      (self.port, self.python))
            elif SERVER_DEBUGGING:
                # debug mode two
                self.port = self.get_free_port()
                proc_args = self.python + [SERVER_SCRIPT,
                             str(self.port), " --debug"]
                self.proc = subprocess.Popen(
                    proc_args, cwd=os.path.dirname(self.python[0]),
                    stderr=subprocess.PIPE, creationflags=CREATION_FLAGS)
                self.queue = Queue()
                self.stderr_reader = AsynchronousFileReader(
                    "Server on port %i - STDERR" % self.port,
                    self.proc.stderr, self.queue)
                self.stderr_reader.start()
                sublime.set_timeout_async(self.debug_consume, 1000)
                print("started server on port %i with %s IN DEBUG MODE" %
                      (self.port, self.python))
            else:
                # standard run of the server in end-user mode
                self.port = self.get_free_port()
                proc_args = self.python + [SERVER_SCRIPT, str(self.port)]
                self.proc = subprocess.Popen(
                    proc_args, cwd=os.path.dirname(self.python[0]),
                    creationflags=CREATION_FLAGS)
                print("started server on port %i with %s" %
                      (self.port, self.python))

            # wait 100 ms to make sure python proc is still running
            for i in range(10):
                time.sleep(0.01)
                if self.proc.poll():
                    if SERVER_DEBUGGING:
                        print(sys.exc_info())
                    raise OSError(
                        None, "Python interpretor crashed (using path %s)" %
                        self.python)

            # in any case, we also need a local client object
            self.proxy = xmlrpc.client.ServerProxy(
                'http://%s:%i' % (self.resolve_localhost(), self.port),
                allow_none=True)
            self.set_heartbeat_timer()
        except OSError as e:
            print("error starting server:", e)
            print(
                "-----------------------------------------------------------------------------------------------")
            print(
                "Try to use an absolute path to your projects python interpreter. On Windows try to use forward")
            print(
                "slashes as in C:/Python27/python.exe or properly escape with double-backslashes""")
            print(
                "-----------------------------------------------------------------------------------------------")
            raise e

    def debug_consume(self):
        '''
        If SERVER_DEBUGGING is enabled, is called by ST every 1000ms and prints
        output from server debugging readers.
        '''
        # Check the queues if we received some output (until there is nothing
        # more to get).
        while not self.queue.empty():
            line = self.queue.get()
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
            self.heartbeat()  # implemented in proxy through __getattr__
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

            # multiple ST3 threads may use the proxy (e.g. linting in parallel
            # to heartbeat etc.) XML-RPC client objects are single-threaded
            # only though, so we introduce a lock here
            with self.rpc_lock:
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


def system_python():
    global SYSTEM_PYTHON

    if SYSTEM_PYTHON is None:
        try:
            if os.name == "nt":
                sys_py = subprocess.check_output(
                    ["where", "python"], creationflags=CREATION_FLAGS)
                # use first result where many might return
                sys_py = sys_py.splitlines()[0]
            else:
                sys_py = subprocess.check_output(["which", "python"])
        except OSError:
            # some systems (e.g. Windows XP) do not support where/which
            try:
                sys_py = subprocess.check_output(
                    'python -c "import sys; print sys.executable"',
                    creationflags=CREATION_FLAGS, shell=True)
            except OSError:
                # now we give up
                sys_py = ""
        SYSTEM_PYTHON = sys_py.strip().decode()

    return SYSTEM_PYTHON


def project_venv_python(view):
    """
    Attempt to "guess" the virtualenv path location either in the
    project dir or in WORKON_HOME (for virtualenvwrapper users).

    If such a path is found, and a python binary exists, returns it,
    otherwise returns None.
    """
    dir_name = get_setting("virtualenv_dir_name", view, DEFAULT_VENV_DIR_NAME)
    project_dir = root_folder_for(view)
    if project_dir == NO_ROOT_PATH:
        return None

    venv_path = os.path.join(project_dir, dir_name)
    if not os.path.exists(venv_path):
        # virtualenvwrapper: attempt to guess virtualenv dir by name
        workon_dir = get_setting("workon_home", view, os.environ.get(
            "WORKON_HOME", None))
        if workon_dir:
            workon_dir = os.path.expanduser(workon_dir)
            venv_path = project_dir.split(os.sep)[-1]
            venv_path = os.path.join(workon_dir, venv_path)
            if not os.path.exists(venv_path):
                return None  # no venv path found: abort
        else:
            return None  # no venv path found: abort

    if os.name == "nt":
        python = os.path.join(venv_path, "Scripts", "python.exe")
    else:
        python = os.path.join(venv_path, "bin", "python")

    if os.path.exists(python):
        return python


def shebang_line_python(view):
    shebang_line = view.substr(view.line(0))
    if shebang_line.startswith('#!'):
        return shebang_line[2:].split(None, 1)


def normalize_path(args, make_abs=False):
    if not args:
        return None
    elif type(args) is str:
        args = [args]
    elif type(args) is not list:
        args = list(args)

    # args is guaranteed to be a non-empty list at this point
    if make_abs:
        args[0] = os.path.abspath(os.path.realpath(os.path.expanduser(args[0])))
    return args


def proxy_for(view):
    '''retrieve an existing proxy for an external Python process.
    will automatically create a new proxy if none exists for the
    requested interpreter'''
    proxy = None

    python_detectors = [
        lambda: normalize_path(get_setting("python_interpreter", view, ""), True),
        lambda: normalize_path(project_venv_python(view)),
        lambda: normalize_path(shebang_line_python(view)),
        lambda: normalize_path(system_python())
    ]
    with PROXY_LOCK:
        for detector in python_detectors:
            python = detector()
            if python is not None:
                break

        if not python or not os.path.exists(python[0]):
            show_python_not_found_error(python_detectors)
            return

        # Since lists cannot be used as keys, a temporary tuple version of this is created.
        python_as_key = tuple(python)
        if python_as_key in PROXIES:
            proxy = PROXIES[python_as_key]
        else:
            try:
                proxy = Proxy(python)
            except OSError:
                pass
            else:
                PROXIES[python_as_key] = proxy
    return proxy


def show_python_not_found_error(python_detectors):
    global LAST_ERROR_TIME
    if LAST_ERROR_TIME is not None and (time.time() < LAST_ERROR_TIME + 10.0):
        return
    LAST_ERROR_TIME = time.time()

    msg = (
        "SublimePythonIDE: Could not find Python.\n"
        "Make sure Python is accessible via one of these methods:\n"
        "\n"
        " \xb7 From SublimePythonIDE settings:\n"
        "   %r\n"
        " \xb7 From venv settings:\n"
        "   %r\n"
        " \xb7 From #! (shebang) line in this file:\n"
        "   %r\n"
        " \xb7 From system Python (via $PATH):\n"
        "   %r\n"
        "\n"
        "We use the first non-None value and ensure that the path exists before proceeding.\n"
        % tuple(d() for d in python_detectors)
    )

    if not get_setting("suppress_python_not_found_error", False):
        result = sublime.yes_no_cancel_dialog(
            msg +
            "\n"
            "\"Do Not Show Again\" suppresses this dialog until next launch. "
            "\"More Info\" shows help for configuring Python or permanently suppressing this dialog.",
            "More Info", "Do Not Show Again"
        )
        # In case the user takes more than 10 seconds to react to the dialog
        LAST_ERROR_TIME = time.time()
        if result == sublime.DIALOG_YES:
            import webbrowser
            webbrowser.open("https://github.com/JulianEberius/SublimePythonIDE#configuration")
        elif result == sublime.DIALOG_NO:
            LAST_ERROR_TIME = float("inf")

    raise OSError(
        msg +
        "More info: https://github.com/JulianEberius/SublimePythonIDE#configuration"
    )


def root_folder_for(view):
    '''returns the folder open in ST which contains
    the file open in this view. Used to determine the
    rope project directory (assumes directories open in
    ST == project directory)

    In addition to open directories in project, the
    lookup uses directory set in setting "src_root" as
    the preferred root (in cases project directory is
    outside of root python package).
    '''
    def in_directory(file_path, directory):
        directory = os.path.realpath(directory)
        file_path = os.path.realpath(file_path)
        return os.path.commonprefix([file_path, directory]) == directory
    file_name = file_or_buffer_name(view)
    root_path = None
    if file_name in ROOT_PATHS:
        root_path = ROOT_PATHS[file_name]
    else:
        window = view.window()
        for folder in [get_setting(
                "src_root", view, None)] + window.folders():
            if not folder:
                continue
            folder = os.path.expanduser(folder)
            if in_directory(file_name, folder):
                root_path = folder
                ROOT_PATHS[file_name] = root_path
                break  # use first dir found

        # no folders found -> single file project
        if root_path is None:
            root_path = NO_ROOT_PATH

    return root_path

'''Utilities'''


def _is_python_syntax(view):
    """Return true if we are in a Python syntax defined view
    """

    syntax = view.settings().get('syntax')
    return bool(syntax and ("Python" in syntax))


def python_only(func):
    """Decorator that make sure we call the given function in python only
    If func has only one argument we assume it to be the view.
    If it has more than one argument, we assume the view to be the second argument,
    the first usually being "self".
    """

    num_args = len(getargspec(func).args)

    if num_args == 1:
        @wraps(func)
        def wrapper1(arg):
            if isinstance(arg, sublime_plugin.WindowCommand):
                view = arg.window.active_view()
            elif isinstance(arg, sublime_plugin.TextCommand):
                view = arg.view
            elif type(arg) == sublime.View:
                view = arg
            elif type(arg) == sublime.Window:
                view = arg.active_view()
            else:
                view = None
            if view is not None and _is_python_syntax(view) and not view.is_scratch():
                return func(arg)
        return wrapper1

    else:
        @wraps(func)
        def wrapperN(self, view, *args):
            if type(view) == sublime.View and _is_python_syntax(view) and not view.is_scratch():
                return func(self, view, *args)
        return wrapperN


def _update_color_scheme():
    '''Updates the current color scheme to include error and warning
    scopes used by the linting features'''

    colors = {
        "warning": get_setting("warning_color", default_value="EDBA00"),
        "error": get_setting("error_color", default_value="DA2000")
    }
    sublime_python_colors.update_color_scheme(colors)


class SimpleClearAndInsertCommand(sublime_plugin.TextCommand):

    '''utility command class for writing into the documentation view'''

    def run(self, edit, block=False, **kwargs):
        doc = kwargs['insert_string']
        r = sublime.Region(0, self.view.size())
        self.view.erase(edit, r)
        self.view.insert(edit, 0, doc)


class AsynchronousFileReader(threading.Thread):

    '''
    Helper class to implement asynchronous reading of a file
    in a separate thread. Pushes read lines on a queue to
    be consumed in another thread.

    Used for reading stderr output of the server.
    '''

    def __init__(self, name, fd, queue):
        threading.Thread.__init__(self)
        self.name = name
        self._fd = fd
        self._queue = queue

    def run(self):
        '''The body of the tread: read lines and put them on the queue.'''
        for line in iter(self._fd.readline, ''):
            if line:
                self._queue.put("{0}: {1}".format(self.name, line))


class DebugProcDummy(object):

    """Used only for debugging, when the server process is started externally
    """
    def poll(*args):
        return None

    def terminate():
        pass
