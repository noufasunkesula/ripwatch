import zipfile
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest

from rw.adapters.base import Frame, UnsupportedInput, fit_width
from rw.adapters.burst import BurstSource, exif_time
from rw.adapters.factory import source_for
from rw.adapters.frames import FrameFolderSource
from rw.adapters.image import ImageSource
from rw.adapters.video import VideoFileSource
from rw.contracts.vision import Mode
from scripts.make_synthetic_clip import write_clip

START = datetime(2026, 10, 5, 10, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    # 2 s at 15 fps, 960 wide so resizing is exercised.
    return write_clip(tmp_path_factory.mktemp("clips") / "c.mp4", seconds=2, width=960, height=540)


def _jpg(path, value=128, size=(120, 80)):
    cv2.imwrite(str(path), np.full((size[1], size[0], 3), value, np.uint8))
    return path


def test_fit_width_shrinks_keeping_aspect_and_never_upscales():
    assert fit_width(np.zeros((540, 960, 3), np.uint8), 640).shape == (360, 640, 3)
    assert fit_width(np.zeros((100, 200, 3), np.uint8), 640).shape == (100, 200, 3)


def test_video_decimates_to_target_fps_resizes_and_timestamps(clip):
    source = VideoFileSource(clip, "cam-01", "cam-01/clip", START)
    assert source.mode is Mode.VIDEO
    assert source.fps_source == pytest.approx(15, abs=0.5)
    frames = list(source.iter_frames(target_fps=5, max_width=640))
    assert len(frames) == 10  # 30 source frames, every 3rd
    assert all(isinstance(f, Frame) for f in frames)
    assert frames[0].image.shape == (360, 640, 3)
    assert [f.index for f in frames] == list(range(10))
    assert frames[0].ts == START
    assert frames[1].ts - frames[0].ts == timedelta(seconds=3 / 15)
    assert {f.camera_id for f in frames} == {"cam-01"}


def test_video_that_cannot_be_opened_raises(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(UnsupportedInput):
        VideoFileSource(bad, "cam-01", "x")


def test_frame_folder_sorts_and_uses_folder_fps(tmp_path, monkeypatch):
    for name in ["b.jpg", "a.jpg", "c.png", "notes.txt"]:
        (tmp_path / name).write_bytes(b"") if name.endswith(".txt") else _jpg(tmp_path / name)
    source = FrameFolderSource(tmp_path, "cam-02", "cam-02/folder", START, fps=10)
    assert [p.name for p in source.files] == ["a.jpg", "b.jpg", "c.png"]
    frames = list(source.iter_frames(target_fps=5))
    assert len(frames) == 2  # every 2nd of 3
    assert frames[1].ts - frames[0].ts == timedelta(seconds=0.2)


def test_frame_folder_without_images_raises(tmp_path):
    with pytest.raises(UnsupportedInput, match="no .jpg/.png"):
        FrameFolderSource(tmp_path, "cam-01", "x", fps=15)


def test_image_source_is_one_frame_in_image_mode(tmp_path):
    source = ImageSource(_jpg(tmp_path / "p.jpg", size=(1280, 720)), "cam-01", "up/1", START)
    assert source.mode is Mode.IMAGE and source.fps_source is None
    (frame,) = source.iter_frames()
    assert frame.image.shape == (360, 640, 3)
    assert frame.ts == START


def test_burst_sorts_images_and_spaces_them_one_second(tmp_path):
    zpath = tmp_path / "b.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for i, name in enumerate(["3.jpg", "1.jpg", "2.jpg"]):
            z.write(_jpg(tmp_path / name, value=50 * i), arcname=name)
        z.writestr("__MACOSX/.ignored.jpg", b"")
        z.writestr("readme.txt", b"hi")
    source = BurstSource(zpath, "cam-01", "up/b", START)
    assert source.mode is Mode.BURST
    frames = list(source.iter_frames())
    assert len(frames) == 3
    assert [f.ts for f in frames] == [START + timedelta(seconds=i) for i in range(3)]


@pytest.mark.parametrize("count", [1, 11])
def test_burst_needs_two_to_ten_images(tmp_path, count):
    zpath = tmp_path / "b.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for i in range(count):
            z.write(_jpg(tmp_path / f"{i}.jpg"), arcname=f"{i:02d}.jpg")
    with pytest.raises(UnsupportedInput, match="2 to 10 images"):
        BurstSource(zpath, "cam-01", "x")


def test_burst_rejects_non_zip(tmp_path):
    bad = tmp_path / "b.zip"
    bad.write_bytes(b"nope")
    with pytest.raises(UnsupportedInput, match="not a zip"):
        BurstSource(bad, "cam-01", "x")


def test_exif_time_reads_datetime_original():
    blob = b"\xff\xd8\xff\xe1\x00\x40Exif\x00\x00MM\x00*...2026:10:05 10:15:07\x00rest"
    assert exif_time(blob) == datetime(2026, 10, 5, 10, 15, 7, tzinfo=UTC)
    assert exif_time(b"\xff\xd8no exif here") is None
    assert exif_time(b"Exif\x00\x002026:13:45 99:00:00") is None


def test_factory_picks_by_extension(tmp_path, clip):
    mov = tmp_path / "a.MOV"
    mov.write_bytes(clip.read_bytes())
    zpath = tmp_path / "a.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for i in range(2):
            z.write(_jpg(tmp_path / f"{i}.jpg"), arcname=f"{i}.jpg")
    cases = {
        clip: VideoFileSource,
        mov: VideoFileSource,
        _jpg(tmp_path / "a.jpeg"): ImageSource,
        _jpg(tmp_path / "a.png"): ImageSource,
        zpath: BurstSource,
    }
    for path, kind in cases.items():
        source = source_for(path, "cam-01", "x", START)
        assert isinstance(source, kind), path.name
        assert source.camera_id == "cam-01"


def test_factory_rejects_unknown_types(tmp_path):
    with pytest.raises(UnsupportedInput, match=r"unsupported input type \.gif"):
        source_for(tmp_path / "a.gif", "cam-01", "x")
    with pytest.raises(UnsupportedInput, match=r"\(none\)"):
        source_for(tmp_path / "noext", "cam-01", "x")
