import os
import sys
import time
import logging
import tempfile
import threading

# add path above SublimePythonIDE to sys.path to be able to do the same
# relative import as the plugin itself does
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from linter import do_linting

# furthermore, modify sys.path to import the correct rope version
if sys.version_info[0] == 2:
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "lib", "python2"))
    from SimpleXMLRPCServer import SimpleXMLRPCServer
    from xmlrpclib import Binary
else:
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "lib", "python3"))
    from xmlrpc.server import SimpleXMLRPCServer
    from xmlrpc.client import Binary

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "lib", "python_all"))

import jedi

from rope.base import libutils
from rope.base.project import Project
from rope.refactor.rename import Rename
from rope.refactor.extract import ExtractMethod
from rope.refactor.importutils import ImportTools
from rope.base.exceptions import ModuleSyntaxError
from rope.contrib.codeassist import (
    get_doc, get_definition_location
)

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
        self.buffer_tmpfile_map = {}
        self.tempfiles = []

    def __del__(self):
        '''Cleanup temporary files when server is deallocated. Although
        Python destructors are not guaranteed to be run it is still ok to
        do cleanup here, as a tempfile surviving the server in TEMPDIR
        is not too big of a problem.'''
        for tfn in self.tempfiles:
            os.unlink(tfn)

    def project_for(self, project_path, file_path, source=""):
        # scratch buffer case: create temp file and proj for buffer and cache it
        if file_path.startswith("BUFFER:"):
            if file_path in self.projects:
                project = self.projects[file_path]
                file_path = self.buffer_tmpfile_map[file_path]
            else:
                original_file_path = file_path
                file_path = self._create_temp_file(source)
                project = self._create_single_file_project(file_path)
                self.projects[original_file_path] = project
                self.buffer_tmpfile_map[original_file_path] = file_path

        # single file case (or scratch buffer with client not sending buffer_id)
        # create temp file and proj, and buffer if file_name given
        elif project_path == NO_ROOT_PATH:
            if file_path in self.projects:
                project = self.projects[file_path]
            else:
                if not file_path:
                    # this path is deprecated and should not be used anymore
                    file_path = self._create_temp_file(source)
                    project = self._create_single_file_project(file_path)
                else:
                    project = self._create_single_file_project(file_path)
                    self.projects[file_path] = project

        # "usual" case: a real file with a project directory is given
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
        Creates a temporary named file for use by Rope. It expects to
        be able to read files from disk in some places, so there is no
        easy way around creating these files. We try to delete those
        files in the servers destructor (see __del__).
        """
        tmpfile = tempfile.NamedTemporaryFile(delete=False)
        tmpfile.write(content.encode("utf-8"))

        tf_path = tmpfile.name
        self.tempfiles.append(tf_path)

        tmpfile.close()
        return tf_path


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
            row, col = loc
            row += 1
            script = jedi.Script(source, row, col, file_path)
            proposals = script.completions()
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

        jedi.cache.clear_time_caches()
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

        real_path, def_lineno = (None, None)
        try:
            def_resource, def_lineno = get_definition_location(
                project, source, loc, resource=resource, maxfixes=3)
            if def_resource:
                real_path = def_resource.real_path
        except ModuleSyntaxError:
            pass

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

    def rename(self, project_path, file_path, loc, source, new_name):
        project, resource = self._get_resource(project_path, file_path, source)
        rename = Rename(project, resource, loc)
        changes = rename.get_changes(new_name, in_hierarchy=True)
        project.do(changes)

    def extract_method(self, project_path, file_path, start, end, source, new_name):
        project, resource = self._get_resource(project_path, file_path, source)
        rename = ExtractMethod(project, resource, start, end)
        changes = rename.get_changes(new_name)
        project.do(changes)

    def organize_imports(self, source, project_path, file_path):
        """
        Organize imports in source

        :param source: the document source
        :param project_path: the actual project_path
        :param file_path: the actual file path
        :returns: a string containing the source with imports fully organized
        """
        project, resource = self._get_resource(project_path, file_path, source)
        pycore = project.pycore
        import_tools = ImportTools(pycore)
        pymodule = pycore.resource_to_pyobject(resource)
        organized_source = import_tools.organize_imports(pymodule)
        return organized_source

    def _proposal_string(self, p):
        """
        Build and return a string for the proposals of completions

        :param p: the original proposal structure
        """

        return '{result}\t({type})'.format(
            result=p.name, type=p.type)

    def _insert_string(self, p):
        """
        """

        result = p.name
        return result

    def _get_resource(self, project_path, file_path, source):
        """Get and returns project and resource objects from Rope library
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
        logging.debug('bumbum %f', last_heartbeat)


class LinterMixin(object):
    """
    Performs a PyFlakes and PEP8 check on the input code, returns either a
    list of messages or a single syntax error in case of an error while
    parsing the code. The receiver thus has to check for these two
    cases.
    """

    def check_syntax(self, code, encoding, lint_settings, filename):
        '''The linting mixin does not use the project_for machinery,
        but uses the linters directy.'''
        try:
            codes = do_linting(lint_settings, code, encoding, filename)
        except Exception:
            import traceback
            sys.stderr.write(traceback.format_exc())

        import pickle
        ret = Binary(pickle.dumps(codes))
        return ret


class Server(RopeProjectMixin, HeartBeatMixin,
             RopeFunctionsMixin, LinterMixin):
    """
    Python's SimpleXMLRPCServer accepts just one call of
    register_instance(), so this class just combines the above
    mixins.
    """

    def __init__(self):
        RopeProjectMixin.__init__(self)
        RopeFunctionsMixin.__init__(self)
        HeartBeatMixin.__init__(self)
        LinterMixin.__init__(self)


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

        # enable debugging?
        if self.debug:
            sys.stderr.write("SublimePythonIDE Server is starting in Debug mode\n")
            self.server.register_instance(DebuggingServer())
        else:
            self.server.register_instance(Server())

        self.server.serve_forever()


if __name__ == '__main__':
    try:
        # single argument to this process should be the port to listen on
        port = int(sys.argv[1])
        # second argument may be "--debug" in which case the server prints to stderr
        debug = False
        if len(sys.argv) > 2 and sys.argv[2].strip() == "--debug":
            debug = True

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
