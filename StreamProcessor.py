import logging
from concurrent.futures import ThreadPoolExecutor
import torch
import cv2
from av import VideoFrame
from aiortc import MediaStreamTrack
from aiortc.contrib.media import MediaRelay
import asyncio

from OvenMediaSignaller import OvenMediaSignaller, OvenMediaConnectionException

logger = logging.getLogger("pc")
relay = MediaRelay()
model = torch.hub.load('ultralytics/yolov5', 'yolov5s')
keep_processing = True
model_executor = ThreadPoolExecutor(max_workers=2)

class VideoTransformTrack(MediaStreamTrack):
    """
    A video stream track that transforms frames from an another track.
    """
    kind = "video"

    def __init__(self, track):
        super().__init__()  # don't forget this!
        self.track = track
        self.frame_count = 0
        self.results = None
        self.current_process_thread = None
        self.running_models = 0

    async def process_frame_async(self, frame):
        if self.running_models == 0:
            self.running_models = self.running_models + 1
            loop = asyncio.get_event_loop()
            self.results = await loop.run_in_executor(model_executor, self.process_frame, frame)
            self.running_models = self.running_models - 1

    # Process the frame and overwrite the results when we've got new ones
    @staticmethod
    def process_frame(frame):
        img = frame.to_ndarray(format="bgr24")
        results = model(img)
        return results.pandas().xyxy[0]

    async def recv(self):
        frame = await self.track.recv()

        current_image = frame.to_ndarray(format="bgr24")

        # Really don't know Python but trying to process image out of main event loop
        if self.frame_count % 10 == 0:
            loop = asyncio.get_event_loop()
            loop.create_task(self.process_frame_async(frame))

        if self.results is not None:
            for result_index, row in self.results.iterrows():
                # if row.confidence > 0.65:
                cv2.rectangle(current_image, (int(row.xmin), int(row.ymin)), (int(row.xmax), int(row.ymax)),
                              (0, 255, 0), 2)
                cv2.putText(current_image, row['name'], (int(row.xmin), int(row.ymin) + 15), cv2.FONT_HERSHEY_COMPLEX,
                            1, (0, 255, 0))

        new_frame = VideoFrame.from_ndarray(current_image, format="bgr24")
        new_frame.pts = frame.pts
        new_frame.time_base = frame.time_base
        self.frame_count = self.frame_count + 1

        return new_frame


def log_info(msg, *args):
    logger.info(" " + msg, *args)


async def connect_to_ovenmedia_stream(stop_processing_callback):
    signaller = OvenMediaSignaller(
        oven_media_url="ws://ovenmediatest.graemefoster.net:3333/app/stream",
        broadcast=False
    )

    audio = {}
    video = {}

    @signaller.on("track")
    def on_track(track):
        if track.kind == "audio":
            audio["track"] = track
        elif track.kind == "video":
            video["track"] = VideoTransformTrack(track)

    while not signaller.connected:
        try:
            await signaller.connect()
        except OvenMediaConnectionException:
            await asyncio.sleep(2)

    publish_signaller = build_publishing_signaller(audio["track"], video["track"])
    await publish_signaller.connect()

    stop = False
    while not stop:
        await asyncio.sleep(0.1)
        stop = stop_processing_callback()

    if publish_signaller is not None:
        await publish_signaller.stop()
    if signaller is not None:
        await signaller.stop()


def build_publishing_signaller(audio, video):
    return OvenMediaSignaller(
        oven_media_url="ws://ovenmediatest.graemefoster.net:3333/app/stream2?direction=send",
        broadcast=True,
        tracks_to_broadcast=[audio, video]
    )
