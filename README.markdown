**SublimePythonIDE**
===========================
This plugin adds Python completions and some IDE-like functions to Sublime Text 3, through the use of the Rope library.
It is a complete rewrite of SublimeRope for ST2. It should be a lot faster and easier to use than SublimeRope was.

In contrast to SublimeRope, it does use the built in Python only for UI-related functions, the completions and refactorings
are calculated using the exact same python interpreter you use for your project (e.g. the one in your virtualenv).
This eliminates a lot of small and big problems that SublimeRope had, e.g., not recognizing dict comprehensions because Python2.6 is used in ST2, or not recognizing some of your libraries because you did not configure all the paths etc..
Everything your projects interpreter sees, should be visible to SublimePython -> easier configuration.

I also added a lot caching throughout the underlying Rope library which improved completion performance by several orders of magnitude. I hope no functionality breaks because of this ;-)


Configuration
-------------

The only necessary configuration at the moment is setting "python_interpreter" in your projects settings (Project->Edit Project) to use another interpreter than your system interpreter. Example:

    {
        "folders": [
            {
               "path": "XYZ"
            },
            {
                "path": "ABC"
            }
        ],
        "settings": {
            "python_interpreter": "/path/to/some/virtualenv/bin/python"
        }
    }

This is also the way to select a virtualenv (point it to the interpreter in the venv) and thus get the completions/definitions for you project working.

SublimePythonIDE will also look up imports relative to the project root directory (the top directory of your project).

In cases where the project directory is outside of your root python module, you may optionally set a custom source root directory in the project settings:

    {
        "folders": [
            {
               "path": "XYZ"
            },
        ],
        "settings": {
            "src_root": "XYZ/THE_ACTUAL_SRC"
            "python_interpreter": "/path/to/some/virtualenv/bin/python",
        }
    }

See Packages/SublimePythonIDE/SublimePython.sublime-settings for other options. As with all ST packages, copy this file into your Packages/User folder and editing the copy there.

Copyright (C) 2013 Julian Eberius

License:
--------

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along
    with this program; if not, write to the Free Software Foundation, Inc.,
    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

Have a look at "LICENSE.txt" file for more information.

EXTERNAL LICENSES
-----------------
This project uses code from other open source projects (Rope)
which may include licenses of their own.
