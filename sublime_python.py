import sys
import os
from time import time
import sublime
import sublime_plugin
import subprocess
import xmlrpc.client
from contextlib import contextmanager

proxy = None
server_proc = None

class PythonStartServerCommand(sublime_plugin.WindowCommand):
    def run(self, *args):
        global server_proc, proxy
        # python = "/usr/local/bin/python "
        python = "/usr/bin/python "
        server_script = os.path.join(os.path.dirname(__file__), "completion_server.py")
        if server_proc:
            server_proc.terminate()
        server_proc = subprocess.Popen(
            python + server_script,
            shell=True)
        proxy = xmlrpc.client.ServerProxy('http://localhost:8765')


class RopeProjectListener(sublime_plugin.EventListener):

    def __init__(self):
        self.uri = None

    def on_load(self, view):
        self.start_daemon(view)

    def on_activated(self, view):
        self.start_daemon(view)

    def start_daemon(self, view):
        pass


class RopeCompletionsListener(sublime_plugin.EventListener):
    def on_query_completions(self, view, prefix, locations):
        if not view.match_selector(locations[0], 'source.python'):
            return []
        path = view.file_name()
        source = view.substr(sublime.Region(0, view.size()))
        loc = locations[0]
        t1 = time()
        proposals = proxy.completions(source, path, loc)
        # proposals = proxy.profile_completions(source, path, loc)
        print(time() - t1)
        if proposals:
            completion_flags = sublime.INHIBIT_WORD_COMPLETIONS
            return (proposals, completion_flags)
        return proposals
