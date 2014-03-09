import sublime
import sublime_plugin

from SublimePythonIDE import util
util.update_sys_path()

from sublime_python import proxy_for, file_or_buffer_name, root_folder_for


class PythonRefactorRename(sublime_plugin.TextCommand):
    '''Renames the identifier under the cursor throughout the project'''
    def __init__(self, *args, **kwargs):
        super(PythonRefactorRename, self).__init__(*args, **kwargs)

    def run(self, edit, block=False):
        self.sel = self.view.sel()[0]
        self.default = self.default_input()
        self.view.window().show_input_panel(
            "New name:",
            self.default,
            self.input_callback,
            None,
            None
        )

    def input_callback(self, input_str):
        if input_str == self.default:
            return

        proxy = proxy_for(self.view)
        if not proxy:
            return

        self.view.run_command("save")
        project_path = root_folder_for(self.view)
        file_path = file_or_buffer_name(self.view)
        offset = self.view.sel()[0].a
        source = self.view.substr(sublime.Region(0, self.view.size()))
        proxy.rename(project_path, file_path, offset, source, input_str)

        # reload view
        self.view.run_command('revert')

    def default_input(self):
        return self.view.substr(self.view.word(self.sel.a))
