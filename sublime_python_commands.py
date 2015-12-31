import sublime
import sublime_plugin

from SublimePythonIDE.sublime_python import proxy_for, root_folder_for,\
    get_setting, file_or_buffer_name, GOTO_STACK, python_only


class PythonCompletionsListener(sublime_plugin.EventListener):

    '''Retrieves completion proposals from external Python
    processes running Rope'''

    @python_only
    def on_query_completions(self, view, prefix, locations):
        path = file_or_buffer_name(view)
        source = view.substr(sublime.Region(0, view.size()))
        loc = view.rowcol(locations[0])
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

    @python_only
    def on_post_save_async(self, view, *args):
        proxy = proxy_for(view)
        if not proxy:
            return
        path = file_or_buffer_name(view)
        proxy.report_changed(root_folder_for(view), path)


class PythonGetDocumentationCommand(sublime_plugin.WindowCommand):

    '''Retrieves the docstring for the identifier under the cursor and
    displays it in a new panel.'''

    @python_only
    def run(self):
        view = self.window.active_view()
        row, col = view.rowcol(view.sel()[0].a)
        offset = view.text_point(row, col)
        path = file_or_buffer_name(view)
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
                    self.window.focus_group(active_group - 1)

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

    @python_only
    def run(self, *args):
        view = self.window.active_view()
        row, col = view.rowcol(view.sel()[0].a)
        offset = view.text_point(row, col)
        path = file_or_buffer_name(view)
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
        current_rowcol = view.rowcol(view.sel()[0].end())
        current_lineno = current_rowcol[0] + 1
        current_colno = current_rowcol[1] + 1

        if None not in (path, target_path, target_lineno):
            self.save_pos(file_or_buffer_name(view), current_lineno, current_colno)
            path = target_path + ":" + str(target_lineno)
            self.window.open_file(path, sublime.ENCODED_POSITION)
        elif target_lineno is not None:
            self.save_pos(file_or_buffer_name(view), current_lineno, current_colno)
            path = file_or_buffer_name(view) + ":" + str(target_lineno)
            self.window.open_file(path, sublime.ENCODED_POSITION)
        else:
            # fail silently (user selected whitespace, etc)
            pass

    def save_pos(self, file_path, lineno, colno=0):
        GOTO_STACK.append((file_path, lineno, colno))


class PythonGoBackCommand(sublime_plugin.WindowCommand):

    @python_only
    def run(self, *args):
        if GOTO_STACK:
            file_name, lineno, colno = GOTO_STACK.pop()
            path = "%s:%d:%d" % (file_name, lineno, colno)
            self.window.open_file(path, sublime.ENCODED_POSITION)
