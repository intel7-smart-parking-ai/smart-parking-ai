import json
import os
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read(path, sample):
    base = os.getenv("REPORT_API_BASE")
    if not base:
        return sample
    url = urlsplit(base)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or url.path not in ("", "/") or url.username or url.query or url.fragment):
        raise ValueError("Use a local HTTP API origin")
    headers = {}
    if token := os.getenv("REPORT_API_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with build_opener(NoRedirect()).open(Request(base.rstrip("/") + path, headers=headers),
                                         timeout=4) as response:
        return json.load(response)


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid count")
    return value
