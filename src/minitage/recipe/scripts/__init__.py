#!/usr/bin/env python

# Copyright (C) 2008, Mathieu PASQUET <kiorky@cryptelium.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

__docformat__ = 'restructuredtext en'

import logging
import os
import shutil
import re
import stat

import pkg_resources
import zc.buildout.easy_install

from minitage.recipe import egg
from minitage.core.common import get_from_cache, system, splitstrip
parse_entry_point = re.compile(
        '([^=]+)=(\w+(?:[.]\w+)*):(\w+(?:[.]\w+)*)$'
        ).match

class Recipe(egg.Recipe):
    """
    Downloads and installs a distutils Python distribution.
    """
    def __init__(self, buildout, name, options):
        egg.Recipe.__init__(self,
                            buildout, name, options)
        self.not_filtered = []
        self.arguments = self.options.get('arguments', '')
        self.zap = splitstrip(self.options.get('zap', ''))
        self.for_buildoutscripts = options.get('buildoutscripts', '')
        self.initialization = self.options.get('initialization', '')
        self.options_scripts = self.options.get('scripts', '')
        self.entry_points_options = self.options.get('entry-points', '').strip()
        self.interpreter = self.options.get('interpreter', '').strip()
        self.env_file = self.options.get('env-file', '').strip()
        self.bin = self.buildout['buildout'].get('bin-directory',
                                                 os.path.join(os.getcwd(), 'bin'))
        if self.extra_paths:
            self.extra_paths = [p for p in self.extra_paths if os.path.exists(p)]
            options['extra-paths'] = '\n'.join(self.extra_paths)

    parse_entry_point = parse_entry_point

    def update(self):
        return self.install()

    def filter(self, dist, name,
               entry_points_options, arguments,
               console_scripts):
        """
        Filter script creation or not.
        Think that the recipe is also used by minitagificator and we patch zc.buildout.easy_install.Installer.
        We may install everything including buildout itself, with an incopatible python version.
        So, we must take care with a robust filter mecanism!
        """
        # never touch to buildout script if it s not explicit!
        if name in ['zc.buildout', 'buildout']:
            if ('buildout' in console_scripts) \
               or ('zc.buildout' in console_scripts)\
               or self.options.get('eggs').strip() == 'zc.buildout':
                return True
            else:
                return False

        if not (name in self.zap):
            if (
                ((not entry_points_options)
                 and (not arguments)
                 and (not ('scripts' in self.options)))
                or (name in console_scripts)
                or (bool(self.for_buildoutscripts)
                    and (dist.project_name in self.options['eggs']))
                or ('generate_all_scripts' in self.options
                    and not entry_points_options)
            ):
                return True
        return False

    def install(self, working_set=None):
        """installs an egg
        """
        self.logger.info('Installing console scripts.')
        arguments = self.arguments
        # install console scripts
        installed_scripts, install_paths = {},[]
        bin = self.bin
        sitepackages = re.sub('bin.*',
                              'lib/python%s/site-packages' % self.executable_version,
                               self.executable)
        scan_paths =[self.buildout['buildout']['develop-eggs-directory'],
                      self.buildout['buildout']['eggs-directory'],
                      sitepackages]  + self.extra_paths
        entry_points = []
        # parse script key
        scripts = self.options_scripts
        entry_points_options = self.entry_points_options
        if isinstance(scripts, str):
            scripts = scripts.split('\n')
            scripts = dict([
                ('=' in s) and s.split('=', 1) or (s, s)
                for s in scripts if s
                ])
        console_scripts = scripts.keys()

        # install needed stuff and get according working set
        sreqs, ws = self.working_set(working_set=working_set)
        reqs = [pkg_resources.Requirement.parse(r) for r in sreqs]
        env = pkg_resources.Environment(scan_paths, python = self.executable_version)
        required_dists = ws.resolve(reqs, env)
        for dist in required_dists:
            if not dist in ws:
                ws.add(dist)
        pypath = [os.path.abspath(p)
                  for p in ws.entries+self.extra_paths
                  if os.path.exists(p)]
        abs_pypath = pypath[:]
        rpypath, rsetup = pypath, ''
        if self._relative_paths:
            rpypath, rsetup = zc.buildout.easy_install._relative_path_and_setup(
                os.path.join(self.bin, 'i_will_be_a_script'),
                pypath,
                self._relative_paths
            )
        else:
            rpypath = "'%s'" % "',\n'".join(rpypath)

        template_vars = {'python': self.executable,
                         'path': rpypath,
                         'rsetup': rsetup,
                         'arguments': arguments,
                         'initialization': self.initialization,}

        # parse entry points key
        for s in [item
                  for item in entry_points_options.split()
                  if item]:
            if s.strip():
                parsed = self.parse_entry_point(s)
                if not parsed:
                    logging.getLogger(self.name).error(
                        "Cannot parse the entry point %s.", s)
                    raise zc.buildout.UserError("Invalid entry point")
                entry = parsed.groups()
                entry_points.append(entry)
                scripts[entry[0]] = entry[0]

        # scan eggs for entry point keys
        for dist in ws:
            for name in pkg_resources.get_entry_map(dist, 'console_scripts'):
                if self.filter(dist, name,
                               entry_points_options, arguments,
                               console_scripts):
                    scripts.setdefault(name, name)
                    entry_point = dist.get_entry_info('console_scripts', name)
                    entry_points.append(
                        (name, entry_point.module_name,
                         '.'.join(entry_point.attrs))
                        )

        # generate interpreter
        interpreter = self.interpreter
        if interpreter:
            inst_script = os.path.join(bin, interpreter)
            installed_scripts[interpreter] = inst_script, py_script_template % template_vars

        if self.env_file:
            env_vars= template_vars.copy()
            env_vars['path'] = ':'.join(abs_pypath)
            env_script = self.env_file
            if not '/' in self.env_file:
                env_script = os.path.join(bin, self.env_file)
            installed_scripts[env_script] = env_script, snv_template % env_vars

        # generate console entry pointts
        for name, module_name, attrs in entry_points:
            sname = name
            if scripts:
                sname = scripts.get(name)
                if sname is None:
                    continue
            entry_point_vars = template_vars.copy()
            entry_point_vars.update(
                {'module_name':  module_name,
                 'attrs': attrs,})
            installed_scripts[sname] = (
                os.path.join(bin, sname),
                entry_point_template % entry_point_vars
            )

        # generate scripts
        option_scripts = self.options_scripts
        # now install classical scripts from the entry script.
        already_installed = [os.path.basename(s) for s in installed_scripts]
        for dist in ws:
            provider = dist._provider
            items = {}
            if dist.has_metadata('scripts'):
                for s in provider.metadata_listdir('scripts'):
                    if not(s in already_installed)\
                       and not(s.endswith('.pyc') or s.endswith('.pyo')):
                        items[s] = provider._fn(provider.egg_info, 'scripts/%s' % s)
            for script in items:
                if self.filter(dist, script,
                               entry_points_options, arguments,
                               console_scripts):
                    # mean to filter by dist, even if the dist doesnt provide
                    # console scripts ;), just add dist.project_name in the
                    # scripts section
                    if dist.project_name in console_scripts:
                        scripts.setdefault(name, name)
                    script_filename = items[script]
                    inst_script = os.path.join(bin,os.path.split(script_filename)[1])
                    script_vars = template_vars.copy()
                    script_vars.update({'code': script_filename})
                    code = script_template % script_vars
                    sname = scripts.get(script, script)
                    installed_scripts[sname] = inst_script, code

        ls = []
        for script in installed_scripts:
            path, content = installed_scripts[script]
            ls.append(path)
            install_paths.append(path)
            open(path, 'w').writelines(content)
            os.chmod(path,
                     stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
                     | stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP
                     | stat.S_IROTH | stat.S_IXOTH
                    )
        l = installed_scripts.keys()
        l.sort()
        msg = 'There were no scripts found to generate or the recipe did not select any one.'
        if(l):
            msg = 'Generated scripts: \'%s\'.' % (
                "', '".join(l)
            )
        self.logger.info(msg)
        return installed_scripts.keys()

