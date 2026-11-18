import shutil
import subprocess

import pytest
from PIL import Image

from uploader import bgm, compose

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 없음")


def _mean_db(path) -> float:
    out = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(out.split("mean_volume:")[1].split("dB")[0])


def test_bgm_survives_concat_and_prepend_cover(tmp_path):
    img = tmp_path / "c.jpg"
    Image.new("RGB", (1080, 1920), (200, 100, 50)).save(img)
    clip = tmp_path / "clip.mp4"
    compose.run_ffmpeg(["-loop", "1", "-t", "2", "-i", str(img), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                        "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)])
    body = compose.concat([clip, clip], tmp_path / "body_silent.mp4")
    body = compose.replace_audio(body, bgm.write(4, 3, tmp_path / "m.wav"), tmp_path / "body.mp4")
    reel = compose.prepend_cover(body, img, tmp_path / "reel.mp4")
    assert _mean_db(reel) > -40  # 커버를 붙여도 BGM이 살아 있어야 한다
