import sys
import os
import threading
import time

if sys.version_info[0] == 2:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lib/python2"))
    from SimpleXMLRPCServer import SimpleXMLRPCServer
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lib/python3"))
    from xmlrpc.server import SimpleXMLRPCServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../lib"))
from pyflakes.checker import Checker

from rope.base.project import Project
from rope.base import libutils
from rope.base.ast import parse
from rope.contrib.codeassist import code_assist, sorted_proposals, get_doc, get_definition_location
from rope.base.exceptions import ModuleSyntaxError

# global state of the server process
last_heartbeat = None
# constants
HEARTBEAT_TIMEOUT = 10

class RopeProjectMixin(object):
    '''Creates and manages Rope projects'''
    def __init__(self):
        self.projects = {}

    def project_for(self, path):
        if path in self.projects:
            project = self.projects[path]
        else:
            project = self._create_project(path)
            self.projects[path] = project
        return project

    def list_projects(self):
        return self.projects.keys()

    def _create_project(self, path):
        project = Project(path,
            fscommands=None, ropefolder=None)
        return project


class RopeFunctionsMixin(object):
    '''Uses Rope to generate completion proposals, depends on
    RopeProjectMixin'''

    def profile_completions(self, source, project_path, file_path, loc):
        '''Only for testing: runs Rope's code completion functionality in the
        python profiler and saves statistics, then reruns for actual results.'''
        try:
            import cProfile as profile
        except:
            import profile
        profile.runctx(
            "self.completions(source, project_path, file_path, loc)",
            globals(), locals(), os.path.expanduser("~/SublimePython.stats"))
        return self.completions(source, project_path, file_path, loc)

    def completions(self, source, project_path, file_path, loc):
        project = self.project_for(project_path)
        resource = libutils.path_to_resource(project, file_path)
        try:
            proposals = code_assist(project, source, loc,
                resource=resource, maxfixes=3)
            proposals = sorted_proposals(proposals)
        except ModuleSyntaxError:
            proposals = []
        except Exception:
            import traceback
            traceback.print_exc()
            return []

        proposals = [(self._proposal_string(p), self._insert_string(p))
                        for p in proposals
                        if p.name != 'self=']
        return proposals

    def documentation(self, source, project_path, file_path, loc):
        project = self.project_for(project_path)
        resource = libutils.path_to_resource(project, file_path)
        try:
            doc = get_doc(project, source, loc,
                resource=resource, maxfixes=3)
        except ModuleSyntaxError:
            doc = None
        return doc

    def definition_location(self, source, project_path, file_path, loc):
        project = self.project_for(project_path)
        resource = libutils.path_to_resource(project, file_path)
        try:
            def_resource, def_lineno = get_definition_location(
                project, source, loc, resource=resource, maxfixes=3)
        except ModuleSyntaxError:
            return None, None
        return def_resource.real_path, def_lineno

    def _proposal_string(self, p):
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
        if p.parameters:
            params = [par for par in p.parameters if par != 'self']
            param_snippet = ", ".join(
                "${%i:%s}" %
                (idx + 1, param) for idx, param in enumerate(params))
            result = "%s(%s)" %(p.name, param_snippet)
        else:
            result = p.name

        return result


class HeartBeatMixin(object):
    """Waits for heartbeat messages from SublimeText. The main thread
    kills the process if no heartbeat arrived in HEARTBEAT_TIMEOUT seconds."""
    def __init__(self):
        self.last_heartbeat = None
        self.heartbeat()

    def heartbeat(self):
        global last_heartbeat
        last_heartbeat = time.time()

class FlakeMixin(object):
    '''Performs a PyFlakes check on the input code, returns either a
    list of messages or a single syntax error in case of an error while
    parsing the code. The receiver thus has to check for these two
    cases.'''
    def check_syntax(self, code):
        try:
            tree = parse(code)
        except (SyntaxError, IndentationError, ValueError) as e:
            return {"lineno": e.lineno, "offset": e.offset, "text": e.text}
        else:
            return Checker(tree).messages

class Server(RopeProjectMixin, RopeFunctionsMixin, HeartBeatMixin, FlakeMixin):
    '''Python's SimpleXMLRPCServer accepts just one call of
    register_instance(), so this class just combines the above
    mixins.'''
    def __init__(self):
        RopeProjectMixin.__init__(self)
        RopeFunctionsMixin.__init__(self)
        HeartBeatMixin.__init__(self)
        FlakeMixin.__init__(self)

class XMLRPCServerThread(threading.Thread):
    '''Runs a SimpleXMLRPCServer in a new thread, so that the main
    thread can watch for the heartbeats and kill the process if no
    heartbeat messages arrive in time'''
    def __init__(self, port):
        threading.Thread.__init__(self)
        self.port = port
        self.daemon = True

    def run(self):
        self.server = SimpleXMLRPCServer(("localhost", port), allow_none=True)
        self.server.register_instance(Server())
        self.server.serve_forever()


if __name__ == '__main__':
        # single argument to this process should be the port to listen on
        port = int(sys.argv[1])
        # the SimpleXMLRPCServer is run in a new thread
        server_thread = XMLRPCServerThread(port)
        server_thread.start()
        # the main thread checks for heartbeat messages
        while 1:
            time.sleep(HEARTBEAT_TIMEOUT)
            if time.time() - last_heartbeat > HEARTBEAT_TIMEOUT:
                sys.exit()

