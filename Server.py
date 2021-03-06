import argparse
import asyncio
import os
import logging
import ssl
from time import sleep

from aiohttp import web

from StreamProcessor import connect_to_ovenmedia_stream

logger = logging.getLogger("pc")
ROOT = os.path.dirname(__file__)
stop_processing_signal = {"stop": False}

async def index(request, ovenmediaserver, input_stream, ouput_stream):
    content = open(os.path.join(ROOT, "index.html"), "r").read()
    content = content.replace("{input-stream}", f'{ovenmediaserver}/{input_stream}')
    content = content.replace("{output-stream}", f'{ovenmediaserver}/{ouput_stream}')
    return web.Response(content_type="text/html", text=content)


def log_info(msg, *args):
    logger.info(" " + msg, *args)


def stop_processing_callback():
    return stop_processing_signal["stop"]


async def on_shutdown(app):
    # stop the processing thread and wait for it to join
    # close peer connections
    log_info("Requested processor stop")
    stop_processing_signal["stop"] = True


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="WebRTC audio / video / data-channels demo"
    )
    parser.add_argument("--cert-file", help="SSL certificate file (for HTTPS)")
    parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8081, help="Port for HTTP server (default: 8081)"
    )
    parser.add_argument("--ovenmediaserver", "--oven", default="ws://ovenmediatest.graemefoster.net:3333")
    parser.add_argument("--input-stream", "--in", default="app/stream")
    parser.add_argument("--output-stream", "--out", default="app/stream2")
    parser.add_argument("--model-rate", "--mr", default="20")
    parser.add_argument("--verbose", "-v", action="count")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.cert_file:
        ssl_context = ssl.SSLContext()
        ssl_context.load_cert_chain(args.cert_file, args.key_file)
    else:
        ssl_context = None

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", lambda req:index(req, args.ovenmediaserver, args.input_stream, args.output_stream))

    loop = asyncio.new_event_loop()

    stream_publisher = loop.create_task(
        connect_to_ovenmedia_stream(
            args.ovenmediaserver,
            args.input_stream,
            args.output_stream,
            args.model_rate,
            stop_processing_callback))

    web.run_app(
        app,
        access_log=None,
        host=args.host,
        port=args.port,
        ssl_context=ssl_context,
        loop=loop
    )

    publisher_finished = False
    while not publisher_finished:
        sleep(0.1)
        publisher_finished = stream_publisher.done()

    log_info("Server has shutdown")
