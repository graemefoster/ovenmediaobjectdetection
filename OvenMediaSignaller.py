from pyee import AsyncIOEventEmitter
import logging
import asyncio
import websockets
import json

from aiortc import RTCIceServer, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

logger = logging.getLogger("pc")

class OvenMediaConnectionException(Exception):
    pass

class OvenMediaSignaller(AsyncIOEventEmitter):

    def __init__(
            self,
            oven_media_url,
            broadcast=False,
            tracks_to_broadcast=None):
        super().__init__()
        self.connected = False
        self.__oven_media_url = oven_media_url
        self.__broadcast = broadcast
        self.__websocket = None
        self.__pc = None
        self.__tracks_to_broadcast = tracks_to_broadcast

    @staticmethod
    def __log_info(msg, *args):
        logger.info(" " + msg, *args)

    @staticmethod
    def __log_debug(msg, *args):
        logger.info(" " + msg, *args)

    async def stop(self):
        logger.debug("Stopping signaller and persistent connection")
        await self.__pc.close()
        await self.__websocket.close()

    async def connect(self):

        logger.debug("Sending 'request_offer' command")
        self.__websocket = await websockets.connect(self.__oven_media_url)
        _ = await self.__websocket.send(json.dumps({"command": "request_offer"}))
        offer_raw = await self.__websocket.recv()

        # If running in Docker on a Mac host address returns as 172.17.0.2
        offer_json = json.loads(
            offer_raw.replace("172.17.0.2", "127.0.0.1")
        )
        if offer_json["code"] != 200:
            raise OvenMediaConnectionException("Could not connect to OvenMedia signalling service")

        self.__log_debug("Received offer")
        self.__log_debug(offer_raw)

        ice_servers = list()
        for iceServerJson in offer_json["iceServers"]:
            ice_server = RTCIceServer(
                credential=iceServerJson["credential"],
                username=iceServerJson["username"],
                urls=iceServerJson["urls"],
                credentialType="password"
            )
            self.__log_debug("Discovered ice server at " + ice_server.urls[0])
            ice_servers.append(ice_server)

        config = RTCConfiguration(ice_servers)
        pc = RTCPeerConnection(config)

        direction = "recvonly"
        if self.__broadcast:
            self.__log_debug("Broadcast mode. Changing direction to sendonly")
            direction = "sendonly"

        pc.addTransceiver("audio", direction=direction)
        pc.addTransceiver("video", direction=direction)

        if self.__tracks_to_broadcast is not None:
            for track in self.__tracks_to_broadcast:
                pc.addTrack(track)

        @pc.on("signalingstatechange")
        async def on_signalingstatechange():
            self.emit("signalingstatechange", pc)

        @pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            self.emit("icegatheringstatechange", pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self.emit("connectionstatechange", pc)
            self.__log_info("Connection state is %s", pc.connectionState)
            if pc.connectionState == "failed":
                await pc.close()

        @pc.on("track")
        def on_track(track):
            self.__log_info("Track %s received", track.kind)
            self.emit("track", track)

            @track.on("ended")
            async def on_ended():
                self.__log_info("Track %s ended", track.kind)

        sdp = offer_json["sdp"]["sdp"]

        self.__log_debug("Creating remote description")
        offer = RTCSessionDescription(sdp=sdp, type="offer")
        unique_id = offer_json["id"]
        peer_id = offer_json["peer_id"]
        await pc.setRemoteDescription(offer)
        self.__log_debug("Set remote description")
        self.__log_debug(sdp)

        self.__log_debug("Detecting OvenMediaEngine bundles")
        bundle = []
        for line in pc.remoteDescription.sdp.splitlines():
            if line.startswith('a=group:BUNDLE'):
                bundle_text = line[15:]
                self.__log_debug("Found bundles - %s", bundle_text)
                bundle = bundle_text.split(" ")

        for ice_candidate_json in offer_json["candidates"]:
            candidate_string = ice_candidate_json["candidate"]
            self.__log_debug("Found candidate - %s. Adding IceCandidate for each bundled sdpMid", candidate_string)
            for sdpMid in bundle:
                candidate = candidate_from_sdp(candidate_string)
                candidate.sdpMLineIndex = ice_candidate_json["sdpMLineIndex"]
                candidate.sdpMid = sdpMid
                self.__log_debug("Adding candidate line-index %s for sdpMid %s",
                                 candidate.sdpMLineIndex, candidate.sdpMid)
                await pc.addIceCandidate(candidate)

        self.__log_debug("Creating offer answer")
        answer = await pc.createAnswer()

        answer_json = json.dumps(
            {
                "id": unique_id,
                "peer_id": peer_id,
                "command": "answer",
                "sdp":
                    {
                        "type": answer.type,
                        "sdp": answer.sdp
                    }
            }
        )

        self.__log_debug(answer.sdp)
        self.__log_debug("Sending answer to Signalling server")
        await self.__websocket.send(answer_json)
        self.__log_debug("Sent answer to Signalling server")
        await pc.setLocalDescription(answer)

        # must happen after settings local description
        self.__log_debug("Sending answer to Signalling server")

        self.__log_debug("Waiting for connection state to change")

        while pc.connectionState != "connected":
            # TODO: what if it doesn't connect? Need to throw a timeout
            await asyncio.sleep(0.1)

        self.__log_debug("Connected to peer")

        self.__pc = pc

        self.connected = True
