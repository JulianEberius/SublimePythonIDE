import sys
import os
if sys.version_info[0] == 2:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib/python2"))
    from SimpleXMLRPCServer import SimpleXMLRPCServer
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib/python3"))
    from xmlrpc.server import SimpleXMLRPCServer

import rope
from rope.base.project import Project
from rope.base import libutils
from rope.contrib.codeassist import code_assist, sorted_proposals
from rope.base.exceptions import ModuleSyntaxError


def proposal_string(p):
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

def insert_string(p):
    if p.parameters:
        params = [par for par in p.parameters if par != 'self']
        param_snippet = ", ".join(
            "${%i:%s}" %
            (idx + 1, param) for idx, param in enumerate(params))
        result = "%s(%s)" %(p.name, param_snippet)
    else:
        result = p.name

    return result

class CompletionDaemon(object):
    def __init__(self, path):
        self.project_path = path
        self.project = self.create_project(path)

    def create_project(self, path):
        project = Project(path,
            fscommands=None, ropefolder=None)
        return project

    def profile_completions(self,source,file_path,loc):
        try:
            import cProfile as profile
        except:
            import profile
        profile.runctx(
            "self.completions(source, file_path, loc)",
            globals(), locals(), os.path.expanduser("~/SublimePython.stats"))
        return self.completions(source, file_path, loc)

    def completions(self, source, file_path, loc):
        resource = libutils.path_to_resource(self.project, file_path)
        try:
            proposals = code_assist(self.project, source, loc,
                resource=resource, maxfixes=3)
            proposals = sorted_proposals(proposals)
        except ModuleSyntaxError:
            proposals = []
        except Exception as e:
            import traceback
            traceback.print_exc()

        proposals = [(proposal_string(p), insert_string(p))
                        for p in proposals
                        if p.name != 'self=']
        return proposals


if __name__ == '__main__':
        project_path = "/Users/ebi/dev/ligatabellen"
        project_path = "/Users/ebi/dev/aweSX"

        # Create server
        server = SimpleXMLRPCServer(("localhost", 8765))
        server.register_introspection_functions()
        server.register_instance(CompletionDaemon(project_path))

        # Run the server's main loop
        server.serve_forever()
