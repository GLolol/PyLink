#!/usr/bin/python3

import imp
import os
import sys

# This must be done before conf imports, so we get the real conf instead of testing one.
import world
world.testing = False

import conf
from log import log
import classes
import utils
import coreplugin

if __name__ == '__main__':
    log.info('PyLink starting...')
    if conf.conf['login']['password'] == 'changeme':
        log.critical("You have not set the login details correctly! Exiting...")
        sys.exit(2)
    protocols_folder = [os.path.join(os.getcwd(), 'protocols')]

    # Write a PID file.
    with open('%s.pid' % conf.confname, 'w') as f:
        f.write(str(os.getpid()))

    # Import plugins first globally, because they can listen for events
    # that happen before the connection phase.
    world.plugins.append(coreplugin)
    to_load = conf.conf['plugins']
    plugins_folder = [os.path.join(os.getcwd(), 'plugins')]
    # Here, we override the module lookup and import the plugins
    # dynamically depending on which were configured.
    for plugin in to_load:
        try:
            moduleinfo = imp.find_module(plugin, plugins_folder)
            pl = imp.load_source(plugin, moduleinfo[1])
            world.plugins.append(pl)
        except ImportError as e:
            if str(e) == ('No module named %r' % plugin):
                log.error('Failed to load plugin %r: The plugin could not be found.', plugin)
            else:
                log.error('Failed to load plugin %r: ImportError: %s', plugin, str(e))
        else:
            if hasattr(pl, 'main'):
                log.debug('Calling main() function of plugin %r', pl)
                pl.main()

    for network in conf.conf['servers']:
        protoname = conf.conf['servers'][network]['protocol']
        try:
            moduleinfo = imp.find_module(protoname, protocols_folder)
            proto = imp.load_source(protoname, moduleinfo[1])
        except ImportError as e:
            if str(e) == ('No module named %r' % protoname):
                log.critical('Failed to load protocol module %r: The file could not be found.', protoname)
            else:
                log.critical('Failed to load protocol module: ImportError: %s', protoname, str(e))
            sys.exit(2)
        else:
            world.networkobjects[network] = classes.Irc(network, proto)
    world.started.set()
    log.info("loaded plugins: %s", world.plugins)

