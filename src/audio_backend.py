"""Small CLI-backed audio adapters for PipeWire and PulseAudio sessions."""

import re
import shutil
import subprocess


class AudioBackendError(RuntimeError):
    pass


class AudioBackend:
    name = 'audio'

    @classmethod
    def detect(cls):
        server = ''
        if shutil.which('pactl'):
            try:
                result = subprocess.run(['pactl', 'info'], capture_output=True, text=True,
                                        timeout=3, check=True)
                match = re.search(r'(?m)^Server Name:\s*(.+)$', result.stdout)
                server = match.group(1).strip().lower() if match else ''
            except (OSError, subprocess.SubprocessError):
                pass
        if 'pipewire' in server and shutil.which('pw-record'):
            return PipeWireBackend()
        if 'pulseaudio' in server and shutil.which('parecord'):
            return PulseAudioBackend()
        # Prefer a working server probe over executable presence: many systems
        # ship both clients while only one server is actually available.
        if shutil.which('wpctl') and shutil.which('pw-record'):
            try:
                subprocess.run(['wpctl', 'status', '-n'], capture_output=True,
                               text=True, timeout=3, check=True)
                return PipeWireBackend()
            except (OSError, subprocess.SubprocessError):
                pass
        if shutil.which('pactl') and shutil.which('parecord'):
            try:
                subprocess.run(['pactl', 'info'], capture_output=True,
                               text=True, timeout=3, check=True)
                return PulseAudioBackend()
            except (OSError, subprocess.SubprocessError):
                pass
        if shutil.which('parecord') and not shutil.which('pw-record'):
            return PulseAudioBackend()
        if shutil.which('pw-record'):
            return PipeWireBackend()
        if shutil.which('parecord'):
            return PulseAudioBackend()
        raise AudioBackendError(
            'No supported PipeWire or PulseAudio capture backend is available. '
            'Check that the audio server is running and install pw-record (PipeWire) '
            'or parecord (PulseAudio).')

    def list_sources(self):
        if shutil.which('pactl'):
            try:
                default = subprocess.run(['pactl', 'get-default-source'], capture_output=True,
                                         text=True, timeout=3, check=True).stdout.strip()
                output = subprocess.run(['pactl', 'list', 'sources'], capture_output=True,
                                        text=True, timeout=3, check=True).stdout
                devices = []
                for block in re.split(r'(?m)^Source #\d+\s*$', output)[1:]:
                    name = re.search(r'(?m)^\s*Name:\s*(.+?)\s*$', block)
                    description = re.search(r'(?m)^\s*Description:\s*(.+?)\s*$', block)
                    if name and not name.group(1).endswith('.monitor'):
                        devices.append((description.group(1) if description else name.group(1),
                                        name.group(1)))
                return default, devices
            except (OSError, subprocess.SubprocessError):
                pass
        if shutil.which('wpctl'):
            try:
                result = subprocess.run(['wpctl', 'status', '-n'], capture_output=True,
                                        text=True, timeout=3, check=True)
                source_section = result.stdout.split('Sources', 1)[-1].split('Filters', 1)[0]
                devices = []
                default = ''
                for line in source_section.splitlines():
                    match = re.match(r'^\s*(\*)?\s*(\d+)\.\s+(.+?)\s*$', line)
                    if not match:
                        continue
                    is_default, node_id, name = match.groups()
                    if name.endswith('.monitor'):
                        continue
                    label = re.sub(r'\s+\[(?:vol|muted):.*$', '', name).strip()
                    devices.append((label or name, node_id))
                    if is_default:
                        default = node_id
                return default, devices
            except (OSError, subprocess.SubprocessError):
                pass
        return '', []

    def recording_command(self, source, destination, rate=16000, channels=1):
        raise NotImplementedError

    def meter_command(self, source, rate=16000, channels=1):
        raise NotImplementedError


class PipeWireBackend(AudioBackend):
    name = 'PipeWire'

    def recording_command(self, source, destination, rate=16000, channels=1):
        command = ['pw-record']
        if source:
            command.extend(['--target', str(source)])
        command.extend(['--rate', str(rate), '--channels', str(channels),
                        '--format', 's16', str(destination)])
        return command

    def meter_command(self, source, rate=16000, channels=1):
        command = ['pw-record']
        if source:
            command.extend(['--target', str(source)])
        command.extend(['--rate', str(rate), '--channels', str(channels),
                        '--format', 's16', '--raw', '-'])
        return command


class PulseAudioBackend(AudioBackend):
    name = 'PulseAudio'

    def recording_command(self, source, destination, rate=16000, channels=1):
        command = ['parecord']
        if source:
            command.extend(['--device', str(source)])
        command.extend(['--file-format=wav', f'--rate={rate}', f'--channels={channels}',
                        '--format=s16le', str(destination)])
        return command

    def meter_command(self, source, rate=16000, channels=1):
        recorder = shutil.which('parec') or shutil.which('parecord')
        command = [recorder, '--raw']
        if source:
            command.extend(['--device', str(source)])
        command.extend([f'--rate={rate}', f'--channels={channels}', '--format=s16le'])
        return command
