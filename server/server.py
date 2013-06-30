import sys
import os
import threading
import time
import tempfile

if sys.version_info[0] == 2:
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "lib", "python2"))
    from SimpleXMLRPCServer import SimpleXMLRPCServer
else:
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "lib", "python3"))
    from xmlrpc.server import SimpleXMLRPCServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from pyflakes.checker import Checker

from rope.base.project import Project
from rope.base import libutils
from rope.base.ast import parse
from rope.contrib.codeassist import (
    code_assist, sorted_proposals, get_doc, get_definition_location
)
from rope.base.exceptions import ModuleSyntaxError


# global state of the server process
last_heartbeat = None
# constants
HEARTBEAT_TIMEOUT = 19
NO_ROOT_PATH = -1


class RopeProjectMixin(object):
    """
    Creates and manages Rope projects"""

    def __init__(self):
        self.projects = {}

    def project_for(self, project_path, file_path, source=""):
        if project_path == NO_ROOT_PATH:
            if file_path in self.projects:
                project = self.projects[file_path]
            else:
                if not file_path:
                    tmp_file = self._create_temp_file(source)
                    file_path = tmp_file.name
                    project = self._create_single_file_project(file_path)
                    # attach the tmp file to the project so that it lives
                    # at least as long as the project object
                    project.tmp_file = tmp_file
                else:
                    project = self._create_single_file_project(file_path)
                    self.projects[file_path] = project
        else:
            if project_path in self.projects:
                project = self.projects[project_path]
            else:
                project = self._create_project(project_path)
                self.projects[project_path] = project
        return project, file_path

    def list_projects(self):
        return self.projects.keys()

    def _create_project(self, path):
        project = Project(path, fscommands=None, ropefolder=None)
        return project

    def _create_single_file_project(self, path):
        folder = os.path.dirname(path)
        ignored_res = os.listdir(folder)
        ignored_res.remove(os.path.basename(path))

        project = Project(
            folder, ropefolder=None,
            ignored_resources=ignored_res, fscommands=None)
        return project

    def _create_temp_file(self, content):
        """
        Creates a temporary file that is return in an opened state,
        and kept open. It is later closed when the project it is
        attached to is deallocated.
        """

        tmpfile = tempfile.NamedTemporaryFile()
        tmpfile.write(content.encode("utf-8"))

        return tmpfile


class RopeFunctionsMixin(object):
    """Uses Rope to generate completion proposals, depends on RopeProjectMixin
    """

    def profile_completions(self, source, project_path, file_path, loc):
        """
        Only for testing purposes::
            runs Rope's code completion functionality in the python profiler
            and saves statistics, then reruns for actual results
        """

        try:
            import cProfile as profile
        except:
            import profile

        profile.runctx(
            "self.completions(source, project_path, file_path, loc)",
            globals(), locals(), os.path.expanduser("~/SublimePython.stats"))

        return self.completions(source, project_path, file_path, loc)

    def completions(self, source, project_path, file_path, loc):
        """
        Get completions from the underlying Rope library and returns it back
        to the editor interface

        :param source: the document source
        :param project_path: the actual project_path
        :param file_path: the actual file path
        :param loc: the buffer location
        :returns: a list of tuples of strings
        """

        project, resource = self._get_resource(project_path, file_path, source)

        try:
            proposals = code_assist(
                project, source, loc, resource=resource, maxfixes=3)
            proposals = sorted_proposals(proposals)
        except ModuleSyntaxError:
            proposals = []
        except Exception:
            import traceback
            traceback.print_exc()
            proposals = []
        finally:
            proposals = [
                (self._proposal_string(p), self._insert_string(p))
                for p in proposals if p.name != 'self='
            ]

        return proposals

    def documentation(self, source, project_path, file_path, loc):
        """
        Search for documentation about the word in the current location

        :param source: the document source
        :param project_path: the actual project_path
        :param file_path: the actual file path
        :param loc: the buffer location
        :returns: a string containing the documentation
        """

        project, resource = self._get_resource(project_path, file_path, source)

        try:
            doc = get_doc(project, source, loc, resource=resource, maxfixes=3)
        except ModuleSyntaxError:
            doc = None

        return doc

    def definition_location(self, source, project_path, file_path, loc):
        """
        Get a global definition location and returns it back to the editor

        :param source: the document source
        :param project_path: the actual project_path
        :param file_path: the actual file path
        :param loc: the buffer location
        :returns: a tuple containing the path and the line number
        """

        project, resource = self._get_resource(project_path, file_path, source)

        try:
            def_resource, def_lineno = get_definition_location(
                project, source, loc, resource=resource, maxfixes=3)
        except ModuleSyntaxError:
            real_path, def_lineno = (None, None)
        finally:
            real_path = def_resource.real_path

        return real_path, def_lineno

    def report_changed(self, project_path, file_path):
        """
        Reports the change of the contents of file_path.

        :param project_path: the actual project path
        :param file_path: the file path
        """

        if project_path != NO_ROOT_PATH:
            project, file_path = self.project_for(project_path, file_path)
            libutils.report_change(project, file_path, "")

    def _proposal_string(self, p):
        """
        Build and return a string for the proposals of completions

        :param p: the original proposal structure
        """

        if p.parameters:
            params = [par for par in p.parameters if par != 'self']
            result = '{name}({params})'.format(
                name=p.name,
                params=', '.join(param for param in params)
            )
        else:
            result = p.name

        return '{result}\t({scope}, {type})'.format(
            result=result, scope=p.scope, type=p.type)

    def _insert_string(self, p):
        """
        """

        if p.parameters:
            params = [par for par in p.parameters if par != 'self']
            param_snippet = ", ".join(
                "${%i:%s}" %
                (idx + 1, param) for idx, param in enumerate(params))
            result = "%s(%s)" % (p.name, param_snippet)
        else:
            result = p.name

        return result

    def _get_resource(self, project_path, file_path, source):
        """Get and returns back project and resource objects from Rope library
        """

        project, file_path = self.project_for(project_path, file_path, source)
        return project, libutils.path_to_resource(project, file_path)


