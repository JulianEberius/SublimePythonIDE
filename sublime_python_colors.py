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
                if c.text and "sublimepythonide" in c.text:
                    style = style_map.get(c.text)
                    if style is None:
                        print("Warning: Unknown SublimePythonIDE color style", c.text)
                        continue
                    color_elem = d.find("./dict/string")
                    if color_elem is None:
                        print("Warning: Error parsing theme", scheme)
                        continue
                    found_color = color_elem.text.upper().lstrip("#")
                    target_color = colors.get(style, DEFAULT_MARK_COLORS[style])
                    if target_color is None:
                        print("Warning: Error parsing theme", scheme, "unknown color style: ", style)
                        continue
                    target_color = target_color.upper().lstrip("#")
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
        original_name = os.path.splitext(os.path.basename(scheme))[0]
        new_name = original_name + ' (SublimePythonIDE).tmTheme'
        scheme_path = os.path.join(sublime.packages_path(), 'User', new_name)

        with open(scheme_path, 'w', encoding='utf8') as f:
            f.write(COLOR_SCHEME_PREAMBLE)
            f.write(ElementTree.tostring(plist, encoding='unicode'))

        # ST does not expect platform specific paths here, but only
        # forward-slash separated paths relative to "Packages"
        new_theme_setting = "/".join(['Packages', 'User', new_name])
        prefs.set('color_scheme', new_theme_setting)
        sublime.save_settings("Preferences.sublime-settings")

    # run async
    sublime.set_timeout_async(generate_color_scheme_async, 0)
