"""Shared offline asset policy and bounded, DNS-pinned HTTPS resolution."""

from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import json
import re
import queue
import threading
import socket
import ssl
import time
import warnings
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 10 * 1024 * 1024
MAX_ASSETS = 128
MAX_TOTAL_BYTES = 100 * 1024 * 1024
# Allows the full unique-image budget after base64 expansion, plus manuscript text.
MAX_HTML_BYTES = 144 * 1024 * 1024
TIMEOUT = 30
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "svg": "image/svg+xml"}
ASSET_PATH = re.compile(r"/sha256/([0-9a-f]{64})\.(png|jpg|jpeg|webp|svg)\Z")
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")


class AssetError(ValueError):
    pass


def base64_size(size: int) -> int:
    return 4 * ((size + 2) // 3)


def check_html_size(size: int) -> None:
    if size > MAX_HTML_BYTES:
        raise AssetError(f"canonical HTML exceeds {MAX_HTML_BYTES // (1024 * 1024)} MiB size limit")


@dataclass(frozen=True)
class AssetPolicy:
    origin: str

    def __post_init__(self):
        # Require one canonical spelling, with no port, userinfo, IDN or path.
        if not isinstance(self.origin, str) or not self.origin.startswith("https://") or not HOST.fullmatch(self.origin[8:]):
            raise AssetError("asset origin must be a canonical HTTPS DNS origin without a path or port")

    def parse(self, target: str) -> tuple[str, str]:
        # Compare the ENTIRE parsed Markdown destination, without URL normalization.
        # Thus empty ?, #, percent escapes, backslashes and authority tricks all fail.
        prefix = self.origin + "/"
        if not target.startswith(prefix):
            raise AssetError("unsupported/external URI: image must use the approved asset origin")
        match = ASSET_PATH.fullmatch(target[len(self.origin):])
        if not match:
            raise AssetError("asset URL requires exact /sha256/<64 lowercase hex>.<supported image extension>")
        return match[1], match[2]


def load_policy(root: Path) -> AssetPolicy | None:
    path = root / "asset-policy.json"
    if not path.exists():
        return None  # Historical sources without remote images still validate.
    if path.is_symlink():
        raise AssetError("asset-policy.json must not be a symlink")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise AssetError("cannot read asset-policy.json") from exc
    if not isinstance(data, dict) or set(data) != {"origin"}:
        raise AssetError("asset-policy.json must contain only origin")
    return AssetPolicy(data["origin"])


def svg_errors(raw: bytes) -> list[str]:
    # Decode first: byte searches alone miss UTF-16 DTDs/entities.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ["SVG must use UTF-8"]
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?(?!xml\s)", text, re.I):
        return ["SVG DTD, entity declarations and processing instructions prohibited"]
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return ["invalid SVG XML"]
    namespace = "{http://www.w3.org/2000/svg}"
    allowed = {"svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse",
               "line", "polyline", "polygon", "text", "tspan", "textPath",
               "clipPath", "mask", "marker", "pattern", "linearGradient",
               "radialGradient", "stop"}
    errors = []
    if root.tag != namespace + "svg":
        errors.append("SVG root must be SVG namespace <svg>")
    for node in root.iter():
        if node.tag not in {namespace + name for name in allowed}:
            errors.append("SVG element is not permitted by the passive asset profile")
        for key, value in node.attrib.items():
            name = key.split("}")[-1].lower()
            if (name.startswith("on") or name in {"style", "href", "src", "base"}
                    or (key.startswith("{") and key not in {
                        "{http://www.w3.org/XML/1998/namespace}space",
                        "{http://www.w3.org/XML/1998/namespace}lang"})
                    or re.search(r"url\s*\(|@import|expression\s*\(", value, re.I)
                    or "\\" in value):
                errors.append("SVG attribute is not permitted by the passive asset profile")
    return sorted(set(errors))


def verify_bytes(raw: bytes, extension: str, content_type: str) -> str:
    if not raw or len(raw) > MAX_BYTES:
        raise AssetError("asset is empty or exceeds 10 MiB limit")
    expected = MIME.get(extension)
    if expected is None or content_type.split(";", 1)[0].strip().lower() != expected:
        raise AssetError("asset response MIME type does not match extension")
    if extension == "svg":
        errors = svg_errors(raw)
        if errors:
            raise AssetError("; ".join(errors))
    else:
        from PIL import Image
        raster_format = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}[extension]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw), formats=[raster_format]) as image:
                    if image.format != raster_format:
                        raise AssetError("actual image type does not match extension")
                    if image.width * image.height > 40_000_000:
                        raise AssetError("image exceeds 40 million pixel limit")
                    if getattr(image, "n_frames", 1) != 1:
                        raise AssetError("animated images are not supported")
                    image.verify()
                # verify() alone does not necessarily decompress pixel data.
                with Image.open(io.BytesIO(raw), formats=[raster_format]) as image:
                    image.load()
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as exc:
            raise AssetError("invalid or unsafe raster image") from exc
    return expected


