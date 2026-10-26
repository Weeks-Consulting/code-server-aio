"""Check the Supervisor listener's byte protocol and fatal-service handling."""
import io
import signal
import unittest
from unittest.mock import patch
from test_start import load

listener = load('supervisor-events')


class ListenerTests(unittest.TestCase):
    def test_fatal_event_is_acknowledged_before_shutdown(self):
        payload = 'processname:code-server groupname:workspace note:café'.encode()
        class FragmentedInput(io.BytesIO):
            def read(self, size):
                return super().read(min(size, 2))
        source = FragmentedInput(b'ver:3.0 len:' + str(len(payload)).encode() + b'\n' + payload)
        output = io.BytesIO()
        def shutdown(pid, sig):
            self.assertEqual(output.getvalue(), b'READY\nRESULT 2\nOK')
            self.assertEqual((pid, sig), (123, signal.SIGTERM))
        with patch.object(listener.os, 'getppid', return_value=123), \
                patch.object(listener.os, 'kill', side_effect=shutdown) as kill:
            listener.listen(source, output)
        kill.assert_called_once()

    def test_malformed_or_truncated_events_are_not_acknowledged(self):
        for event in (b'len:bad\n', b'ver:3\n', b'len:10\nshort'):
            with self.subTest(event=event), patch.object(listener.os, 'kill') as kill:
                output = io.BytesIO()
                with self.assertRaises((ValueError, EOFError)):
                    listener.listen(io.BytesIO(event), output)
                self.assertEqual(output.getvalue(), b'READY\n')
                kill.assert_not_called()

    def test_multiple_events(self):
        source = io.BytesIO(b'len:0\nlen:0\n')
        output = io.BytesIO()
        listener.listen(source, output)
        self.assertEqual(output.getvalue(), b'READY\nRESULT 2\nOKREADY\nRESULT 2\nOKREADY\n')


if __name__ == '__main__':
    unittest.main()
