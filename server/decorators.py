
# Copyright (c) 2013 Oscar Campos <oscar.campos@member.fsf.org>
# See LICENSE for more details

"""
.. module:: decorators
    :platform: Unix, Windows
    :synopsis: Decorators for SublimePython plugin

.. moduleauthor:: Oscar Campos <oscar.campos@member.fsf.org>

"""

import os
import functools


def debug(f):

    @functools.wrap(f)
    def wrapped(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except:
            import traceback
            with open(os.path.expanduser("~/trace"), "w") as fl:
                traceback.print_exc(file=fl)
    return wrapped
