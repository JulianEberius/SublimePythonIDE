import os
from xml.etree import ElementTree
import sublime

# color-related constants

DEFAULT_MARK_COLORS = {'warning': 'EDBA00', 'error': 'DA2000', 'gutter': 'FFFFFF'}

COLOR_SCHEME_PREAMBLE = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
'''

COLOR_SCHEME_STYLES = {
    'warning': '''
        <dict>
            <key>name</key>
            <string>SublimePythonIDE Warning</string>
            <key>scope</key>
            <string>sublimepythonide.mark.warning</string>
            <key>settings</key>
            <dict>
                <key>foreground</key>
                <string>#{}</string>
            </dict>
        </dict>
    ''',

    'error': '''
        <dict>
            <key>name</key>
            <string>SublimePythonIDE Error</string>
            <key>scope</key>
            <string>sublimepythonide.mark.error</string>
            <key>settings</key>
            <dict>
                <key>foreground</key>
                <string>#{}</string>
            </dict>
        </dict>
    ''',

    'gutter': '''
        <dict>
            <key>name</key>
            <string>SublimePythonIDE Gutter Mark</string>
            <key>scope</key>
            <string>sublimepythonide.gutter-mark</string>
            <key>settings</key>
            <dict>
                <key>foreground</key>
                <string>#FFFFFF</string>
            </dict>
        </dict>
    '''
}

# maps scopes to style names
style_map = {
    "sublimepythonide.gutter-mark": "gutter",
    "sublimepythonide.mark.warning": "warning",
    "sublimepythonide.mark.error": "error"
}


def update_color_scheme(colors):
    """
    Adapted from SublimeLinter
    Asynchronously call generate_color_scheme_async.
    Modify  the current color scheme to contain SublimePythonIDE color entries as
    set in SublimePython.sublime-settings
    """

    def generate_color_scheme_async():
        # find and parse current theme
        prefs = sublime.load_settings("Preferences.sublime-settings")
        scheme = prefs.get('color_scheme')

        if scheme is None:
            return

        scheme_text = sublime.load_resource(scheme)
        plist = ElementTree.XML(scheme_text)
        dicts = plist.find('./dict/array')

        # find all SublimePythonIDE style infos in the theme and update if necessary
        change = False
        found_styles = {"gutter": False, "warning": False, "error": False}
        for d in dicts.findall("./dict"):
            for c in d.getchildren():
                if "sublimepythonide" in c.text:
                    style = style_map.get(c.text)
                    color_elem = d.find("./dict/string")
                    found_color = color_elem.text.upper().lstrip("#")
                    target_color = colors.get(style, DEFAULT_MARK_COLORS[style]).upper().lstrip("#")
                    if found_color != target_color:
                        change = True
                        color_elem.text = "#" + target_color
                    found_styles[style] = True
                    break

        # add defaults for all styles that were not found
        for style in [s for s, found in found_styles.items() if not found]:
            color = colors.get(style, DEFAULT_MARK_COLORS[style])
            color = color.lstrip('#')
            dicts.append(
                ElementTree.XML(COLOR_SCHEME_STYLES[style].format(color)))
            change = True

        # only write new theme if necessary
        if not change:
            return

        # write new theme
        scheme_path = os.path.join(os.path.split(sublime.packages_path())[0], scheme)

        with open(scheme_path, 'w', encoding='utf8') as f:
            f.write(COLOR_SCHEME_PREAMBLE)
            f.write(ElementTree.tostring(plist, encoding='unicode'))

        prefs.set('color_scheme', scheme)
        sublime.save_settings("Preferences.sublime-settings")

    # run async
    sublime.set_timeout_async(generate_color_scheme_async, 0)
