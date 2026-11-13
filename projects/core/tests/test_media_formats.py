"""Real codecs and complete caption syntax are checked in addition to hashes."""
import io,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from guavajam_core.media_formats import inspect,captions
from guavajam_core.asset_policy import AssetError
from guavajam_test_support.media_fixture import video,audio

class MediaTests(unittest.TestCase):
    def test_real_h264_aac_mp3_and_silent_video_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            full=video(root/'full.mp4');silent=video(root/'silent.mp4',audio=False);sound=audio(root/'sound.mp3')
            self.assertEqual({'h264','aac'},set(inspect(io.BytesIO(full),'mp4',sound='present')['codecs']))
            self.assertEqual(['h264'],inspect(io.BytesIO(silent),'mp4',sound='none')['codecs'])
            self.assertTrue(inspect(io.BytesIO(sound),'mp3')['codecs'][0].startswith('mp3'))
            with self.assertRaisesRegex(AssetError,'silent'):inspect(io.BytesIO(full),'mp4',sound='none')
            with self.assertRaisesRegex(AssetError,'declared audio'):inspect(io.BytesIO(silent),'mp4',sound='present')
            with self.assertRaises(AssetError):inspect(io.BytesIO(full),'mp3')
            with self.assertRaises(AssetError):inspect(io.BytesIO(video(root/'wrong.mp4',codec='mpeg4')),'mp4')
    def test_garbage_and_network_playlist_cannot_be_media(self):
        for data in [b'fake video',b'#EXTM3U\nhttps://private.invalid/stream',b'\0'*64]:
            with self.subTest(data=data[:8]),self.assertRaises(AssetError):inspect(io.BytesIO(data),'mp4')
    def test_captions_require_real_ordered_nonempty_cues(self):
        good=b'WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nThe speaker says hello.\n'
        self.assertEqual(1,captions(good))
        for bad in [b'WEBVTT\n\n',good.replace(b'01.500',b'00.000'),good.replace(b'The speaker says hello.',b'<script>alert(1)</script>'),good.replace(b'\n',b'\r\n')]:
            with self.subTest(bad=bad[:30]),self.assertRaises(AssetError):captions(bad)

    def test_full_hash_then_bounded_exact_version_codec_ranges(self):
        import contextlib,hashlib
        from guavajam_core.verify_media_sources import verify_one
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            raw=video(Path(directory)/'actual.mp4')
        calls=[]
        @contextlib.contextmanager
        def receive(url,mime,*,timeout,range_value=None):
            calls.append(range_value);data=raw;headers={'Content-Type':'video/mp4','Accept-Ranges':'bytes','X-Press-Asset-Version':'fixture-version'}
            status=200
            if range_value:
                start,end=map(int,range_value.removeprefix('bytes=').split('-'));data=raw[start:end+1];status=206;headers['Content-Range']=f'bytes {start}-{end}/{len(raw)}'
            class Response(io.BytesIO):
                def getheader(self,key,default=None):return headers.get(key,default)
            response=Response(data);response.status=status
            with response:yield response
        url='https://assets.example.org/sha256/'+hashlib.sha256(raw).hexdigest()+'.mp4'
        self.assertEqual(len(raw),verify_one(url,'https://assets.example.org',response_factory=receive,sound='present'))
        self.assertIsNone(calls[0]);self.assertTrue(any(calls[1:]))
        with patch('guavajam_core.media_ranges.MAX_PROBE_BYTES',1),self.assertRaises(AssetError):verify_one(url,'https://assets.example.org',response_factory=receive)
