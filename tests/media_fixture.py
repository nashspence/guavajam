"""Generate tiny real codec fixtures with the pinned test toolchain."""
from pathlib import Path
import av
from PIL import Image


def video(path,*,audio=True,codec='libx264'):
    with av.open(str(path),'w',format='mp4') as output:
        stream=output.add_stream(codec,rate=2);stream.width=32;stream.height=32;stream.pix_fmt='yuv420p'
        sound=output.add_stream('aac',rate=44100) if audio else None
        if sound:sound.layout='mono'
        for i in range(4):
            frame=av.VideoFrame.from_image(Image.new('RGB',(32,32),(20*i,90,120)));frame.pts=i
            for packet in stream.encode(frame):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
        if audio:
            for i in range(87):
                frame=av.AudioFrame(format='fltp',layout='mono',samples=1024);frame.sample_rate=44100;frame.pts=i*1024
                for plane in frame.planes:plane.update(bytes(plane.buffer_size))
                for packet in sound.encode(frame):output.mux(packet)
            for packet in sound.encode():output.mux(packet)
    return Path(path).read_bytes()


def audio(path,*,frames=20):
    with av.open(str(path),'w',format='mp3') as output:
        stream=output.add_stream('libmp3lame',rate=44100);stream.layout='mono'
        for i in range(frames):
            frame=av.AudioFrame(format='fltp',layout='mono',samples=1152);frame.sample_rate=44100;frame.pts=i*1152
            for plane in frame.planes:plane.update(bytes(plane.buffer_size))
            for packet in stream.encode(frame):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    return Path(path).read_bytes()
