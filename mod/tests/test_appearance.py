import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tsmp import appearance  # noqa: E402


class CopyTest(unittest.TestCase):
    def setUp(self):
        try:
            from google.protobuf import descriptor_pb2
        except ImportError:
            self.skipTest("protobuf is not installed")
        self.message = descriptor_pb2.FileDescriptorProto

    def test_only_the_named_fields_are_copied(self):
        source = self.message(name="a.proto", package="pkg", syntax="proto3")
        source.dependency.extend(["x", "y"])
        source.message_type.add().name = "Thing"
        source.options.java_package = "j"
        target = self.message(name="kept")
        target.dependency.append("old")
        appearance.copy(source, target, ("package", "dependency", "message_type", "options", "no_such_field"))
        self.assertEqual(target.name, "kept")
        self.assertEqual(target.package, "pkg")
        self.assertEqual(list(target.dependency), ["x", "y"])
        self.assertEqual([thing.name for thing in target.message_type], ["Thing"])
        self.assertEqual(target.options.java_package, "j")
        self.assertFalse(target.HasField("syntax"))

    def test_a_message_field_that_is_not_set_stays_unset(self):
        target = self.message()
        appearance.copy(self.message(), target, ("options",))
        self.assertFalse(target.HasField("options"))


if __name__ == "__main__":
    unittest.main()
