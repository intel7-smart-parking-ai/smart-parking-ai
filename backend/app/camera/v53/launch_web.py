"""Run the local UI and open its link after readiness; cameras start on user input."""
import argparse
import logging
import logging.handlers
import threading
import webbrowser
from pathlib import Path
import uvicorn
from web_live.server import app


def setup_logging(folder=Path(__file__).resolve().parent/'runtime'/'logs'):
    """Show the 1-minute frame/heartbeat/delivery INFO lines (README) and keep them after the window closes.

    Without this the root logger stays at WARNING and those lines were dropped (11/10).
    Logs never contain tokens or request bodies.
    """
    handlers=[logging.StreamHandler()]
    try:
        folder.mkdir(parents=True,exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(folder/'v53.log',maxBytes=5_000_000,backupCount=5,encoding='utf-8'))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s',handlers=handlers,force=True)
    return handlers


def open_when_ready(server, finished, *, browser=True, start_camera=False):
    while not finished.wait(.1):
        if server.started and not server.should_exit:
            if start_camera and app.state.live:
                try:app.state.live.start()
                except (ValueError,RuntimeError):
                    logging.exception('Camera startup failed; F5 can retry')
            if browser:webbrowser.open(f'http://127.0.0.1:{server.config.port}/')
            return


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8011);parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--start-camera',action='store_true',help='Start local camera collection when the server opens')
    args=parser.parse_args()
    setup_logging()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,access_log=False))
    finished=threading.Event()
    if not args.no_browser or args.start_camera:
        threading.Thread(target=open_when_ready,args=(server,finished),
                         kwargs=dict(browser=not args.no_browser,start_camera=args.start_camera),daemon=True).start()
    try:server.run()
    finally:finished.set()
    if not server.started:raise SystemExit(1)
