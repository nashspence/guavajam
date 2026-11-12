"""Inspect downloaded media bytes and visible caption timing, without transcoding."""
from __future__ import annotations
from pathlib import Path
import re
from asset_policy import AssetError, verify_bytes


def captions(raw: bytes):
    try:text=raw.decode('utf-8','strict')
    except UnicodeError:raise AssetError('WebVTT must be UTF-8') from None
    if '\x00' in text or '\r' in text or not text.startswith('WEBVTT\n\n'):
        raise AssetError('captions require an LF WebVTT header')
    cue=re.compile(r'(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3}) --> (\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})\Z')
    count,previous=0,-1
    for block in text[8:].strip().split('\n\n'):
        lines=block.splitlines()
        if not lines:continue
        if '-->' not in lines[0]:lines=lines[1:]
        match=cue.fullmatch(lines[0]) if lines else None
        if not match or len(lines)<2 or not ''.join(lines[1:]).strip():raise AssetError('WebVTT requires timed, nonempty plain-text cues')
        nums=list(map(int,match.groups()))
        start=((nums[0]*60+nums[1])*60+nums[2])*1000+nums[3]
        end=((nums[4]*60+nums[5])*60+nums[6])*1000+nums[7]
        if start<previous or end<=start:raise AssetError('WebVTT cue times must be ordered and have positive duration')
        if any('<' in line or '>' in line for line in lines[1:]):raise AssetError('caption cues require plain text')
        previous=start;count+=1
    if not count:raise AssetError('captions require at least one meaningful cue')
    return count


def inspect(stream, extension, *, sound=None):
    """Verify real container/codec identities and decode a frame from each track."""
    stream.seek(0)
    if extension=='vtt':return {'cues':captions(stream.read(10*1024*1024+1))}
    if extension not in {'mp3','mp4'}:
        from asset_policy import MIME
        raw=stream.read(10*1024*1024+1);verify_bytes(raw,extension,MIME[extension]);return {}
    import av
    try:
        with av.open(stream,mode='r',format='mp3' if extension=='mp3' else 'mp4',options={'enable_drefs':'0','use_absolute_path':'0','protocol_whitelist':'file,pipe'}) as container:
            streams=list(container.streams)
            codecs=[s.codec_context.name for s in streams]
            if extension=='mp3':
                if len(streams)!=1 or streams[0].type!='audio' or codecs[0] not in {'mp3','mp3float'}:
                    raise AssetError('MP3 requires one real MPEG audio track')
            else:
                if 'mp4' not in container.format.name or len(container.streams.video)!=1:
                    raise AssetError('MP4 requires one H.264 video track')
                if container.streams.video[0].codec_context.name!='h264' or len(container.streams.audio)>1:
                    raise AssetError('video profile requires H.264 and at most one AAC audio track')
                if any(s.type not in {'video','audio'} for s in streams) or any(s.codec_context.name!='aac' for s in container.streams.audio):
                    raise AssetError('MP4 permits only H.264 video and AAC audio')
                if sound=='none' and container.streams.audio:raise AssetError('silent video contains an audio track')
                if sound=='present' and not container.streams.audio:raise AssetError('captioned video must contain its declared audio track')
            if any(v.width*v.height>40000000 for v in container.streams.video):raise AssetError('video frame exceeds the decoded pixel budget')
            if any(a.codec_context.sample_rate>192000 or a.codec_context.channels>8 for a in container.streams.audio):raise AssetError('audio exceeds the supported channel/rate budget')
            decoded=set()
            for packet in container.demux():
                for frame in packet.decode():decoded.add(packet.stream.index)
                if decoded=={s.index for s in streams}:break
            if decoded!={s.index for s in streams}:raise AssetError('media contains an empty or undecodable track')
            return {'codecs':codecs}
    except AssetError:raise
    except Exception:raise AssetError('media container or codec inspection failed') from None
