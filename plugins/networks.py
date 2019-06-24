"""Networks plugin - allows you to manipulate connections to various configured networks."""
import importlib
import types
import threading

import pylinkirc
from pylinkirc import utils, world
from pylinkirc.log import log
from pylinkirc.coremods import control, permissions

REMOTE_IN_USE = threading.Event()

@utils.add_cmd
def disconnect(irc, source, args):
    """<network>

    Disconnects the network <network>. When all networks are disconnected, PyLink will automatically exit.

    To reconnect a network disconnected using this command, use REHASH to reload the networks list."""
    permissions.check_permissions(irc, source, ['networks.disconnect'])
    try:
        netname = args[0]
        network = world.networkobjects[netname]
    except IndexError:  # No argument given.
        irc.error('Not enough arguments (needs 1: network name (case sensitive)).')
        return
    except KeyError:  # Unknown network.
        irc.error('No such network "%s" (case sensitive).' % netname)
        return

    if network.has_cap('virtual-server'):
        irc.error('"%s" is a virtual server and cannot be directly disconnected.' % netname)
        return

    log.info('Disconnecting network %r per %s', netname, irc.get_hostmask(source))
    control.remove_network(network)
    irc.reply("Done. If you want to reconnect this network, use the 'rehash' command.")

@utils.add_cmd
def autoconnect(irc, source, args):
    """<network> <seconds>

    Sets the autoconnect time for <network> to <seconds>.
    You can disable autoconnect for a network by setting <seconds> to a negative value."""
    permissions.check_permissions(irc, source, ['networks.autoconnect'])
    try:
        netname = args[0]
        seconds = float(args[1])
        network = world.networkobjects[netname]
    except IndexError:  # Arguments not given.
        irc.error('Not enough arguments (needs 2: network name (case sensitive), autoconnect time (in seconds)).')
        return
    except KeyError:  # Unknown network.
        irc.error('No such network "%s" (case sensitive).' % netname)
        return
    except ValueError:
        irc.error('Invalid argument "%s" for <seconds>.' % seconds)
        return
    network.serverdata['autoconnect'] = seconds
    irc.reply("Done.")

remote_parser = utils.IRCParser()
remote_parser.add_argument('--service', type=str, default='pylink')
remote_parser.add_argument('network')
remote_parser.add_argument('command', nargs=utils.IRCParser.REMAINDER)
@utils.add_cmd
def remote(irc, source, args):
    """[--service <service name>] <network> <command>

    Runs <command> on the remote network <network>. Plugin responses sent using irc.reply() are
    supported and returned here, but others are dropped due to protocol limitations."""
    args = remote_parser.parse_args(args)
    if not args.command:
        irc.error('No command given!')
        return

    netname = args.network

    permissions.check_permissions(irc, source, [
        # Quite a few permissions are allowed. 'networks.remote' is the global permission,
        'networks.remote',
        # networks.remote.<network> allows running any command on a specific network,
        'networks.remote.%s' % netname,
        # networks.remote.<network>.<service> allows running any command on the given service on a
        # specific network,
        'networks.remote.%s.%s' % (netname, args.service),
        # and networks.remote.<network>.<service>.<command> narrows this further into which command
        # can be used.
        'networks.remote.%s.%s.%s' % (netname, args.service, args.command[0])
    ])

    # XXX: things like 'remote network1 remote network2 echo hi' will crash PyLink if the source network is network1...
    global REMOTE_IN_USE
    if REMOTE_IN_USE.is_set():
        irc.error("The 'remote' command can not be nested.")
        return

    REMOTE_IN_USE.set()
    if netname == irc.name:
        # This would actually throw _remote_reply() into a loop, so check for it here...
        # XXX: properly fix this.
        irc.error("Cannot remote-send a command to the local network; use a normal command!")
        REMOTE_IN_USE.clear()
        return

    try:
        remoteirc = world.networkobjects[netname]
    except KeyError:  # Unknown network.
        irc.error('No such network %r (case sensitive).' % netname)
        REMOTE_IN_USE.clear()
        return

    if args.service not in world.services:
        irc.error('Unknown service %r.' % args.service)
        REMOTE_IN_USE.clear()
        return
    elif not remoteirc.connected.is_set():
        irc.error('Network %r is not connected.' % netname)
        REMOTE_IN_USE.clear()
        return
    elif not world.services[args.service].uids.get(netname):
        irc.error('The requested service %r is not available on %r.' % (args.service, netname))
        REMOTE_IN_USE.clear()
        return

    # Force remoteirc.called_in to something private in order to prevent
    # accidental information leakage from replies.
    try:
        remoteirc.called_in = remoteirc.called_by = remoteirc.pseudoclient.uid

        # Set the identification override to the caller's account.
        remoteirc.pseudoclient.account = irc.users[source].account
    except:
        REMOTE_IN_USE.clear()
        raise

    def _remote_reply(placeholder_self, text, **kwargs):
        """
        reply() rerouter for the 'remote' command.
        """
        assert irc.name != placeholder_self.name, \
            "Refusing to route reply back to the same " \
            "network, as this would cause a recursive loop"
        log.debug('(%s) networks.remote: re-routing reply %r from network %s', irc.name,
                  text, placeholder_self.name)

        # Override the source option to make sure the source is valid on the local network.
        if 'source' in kwargs:
            del kwargs['source']
        irc.reply(text, source=irc.pseudoclient.uid, **kwargs)

    old_reply = remoteirc._reply

    with remoteirc._reply_lock:
        try:  # Remotely call the command (use the PyLink client as a dummy user).
            # Override the remote irc.reply() to send replies HERE.
            log.debug('(%s) networks.remote: overriding reply() of IRC object %s', irc.name, netname)
            remoteirc._reply = types.MethodType(_remote_reply, remoteirc)
            world.services[args.service].call_cmd(remoteirc, remoteirc.pseudoclient.uid,
                                                  ' '.join(args.command))
        finally:
            # Restore the original remoteirc.reply()
            log.debug('(%s) networks.remote: restoring reply() of IRC object %s', irc.name, netname)
            remoteirc._reply = old_reply
            # Remove the identification override after we finish.
            try:
                remoteirc.pseudoclient.account = ''
            except:
                log.warning('(%s) networks.remote: failed to restore pseudoclient account for %s; '
                            'did the remote network disconnect while running this command?', irc.name, netname)
            REMOTE_IN_USE.clear()

@utils.add_cmd
def reloadproto(irc, source, args):
    """<protocol module name>

    Reloads the given protocol module without restart. You will have to manually disconnect and reconnect any network using the module for changes to apply."""
    permissions.check_permissions(irc, source, ['networks.reloadproto'])
    try:
        name = args[0]
    except IndexError:
        irc.error('Not enough arguments (needs 1: protocol module name)')
        return

    # Reload the dependency libraries first
    importlib.reload(pylinkirc.classes)
    log.debug('networks.reloadproto: reloading %s', pylinkirc.classes)

    for common_name in pylinkirc.protocols.common_modules:
        module = utils._get_protocol_module(common_name)
        log.debug('networks.reloadproto: reloading %s', module)
        importlib.reload(module)

    proto = utils._get_protocol_module(name)
    log.debug('networks.reloadproto: reloading %s', proto)
    importlib.reload(proto)

    irc.reply("Done. You will have to manually disconnect and reconnect any network using the %r module for changes to apply." % name)
