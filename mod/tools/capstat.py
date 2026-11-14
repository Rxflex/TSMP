"""Summarise a .tsmpcap capture.

Usage:
    python mod/tools/capstat.py <capture> [--from S] [--to S] [--timeline]

Message and operation names come from the game's own protobuf modules, looked
up in decompiled/pyc (see docs/internals.md). Those are Python 3.7 bytecode,
so names only appear when this runs under Python 3.7
(.tools/python37/python.exe); otherwise ids are printed raw.
"""

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

from tsmp import capture, wire  # noqa: E402

VIEW_UPDATE = "MSG_OBJECTS_VIEW_UPDATE"


def _enum_names(holder, prefix=""):
    """Enum values are plain int attributes on generated modules and classes."""
    names = {}
    for name in dir(holder):
        value = getattr(holder, name)
        if name.endswith("_FIELD_NUMBER"):
            continue  # protobuf field constants, not enum values
        if name.startswith(prefix) and name.isupper() and type(value) is int:
            names.setdefault(value, name)
    return names


def load_names():
    """(message names, operation names) by number; empty if unavailable.

    The paths are appended so the game's patched standard-library modules do
    not shadow the real ones.
    """
    pyc = os.path.join(ROOT, "decompiled", "pyc")
    for part in ("core", "generated"):
        sys.path.append(os.path.join(pyc, part))
    try:
        from protocolbuffers import Consts_pb2, DistributorOps_pb2
    except Exception as exc:
        print("names unavailable: {!r}".format(exc), file=sys.stderr)
        return {}, {}
    return _enum_names(Consts_pb2, "MSG_"), _enum_names(DistributorOps_pb2.Operation)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture")
    parser.add_argument("--from", dest="start", type=float, default=0.0, metavar="S")
    parser.add_argument("--to", dest="end", type=float, default=float("inf"), metavar="S")
    parser.add_argument("--timeline", action="store_true", help="print commands in order with times")
    args = parser.parse_args()

    msg_names, op_names = load_names()
    view_update_ids = {number for number, name in msg_names.items() if name == VIEW_UPDATE}

    messages = collections.Counter()
    message_bytes = collections.Counter()
    commands = collections.Counter()
    ops = collections.Counter()
    op_bytes = collections.Counter()
    sessions = collections.Counter()
    timeline = []
    last_time = 0.0

    with open(args.capture, "rb") as stream:
        for kind, when, ident, payload in capture.read(stream):
            last_time = when
            if not args.start <= when < args.end:
                continue
            if kind == capture.KIND_MSG_OUT:
                messages[ident] += 1
                message_bytes[ident] += len(payload)
                if ident in view_update_ids:
                    try:
                        for _, _, op_type, data in wire.view_update_ops(payload):
                            ops[op_type] += 1
                            op_bytes[op_type] += len(data)
                    except ValueError:
                        ops["<malformed>"] += 1
            elif kind == capture.KIND_COMMAND_IN:
                command = json.loads(payload.decode("utf-8"))
                commands[command["name"]] += 1
                sessions[ident] += 1
                timeline.append((when, ident, command["name"], command["args"]))

    print("capture length: {:.0f} s".format(last_time))
    print("\n== simulation -> client: {} messages, {} bytes".format(sum(messages.values()), sum(message_bytes.values())))
    for ident, count in messages.most_common():
        print("{:7d} {:10d} B  {}".format(count, message_bytes[ident], msg_names.get(ident, ident)))

    if ops:
        print("\n== view update operations: {}".format(sum(ops.values())))
        for op, count in ops.most_common():
            print("{:7d} {:10d} B  {}".format(count, op_bytes[op], op_names.get(op, op)))

    print("\n== client -> simulation: {} commands, per session {}".format(sum(commands.values()), dict(sessions)))
    for name, count in commands.most_common():
        print("{:7d}  {}".format(count, name))

    if args.timeline:
        print("\n== timeline")
        for when, session, name, arguments in timeline:
            line = " ".join(str(a) for a in arguments).replace("\n", " ")
            print("{:8.2f} [{}] {} {}".format(when, session, name, line[:160]))


if __name__ == "__main__":
    main()