script_template = """\
#!%(python)s

# ! GENERATED BY minitage.recipe !
import os
import sys
import subprocess

%(rsetup)s

sys.path[0:0] = [%(path)s ]

%(initialization)s

# EXEC ORGINAL CODE WITHOUT SHEBANG
__doc__  = 'I am generated by minitage.recipe.script recipe'

os.environ['PYTHONPATH'] = ':'.join(sys.path + os.environ.get('PYTHONPATH', '').split(':'))
sys.argv.pop(0)
sys.exit(
    subprocess.Popen(
        [sys.executable, '%(code)s']+sys.argv,
        env=os.environ
    ).wait()
)

"""
snv_template = """\
#!/usr/bin/env sh

PYTHONPATH="%(path)s:$PYTHONPATH"
export PYTHONPATH

"""

py_script_template = """\
#!%(python)s
#!!! #GENERATED VIA MINITAGE.recipe !!!

import sys

%(rsetup)s

sys.path[0:0] = [ %(path)s ]

%(initialization)s

_interactive = True
if len(sys.argv) > 1:
    import getopt
    _options, _args = getopt.getopt(sys.argv[1:], 'ic:')
    _interactive = False
    for (_opt, _val) in _options:
        if _opt == '-i':
            _interactive = True
        elif _opt == '-c':
            exec _val

    if _args:
        sys.argv[:] = _args
        execfile(sys.argv[0])

if _interactive:
    import code
    code.interact(banner="", local=globals())
"""

entry_point_template = """\
#!%(python)s
#!!! #GENERATED VIA MINITAGE.recipe !!!

import sys

%(rsetup)s

sys.path[0:0] = [ %(path)s ]

%(initialization)s

import %(module_name)s

if __name__ == '__main__':
    %(module_name)s.%(attrs)s(%(arguments)s)
"""



def stub():
    pass

# vim:set et sts=4 ts=4 tw=80:
