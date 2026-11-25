import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import frames, transfer  # noqa: E402


class Side:
    """One end of a transfer, with what it sends collected as frames."""

    def __init__(self):
        self.now = 0.0
        self.sent = []
        self.transfers = transfer.Transfers(lambda frame: self.sent.extend(frames.Decoder().feed(frame)),
                                            lambda: self.now)

    def take(self):
        sent, self.sent = self.sent, []
        return sent


def deliver(frames_, to):
    """Hand frames to a side; returns the transfers it completed."""
    done = []
    for kind, ident, payload in frames_:
        if kind == frames.PART:
            finished = to.transfers.part(ident, payload)
        else:
            message = frames.parse_json(payload)
            offer = to.transfers.receive(message)
            finished = to.transfers.accept(offer) if offer is not None else None
        if finished is not None:
            done.append(finished)
    return done


class TransferTest(unittest.TestCase):
    def setUp(self):
        self.sender, self.receiver = Side(), Side()

    def exchange(self):
        done = []
        for _ in range(1000):
            to_receiver, to_sender = self.sender.take(), self.receiver.take()
            if not to_receiver and not to_sender:
                return done
            done += deliver(to_receiver, self.receiver)
            deliver(to_sender, self.sender)
        raise AssertionError("never went quiet")

    def test_a_payload_arrives_whole_with_what_the_offer_said(self):
        payload = bytes(range(256)) * 10000
        self.sender.transfers.send("state", 4, payload, world="a.1")
        (done,) = self.exchange()
        self.assertEqual((done.ident, done.what, done.fields), (4, "state", {"world": "a.1"}))
        self.assertEqual(done.payload, payload)
        self.assertIsNone(self.sender.transfers.outgoing(4))

    def test_no_more_than_a_window_is_in_flight(self):
        self.sender.transfers.send("state", 1, b"x" * (transfer.WINDOW * 3))
        sent = self.sender.take()
        in_flight = sum(len(payload) - 4 for kind, _, payload in sent if kind == frames.PART)
        self.assertEqual(in_flight, transfer.WINDOW)
        # More goes once the receiver confirms some.
        deliver(sent, self.receiver)
        deliver(self.receiver.take(), self.sender)
        self.assertTrue(any(kind == frames.PART for kind, _, _ in self.sender.take()))

    def test_frames_stay_small(self):
        self.sender.transfers.send("state", 1, b"x" * (transfer.PART_BYTES * 3 + 5))
        sizes = [len(payload) for kind, _, payload in self.sender.take() if kind == frames.PART]
        self.assertEqual(sizes, [transfer.PART_BYTES + 4] * 3 + [9])

    def test_progress_is_seen_on_both_sides(self):
        self.sender.transfers.send("state", 1, b"x" * transfer.WINDOW * 2)
        self.sender.now = self.receiver.now = 30.0
        deliver(self.sender.take(), self.receiver)
        self.assertEqual(self.receiver.transfers.incoming(1).progressed, 30.0)
        deliver(self.receiver.take(), self.sender)
        self.assertEqual(self.sender.transfers.outgoing(1).progressed, 30.0)
        self.assertEqual(self.sender.transfers.outgoing(1).acked, transfer.WINDOW)

    def test_an_empty_payload_is_done_at_once(self):
        self.sender.transfers.send("household", 2, b"")
        (done,) = self.exchange()
        self.assertEqual(done.payload, b"")
        self.assertIsNone(self.sender.transfers.outgoing(2))

    def test_a_refused_offer_stops_the_sender(self):
        self.sender.transfers.send("state", 3, b"x" * transfer.WINDOW * 2)
        offer = self.receiver.transfers.receive(frames.parse_json(self.sender.take()[0][2]))
        self.receiver.transfers.refuse(offer.ident)
        deliver(self.receiver.take(), self.sender)
        self.assertIsNone(self.sender.transfers.outgoing(3))
        self.assertEqual(self.receiver.transfers.part(3, b"\0\0\0\0x"), None)

    def test_a_part_out_of_order_ends_the_transfer(self):
        self.sender.transfers.send("state", 5, b"x" * transfer.PART_BYTES * 3)
        offer, first, second, third = self.sender.take()
        deliver([offer, first, third], self.receiver)
        self.assertIsNone(self.receiver.transfers.incoming(5))
        self.assertIn({"type": "drop", "id": 5}, [frames.parse_json(p) for _, _, p in self.receiver.take()])

    def test_parts_of_a_transfer_given_up_here_are_answered_once(self):
        self.sender.transfers.send("household", 9, b"x" * transfer.WINDOW * 3)
        offer, *parts = self.sender.take()
        deliver([offer, parts[0]], self.receiver)
        self.receiver.take()
        self.receiver.now = 100.0
        self.receiver.transfers.expire(45)
        deliver(parts[1:], self.receiver)
        self.assertEqual([frames.parse_json(p) for _, _, p in self.receiver.take()], [{"type": "drop", "id": 9}])
        deliver(parts[1:], self.receiver)
        self.assertEqual(self.receiver.take(), [])

    def test_an_offer_too_large_is_refused(self):
        offer = self.receiver.transfers.receive({"type": "offer", "id": 6, "what": "state",
                                                 "size": transfer.MAX_SIZE + 1})
        self.assertIsNone(self.receiver.transfers.accept(offer))
        self.assertIsNone(self.receiver.transfers.incoming(6))

    def test_sending_again_under_an_id_replaces_the_transfer(self):
        self.sender.transfers.send("state", 7, b"old" * transfer.WINDOW)
        self.sender.take()
        self.sender.transfers.send("state", 7, b"new")
        self.assertEqual(self.sender.transfers.outgoing(7).payload, b"new")

    def test_forgetting_drops_everything_quietly(self):
        self.sender.transfers.send("state", 8, b"x" * transfer.WINDOW * 2)
        self.sender.take()
        self.sender.transfers.forget()
        self.sender.transfers.pump()
        self.assertEqual(self.sender.take(), [])


if __name__ == "__main__":
    unittest.main()
