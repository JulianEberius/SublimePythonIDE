**SublimePython**
===========================

The only necessary (and possible ;-) ) configuration at the moment is setting "python_interpreter" in your projects settings (Project->Edit Project) to use another interpreter than your system interpreter. Example:
    
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
