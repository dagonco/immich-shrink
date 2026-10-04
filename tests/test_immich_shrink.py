#!/usr/bin/env python3
"""
Unit tests for immich-shrink. Pure logic only: no network, no ffmpeg runs.

    python3 -m unittest discover -s tests
"""
import datetime as dt
import importlib.util
import os
import pathlib
import tempfile
import unittest
from argparse import Namespace
from importlib.machinery import SourceFileLoader
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_loader = SourceFileLoader("immich_shrink", str(ROOT / "immich-shrink"))
_spec = importlib.util.spec_from_loader("immich_shrink", _loader)
shrink = importlib.util.module_from_spec(_spec)
_loader.exec_module(shrink)


def gopro_info():
    return {"format": {"duration": "10.4"}, "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuvj420p",
         "width": 2704, "height": 1520, "color_primaries": "bt709", "color_transfer": "bt709",
         "color_space": "bt709"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        {"index": 2, "codec_type": "data", "codec_tag_string": "tmcd"},
        {"index": 3, "codec_type": "data", "codec_tag_string": "gpmd"}]}


def phone_hdr_info():
    return {"format": {"duration": "5.4"}, "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le",
         "width": 1920, "height": 1080, "color_primaries": "bt2020", "color_transfer": "arib-std-b67",
         "color_space": "bt2020nc"},
        {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le"}]}


class Names(unittest.TestCase):
    def test_mp4_keeps_original_name_and_case(self):
        self.assertEqual(shrink.mp4_name("trip/GX010093.MP4"), pathlib.Path("trip/GX010093.MP4"))

    def test_other_containers_become_mp4(self):
        self.assertEqual(shrink.mp4_name("IMG_0001.MOV"), pathlib.Path("IMG_0001.mp4"))

    def test_skipped_files(self):
        for rel in ("GL010093.LRV", "GX010093.THM", "@eaDir/x.mp4", "#recycle/x.mp4", ".DS_Store", "a/.hidden.mp4"):
            self.assertTrue(shrink.skipped(pathlib.Path(rel)), rel)
        self.assertFalse(shrink.skipped(pathlib.Path("trip/GX010093.MP4")))


class WhatToConvert(unittest.TestCase):
    def test_above_1080p_is_converted_even_at_low_bitrate(self):
        self.assertTrue(shrink.worth_converting(3840, 2160, 10_000_000, 10))

    def test_1080p_at_gopro_bitrate_is_converted(self):
        self.assertTrue(shrink.worth_converting(1920, 1080, 56_000_000, 10))  # 45 Mbps

    def test_small_1080p_is_left_alone(self):
        self.assertFalse(shrink.worth_converting(1920, 1080, 12_000_000, 10))  # ~10 Mbps

    def test_portrait_uses_the_short_side(self):
        self.assertFalse(shrink.worth_converting(1080, 1920, 12_000_000, 10))

    def test_unknown_duration_relies_on_resolution(self):
        self.assertFalse(shrink.worth_converting(1920, 1080, 10**9, 0))


class FfmpegCommand(unittest.TestCase):
    def test_gopro_keeps_telemetry_and_8_bit(self):
        cmd = shrink.ffmpeg_command("in.MP4", "out.mp4", gopro_info(), gentle=False)
        self.assertIn("libsvtav1", cmd)
        self.assertTrue(cmd[cmd.index("-vf") + 1].endswith(",format=yuv420p"))
        self.assertIn("0:3", cmd)          # gpmd stream mapped by index
        self.assertNotIn("0:2", cmd)       # tmcd is not
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")

    def test_hdr_stays_10_bit_with_its_color_tags(self):
        cmd = shrink.ffmpeg_command("in.mp4", "out.mp4", phone_hdr_info(), gentle=False)
        self.assertTrue(cmd[cmd.index("-vf") + 1].endswith(",format=yuv420p10le"))
        self.assertEqual(cmd[cmd.index("-color_trc") + 1], "arib-std-b67")
        self.assertEqual(cmd[cmd.index("-color_primaries") + 1], "bt2020")

    def test_audio_mp4_cannot_hold_is_reencoded(self):
        cmd = shrink.ffmpeg_command("in.mov", "out.mp4", phone_hdr_info(), gentle=False)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")

    def test_gentle_lowers_priority(self):
        with mock.patch.object(shrink, "MAC", True):
            self.assertEqual(shrink.ffmpeg_command("i", "o", gopro_info(), True)[:2], ["taskpolicy", "-b"])
        with mock.patch.object(shrink, "MAC", False):
            cmd = shrink.ffmpeg_command("i", "o", gopro_info(), True)
            self.assertEqual(cmd[:3], ["nice", "-n", "19"])
            self.assertNotIn("videotoolbox", cmd)


class ImmichHelpers(unittest.TestCase):
    def test_api_base(self):
        self.assertEqual(shrink.api_base("http://nas:2283"), "http://nas:2283/api")
        self.assertEqual(shrink.api_base("http://nas:2283/api/"), "http://nas:2283/api")

    def test_asset_seconds_handles_both_duration_formats(self):
        self.assertEqual(shrink.asset_seconds({"duration": 839855}), 839.855)
        self.assertAlmostEqual(shrink.asset_seconds({"duration": "0:14:00.500000"}), 840.5)
        self.assertEqual(shrink.asset_seconds({"duration": None}), 0)

    def test_candidates(self):
        def asset(id, size, w=3840, h=2160, **kw):
            return {"id": id, "duration": 60_000, "visibility": "timeline",
                    "exifInfo": {"fileSizeInByte": size, "exifImageWidth": w, "exifImageHeight": h}, **kw}
        assets = [asset("small", 10**9), asset("big", 3 * 10**9), asset("done", 10**9),
                  asset("new", 10**9), asset("wedding", 10**9), asset("motion", 10**9, visibility="hidden"),
                  asset("phone", 50 * 10**6, 1920, 1080)]
        state = {"done": {"status": "done", "new": "new"}}
        todo = shrink.candidates(assets, state, kept={"wedding"})
        self.assertEqual([a["id"] for a in todo], ["big", "small"])


class Log(unittest.TestCase):
    def test_replay_keeps_latest_state_and_new_id(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "replaced.jsonl"
            shrink.write_log(log, old="a", name="A.mp4", new="b", status="uploaded")
            shrink.write_log(log, old="a", status="done")
            shrink.write_log(log, old="c", status="skipped", reason="no real saving")
            state = shrink.read_log(log)
        self.assertEqual(state["a"]["status"], "done")
        self.assertEqual(state["a"]["new"], "b")
        self.assertEqual(state["c"]["status"], "skipped")

    def test_missing_log_is_empty(self):
        self.assertEqual(shrink.read_log(pathlib.Path("/nonexistent/replaced.jsonl")), {})


class Config(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = pathlib.Path(self.dir.name) / "config.toml"
        self.file.write_text('url = "http://file:2283"\napi_key = "file-key"\nkeep_albums = ["Wedding"]\n')
        self.patch = mock.patch.object(shrink, "CONFIG_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.dir.cleanup()

    def test_file_values(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            base, key, keep = shrink.load_config(Namespace(url=None, keep_album=["Diplomas"]))
        self.assertEqual((base, key, keep), ("http://file:2283/api", "file-key", ["Wedding", "Diplomas"]))

    def test_flag_and_env_win(self):
        with mock.patch.dict(os.environ, {"IMMICH_URL": "http://env:2283", "IMMICH_API_KEY": "env-key"}):
            base, key, _ = shrink.load_config(Namespace(url="http://flag:2283", keep_album=None))
        self.assertEqual((base, key), ("http://flag:2283/api", "env-key"))

    def test_missing_key_exits(self):
        self.file.write_text('url = "http://file:2283"\n')
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            shrink.load_config(Namespace(url=None, keep_album=None))


class Deadline(unittest.TestCase):
    def test_later_today(self):
        now = dt.datetime(2026, 10, 4, 7, 0)
        self.assertEqual(shrink.deadline("08:00", now), dt.datetime(2026, 10, 4, 8, 0))

    def test_rolls_over_to_tomorrow(self):
        now = dt.datetime(2026, 10, 4, 23, 30)
        self.assertEqual(shrink.deadline("08:00", now), dt.datetime(2026, 10, 5, 8, 0))


if __name__ == "__main__":
    unittest.main()
