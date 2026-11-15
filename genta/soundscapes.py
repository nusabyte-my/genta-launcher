"""Deterministic, original synthesized brown/pink noise, rain and ocean loops."""
from pathlib import Path
import wave

import numpy as np
from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

NAMES = ('Silence', 'Brown noise', 'Pink noise', 'Rain', 'Ocean')


def render_sounds(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rate, duration = 22050, 24
    count = rate * duration
    rng = np.random.default_rng(14062026)
    frequencies = np.fft.rfftfreq(count, 1 / rate)
    safe = np.maximum(frequencies, 25)
    spectrum = np.fft.rfft(rng.standard_normal(count))
    spectrum[0] = 0
    pink = np.fft.irfft(spectrum / np.sqrt(safe), n=count)
    brown = np.fft.irfft(spectrum / safe, n=count)
    white = rng.standard_normal(count)
    rain = .5 * np.fft.irfft(np.fft.rfft(white) * (frequencies / (frequencies + 700)), n=count) + .18 * pink / np.std(pink)
    t = np.arange(count) / rate
    ocean = pink * (.32 + .68 * (.5 + .5 * np.sin(2 * np.pi * t / 8)) ** 2)
    for name, values in [('Brown noise', brown), ('Pink noise', pink), ('Rain', rain), ('Ocean', ocean)]:
        path = folder / (name.lower().replace(' ', '-') + '.wav')
        if path.exists():
            continue
        # -12 dBFS peak headroom before the user-controlled ambience bus gain.
        values = values / max(float(np.max(np.abs(values))), 1e-9) * .25
        pcm = np.asarray(values * 32767, dtype='<i2').tobytes()
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(pcm)
    return folder


class FocusAudio(QObject):
    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.output = QAudioOutput(self)
        self.output.setVolume(.09)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.name = 'Silence'
        self.running = False

    def select(self, name):
        if name not in NAMES:
            raise ValueError('Choose a supported focus sound')
        self.name = name
        self.player.stop()
        if name != 'Silence':
            path = self.folder / (name.lower().replace(' ', '-') + '.wav')
            if not path.is_file():
                render_sounds(self.folder)
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            if self.running:
                self.player.play()

    def volume(self, percent):
        self.output.setVolume((max(0, min(100, percent)) / 100) ** 2)

    def set_running(self, running):
        self.running = running
        if self.name != 'Silence':
            if running:
                self.player.play()
            else:
                self.player.pause()