class HeartBeatMixin(object):
    """
    Waits for heartbeat messages from SublimeText. The main thread
    kills the process if no heartbeat arrived in HEARTBEAT_TIMEOUT seconds.
    """

    def __init__(self):
        self.heartbeat()

    def heartbeat(self):
        global last_heartbeat
        last_heartbeat = time.time()


class FlakeMixin(object):
    """
    Performs a PyFlakes check on the input code, returns either a
    list of messages or a single syntax error in case of an error while
    parsing the code. The receiver thus has to check for these two
    cases.
    """

    def check_syntax(self, code):
        try:
            tree = parse(code)
        except (SyntaxError, IndentationError, ValueError) as e:
            return {"lineno": e.lineno, "offset": e.offset, "text": e.text}
        else:
            return Checker(tree).messages


class Server(RopeProjectMixin, RopeFunctionsMixin, HeartBeatMixin, FlakeMixin):
    """
    Python's SimpleXMLRPCServer accepts just one call of
    register_instance(), so this class just combines the above
    mixins.
    """

    def __init__(self):
        RopeProjectMixin.__init__(self)
        RopeFunctionsMixin.__init__(self)
        HeartBeatMixin.__init__(self)
        FlakeMixin.__init__(self)

class DebuggingServer(Server):
    """
    Prints calls and exceptions to stderr
    """

    def __init__(self):
        Server.__init__(self)

    def _dispatch(self, method, params):
        try:
            sys.stderr.write("SublimePythonIDE Server is called: %s\n" % str(method))
            method = getattr(self, method)
            return method(*params)
        except Exception as e:
            sys.stderr.write("SublimePythonIDE Server Error: %s\n" % str(e))
            import traceback
            traceback.print_exc()

class XMLRPCServerThread(threading.Thread):
    """
    Runs a SimpleXMLRPCServer in a new thread, so that the main
    thread can watch for the heartbeats and kill the process if no
    heartbeat messages arrive in time

    :param port: the port where to listen to
    :type port: int
    """

    def __init__(self, port, debug):
        threading.Thread.__init__(self)
        self.port = port
        self.daemon = True
        self.debug = debug

    def run(self):
        self.server = SimpleXMLRPCServer(
            ("localhost", port), allow_none=True, logRequests=False)
        if self.debug:
            self.server.register_instance(DebuggingServer())
            sys.stderr.write("SublimePythonIDE Server is starting in Debug mode\n")
        else:
            self.server.register_instance(Server())
        self.server.serve_forever()


if __name__ == '__main__':
        # single argument to this process should be the port to listen on
        try:
            port = int(sys.argv[1])
            if len(sys.argv) > 2:
                debug = True if int(sys.argv[2]) else False
            else:
                debug = False
            # the SimpleXMLRPCServer is run in a new thread
            server_thread = XMLRPCServerThread(port, debug)
            server_thread.start()
            # the main thread checks for heartbeat messages
            while 1:
                time.sleep(HEARTBEAT_TIMEOUT)
                if time.time() - last_heartbeat > HEARTBEAT_TIMEOUT:
                    sys.exit()
        except Exception as e:
            sys.stderr.write("SublimePythonIDE Server Error: %s\n" % str(e))
            import traceback
            traceback.print_exc()
