from __future__ import annotations


class RecordingBrowserDriver:
    def start_existing_device_recording(self, *_args, **_kwargs):
        raise RuntimeError(
            "Recording start is blocked until existing-device action contracts are approved"
        )

    def stop_existing_device_recording(self, *_args, **_kwargs):
        raise RuntimeError(
            "Recording stop is blocked until existing-device action contracts are approved"
        )
