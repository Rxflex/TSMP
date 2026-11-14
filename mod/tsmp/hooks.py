"""Injection points into the game's Python simulation.

See docs/internals.md for how these were found and verified.

Two kinds of callbacks hang off each point. Taps only observe. The single
router (outbound) or gate (inbound) decides what happens to the message or
command; without one, everything passes through unchanged.
"""

import functools
import sys

from tsmp import log

_outbound_taps = []
_outbound_router = None
_outbound_installed = False

_inbound_taps = []
_inbound_gate = None
_inbound_installed = False
_block_next = False

_tick_callbacks = []
_tick_installed = False
_in_tick = False


def install_outbound_tap(callback):
    """Call `callback(client, msg_id, data)` for every message the simulation
    sends to the game client, before any routing."""
    _outbound_taps.append(callback)
    _install_outbound()


def set_outbound_router(router):
    """`router(client, msg_id, data)` returns the bytes the local game client
    should receive in place of `data`, or None to deliver nothing."""
    global _outbound_router
    _outbound_router = router
    _install_outbound()


def _install_outbound():
    """`server.client.Client.send_message` is the only caller of `omega.send`,
    so replacing it sees the complete simulation -> client stream. The
    replacement serializes once and hands the same bytes to everyone."""
    global _outbound_installed
    if _outbound_installed:
        return
    import omega
    from server.client import Client

    original = Client.send_message

    @functools.wraps(original)
    def send_message(self, msg_id, msg):
        if not self.active:
            return original(self, msg_id, msg)
        data = msg.SerializeToString()
        for tap in _outbound_taps:
            try:
                tap(self, msg_id, data)
            except Exception:
                log.exception("outbound tap failed")
        if _outbound_router is not None:
            try:
                data = _outbound_router(self, msg_id, data)
            except Exception:
                log.exception("outbound router failed")
            if data is None:
                return
        omega.send(self.id, msg_id, data)

    Client.send_message = send_message
    _outbound_installed = True


def install_inbound_tap(callback):
    """Call `callback(name, args, session_id)` for every command the game
    client sends to the simulation."""
    _inbound_taps.append(callback)
    _install_inbound()


def set_inbound_gate(gate):
    """`gate(name, args, session_id)` returns False to stop the command from
    running."""
    global _inbound_gate
    _inbound_gate = gate
    _install_inbound()


def _install_inbound():
    """Every registered command runs through `invoke_command`, a closure
    created by `sims4.commands.Command`. It looks up two module-level
    functions on each call, in this order:

        args = parse_args(spec, args, session_id)
        if not is_command_available(command_type): return

    Wrapping `parse_args` and reading the command name from the caller's
    frame sees all commands without re-registering any of them. To stop one,
    the wrapper raises a flag that makes the `is_command_available` call that
    follows answer False, which is the game's own way of declining a command.
    """
    global _inbound_installed
    if _inbound_installed:
        return
    import sims4.commands

    original_parse = sims4.commands.parse_args
    original_available = sims4.commands.is_command_available

    @functools.wraps(original_parse)
    def parse_args(spec, args, account):
        global _block_next
        _block_next = False
        try:
            name = sys._getframe(1).f_locals.get("name")
        except Exception:
            name = None
        if name is not None:
            for tap in _inbound_taps:
                try:
                    tap(name, args, account)
                except Exception:
                    log.exception("inbound tap failed")
            if _inbound_gate is not None:
                try:
                    _block_next = _inbound_gate(name, args, account) is False
                except Exception:
                    log.exception("inbound gate failed")
        try:
            return original_parse(spec, args, account)
        except BaseException:
            _block_next = False
            raise

    @functools.wraps(original_available)
    def is_command_available(command_type):
        global _block_next
        if _block_next:
            _block_next = False
            return False
        return original_available(command_type)

    sims4.commands.parse_args = parse_args
    sims4.commands.is_command_available = is_command_available
    _inbound_installed = True


def install_tick(callback):
    """Call `callback()` once per simulation tick, on the simulation thread,
    just before the tick's messages are sent to the game client."""
    global _tick_installed
    _tick_callbacks.append(callback)
    if _tick_installed:
        return
    from distributor.distributor_service import DistributorService

    original = DistributorService.on_tick

    @functools.wraps(original)
    def on_tick(self):
        global _in_tick
        if not _in_tick:
            _in_tick = True
            try:
                for tick in _tick_callbacks:
                    try:
                        tick()
                    except Exception:
                        log.exception("tick callback failed")
            finally:
                _in_tick = False
        return original(self)

    DistributorService.on_tick = on_tick
    _tick_installed = True
