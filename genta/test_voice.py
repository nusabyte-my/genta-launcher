"""A cancelled voice recording must not remain in the temporary directory."""
from pathlib import Path
import unittest
import wave

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog

from desktop_services import VoiceRecorder


class VoicePrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['genta-tests', '-platform', 'offscreen'])

    def test_escape_deletes_cancelled_recording(self):
        dialog = VoiceRecorder()
        recording = Path(dialog.path)
        try:
            # A real WAV artifact, without opening a physical microphone.
            with wave.open(str(recording), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b'\x00\x00' * 160)
            dialog.show()
            self.app.processEvents()
            key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(dialog, key)
            self.assertFalse(dialog.isVisible())
            self.assertFalse(recording.exists(), 'Cancelling must erase the private recording')
            self.assertFalse(Path(dialog.temp.name).exists())
        finally:
            dialog.done(QDialog.DialogCode.Rejected)
            dialog.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
