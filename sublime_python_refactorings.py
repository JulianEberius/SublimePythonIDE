from abc import ABCMeta, abstractmethod

import sublime
import sublime_plugin

from SublimePythonIDE.sublime_python import proxy_for, file_or_buffer_name, root_folder_for


class PythonAbstractRefactoring(object):
    '''
    Implements basic interaction for simple refactorings:
    1.) Ask user for some input using some message and default input
    2.) Collect necessary context (selection, source, file_path etc)
    3.) Save, call server to do the refactoring, reload view
    Subclasses should implement default_input, input_msg and refactor
    '''

    __metaclass__ = ABCMeta

    def run(self, edit, block=False):
        self.sel = self.view.sel()[0]
        self.default = self.default_input()
        self.view.window().show_input_panel(
            self.input_msg(),
            self.default,
            self.input_callback,
            None,
            None
        )

    def refactoring_context(self):
        project_path = root_folder_for(self.view)
        file_path = file_or_buffer_name(self.view)
        start = self.sel.a
        end = self.sel.b
        source = self.view.substr(sublime.Region(0, self.view.size()))
        return project_path, file_path, start, end, source

    def input_callback(self, input_str):
        if input_str == self.default:
            return

        proxy = proxy_for(self.view)
        if not proxy:
            return

        self.view.run_command("save")
        self.refactor(proxy, input_str, *self.refactoring_context())
        self.view.run_command('revert')  # reload view

    @abstractmethod
    def default_input(self):
        '''Default value shown in the input box'''
        pass

    @abstractmethod
    def input_msg(self):
        '''Message displayed to the user in the input box'''
        pass

    @abstractmethod
    def refactor(self, proxy, input_str, project_path, file_path, start, end, source):
        '''Given all the necessary context, a server proxy and the user's input,
        perform the refactoring'''
        pass


class PythonRefactorRename(PythonAbstractRefactoring, sublime_plugin.TextCommand):
    '''Renames the identifier under the cursor throughout the project'''
    def __init__(self, *args, **kwargs):
        sublime_plugin.TextCommand.__init__(self, *args, **kwargs)

    def default_input(self):
        return self.view.substr(self.view.word(self.sel.a))

    def input_msg(self):
        return "New name:"

    def refactor(self, proxy, input_str, project_path, file_path, start, _, source):
        print("calling rename with ", proxy, input_str, project_path, file_path, start)
        proxy.rename(project_path, file_path, start, source, input_str)


class PythonExtractMethod(PythonAbstractRefactoring, sublime_plugin.TextCommand):
    '''Extracts the selected code and creates a new method to contain it
    Tries to guess the correct arguments and return values for this new method'''
    def __init__(self, *args, **kwargs):
        sublime_plugin.TextCommand.__init__(self, *args, **kwargs)

    def input_msg(self):
        return "New method name:"

    def default_input(self):
        return ""

    def refactor(self, proxy, input_str, project_path, file_path, start, end, source):
        proxy.extract_method(project_path, file_path, start, end, source, input_str)


class PythonOrganizeImports(sublime_plugin.TextCommand):
    '''
    Organizes the imports of the current view.

    Tries to saves the view beforehand
    and organizes the imports only on a successful save.
    '''
    def run(self, edit):
        if self.view.is_dirty():
            self.view.run_command("save")
        if self.view.file_name():
            row, col = self.view.rowcol(self.view.sel()[0].a)
            path = file_or_buffer_name(self.view)
            all_view = sublime.Region(0, self.view.size())
            source = self.view.substr(all_view)

            proxy = proxy_for(self.view)
            if not proxy:
                return
            organized_source = proxy.organize_imports(source, root_folder_for(self.view), path)
            self.view.replace(edit, all_view, organized_source)
            # end with a saved view to be compatible with the other refactorings
            self.view.run_command("save")


class PythonOrganizeImportsOnSave(sublime_plugin.EventListener):
    '''
    Applies the organize imports refactoring everytime a file is saved.

    Only works if "python_organize_imports_on_save" is "True" in the project settings.
    That can be achieved by editing the project to have:

    "settings": {
        ...
        "python_organize_imports_on_save": true
    }
    '''

    _post_save_is_on = True

    def on_post_save(self, view):
        if view.settings().get("python_organize_imports_on_save") is True:
            # since the refactoring itself calls "save"
            # the event has to be turned off/on
            # otherwise we would enter an infinite recursion
            if self._post_save_is_on:
                try:
                    self._post_save_is_on = False
                    view.run_command("python_organize_imports")
                finally:
                    self._post_save_is_on = True