def public_addresses(host: str) -> list[str]:
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.put(exc)
    # getaddrinfo has no Python socket timeout; bound DNS separately.
    threading.Thread(target=resolve, daemon=True).start()
    try:
        records = result.get(timeout=TIMEOUT)
    except queue.Empty as exc:
        raise AssetError("asset origin DNS lookup timed out") from exc
    if isinstance(records, OSError):
        raise AssetError("asset origin DNS lookup failed") from records
    addresses = sorted({record[4][0] for record in records})
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise AssetError("asset origin must resolve exclusively to public addresses")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect only to prevalidated IPs, retaining TLS hostname/SNI verification."""

    def __init__(self, host: str, addresses: list[str], timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.addresses = addresses

    def connect(self):
        # No second hostname resolution: prevent DNS rebinding between check/use.
        last_error = None
        deadline = time.monotonic() + self.timeout
        for address in self.addresses:
            raw = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                raw = socket.create_connection((address, 443), timeout=remaining)
                raw.settimeout(max(0.001, deadline - time.monotonic()))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                self.transport = self.sock
                return
            except OSError as exc:
                last_error = exc
                if raw is not None:
                    raw.close()
        raise AssetError("cannot establish verified TLS connection to asset origin") from last_error


@contextmanager
def public_response(url: str, accept: str, *, timeout=TIMEOUT):
    """One unauthenticated HTTPS GET with DNS pinning, a deadline and no redirects."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if (parsed.scheme != "https" or host is None or not HOST.fullmatch(host)
            or parsed.netloc != host or parsed.fragment or parsed.query):
        raise AssetError("public URL requires a canonical HTTPS host and path")
    deadline = time.monotonic() + timeout
    addresses = public_addresses(host)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AssetError("public download deadline exceeded")
    connection = PinnedHTTPSConnection(host, addresses, remaining)
    def abort():
        transport = getattr(connection, "transport", None)
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            transport.close()
    watchdog = threading.Timer(remaining, abort)
    watchdog.daemon = True
    watchdog.start()
    try:
        connection.request("GET", parsed.path or "/", headers={
            "Accept": accept, "Accept-Encoding": "identity", "User-Agent": "Press-archive-verifier/1"})
        yield connection.getresponse()
        if time.monotonic() >= deadline:
            raise AssetError("public download deadline exceeded")
    except (OSError, http.client.HTTPException) as exc:
        raise AssetError(f"public download failed at {url}") from exc
    finally:
        watchdog.cancel()
        connection.close()


def response_chunks(response, limit):
    if response.getheader("Content-Encoding", "identity").lower() != "identity":
        raise AssetError("encoded response prohibited")
    length = response.getheader("Content-Length")
    if length is not None and (not re.fullmatch(r"[0-9]+", length) or int(length) > limit):
        raise AssetError("invalid or oversized Content-Length")
    size = 0
    while chunk := response.read1(min(65536, limit + 1 - size)):
        size += len(chunk)
        if size > limit:
            raise AssetError("download exceeds size limit")
        yield chunk
    if length is not None and size != int(length):
        raise AssetError("response is truncated")


def fetch_asset(policy: AssetPolicy, url: str) -> tuple[bytes, str]:
    digest, extension = policy.parse(url)
    with public_response(url, MIME[extension]) as response:
        if response.status != 200:
            raise AssetError(f"asset fetch requires HTTP 200 (received {response.status}); redirects prohibited")
        raw = b"".join(response_chunks(response, MAX_BYTES))
        if hashlib.sha256(raw).hexdigest() != digest:
            raise AssetError("asset SHA-256 does not match URL")
        media_type = verify_bytes(raw, extension, response.getheader("Content-Type", ""))
        return raw, media_type
