import unittest

from protocol import parse_sample, excitation_command


class TestProtocol(unittest.TestCase):
    def test_trailing_comma_from_firmware(self):
        self.assertEqual(parse_sample('1.0, 2, 3, 4, 5, 6, 7, -8.5,\r\n'),
                         (1.0, 2, 3, 4, 5, 6, 7, -8.5))

    def test_ignore_frequency_ack_and_bad_lines(self):
        self.assertIsNone(parse_sample('10.00\n'))
        self.assertIsNone(parse_sample('command not recognized'))
        self.assertIsNone(parse_sample('1,2,3,4,5,6,7'))
        self.assertIsNone(parse_sample('1,2,3,4,5,6,7,foo,'))

    def test_excitation_command_without_line_ending(self):
        self.assertEqual(excitation_command(10), 'response:10')
        with self.assertRaises(ValueError):
            excitation_command(26)


if __name__ == '__main__':
    unittest.main()
