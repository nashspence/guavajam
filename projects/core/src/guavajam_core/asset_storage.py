"""Narrow B2 Native API client for local asset operations; never log credentials."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import stat
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from .asset_policy import AssetError, MAX_BYTES, public_response, response_chunks, verify_bytes

PREFIX = "sha256/"
LEASE_PREFIX = PREFIX + ".leases/"
CACHE_CONTROL = "public, max-age=31536000, immutable, no-transform"
GRACE_DAYS = 30
MAX_VERSIONS = 100_000
VARIABLES = {"B2_APPLICATION_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET_ID", "B2_BUCKET_NAME"}


def credentials(root, environ=None):
    """Read an optional private data file, never execute it as shell code."""
    env = dict(os.environ if environ is None else environ)
    path = Path(env.get("B2_ENV") or root / ".env.assets")
    if env.get("B2_ENV") or path.exists():
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16384):
                    raise AssetError("B2_ENV must be an owner-only regular file (mode 0600)")
                for line in stream.read().splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    key, sep, value = line.removeprefix("export ").partition("=")
                    if not sep or key not in VARIABLES:
                        raise AssetError("B2_ENV accepts only the documented B2 variable assignments")
                    parts = shlex.split(value, comments=True)
                    if len(parts) != 1:
                        raise AssetError("invalid B2_ENV assignment")
                    env[key] = parts[0]
        except (OSError, ValueError) as exc:
            raise AssetError("cannot read private B2_ENV credentials") from exc
    if any(not env.get(name) for name in VARIABLES):
        raise AssetError("set B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_ID and B2_BUCKET_NAME locally")
    return {name: env[name] for name in VARIABLES}


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def b2_url(url):
    """Tokens go only to verified HTTPS Backblaze service endpoints."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.netloc != host or parsed.fragment
            or not re.fullmatch(r"[a-z0-9-]+\.(?:backblazeb2|backblaze)\.com", host)):
        raise AssetError("unexpected B2 service endpoint")
    return url


def request(url, token, *, body=None, headers=None, operation="request"):
    headers = {"Authorization": token, **(headers or {})}
    req = urllib.request.Request(b2_url(url), data=body, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirects()).open(req, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 2 * 1024 * 1024:
                raise AssetError("unexpected B2 response")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
    except urllib.error.HTTPError as exc:
        raise AssetError(f"B2 {operation} failed (HTTP {exc.code})") from None
    except (OSError, ValueError) as exc:
        raise AssetError(f"B2 {operation} failed; no response details logged") from None


class B2:
    def __init__(self, values, *, operation):
        self.key_id=values["B2_APPLICATION_KEY_ID"];self.key=values["B2_APPLICATION_KEY"]
        basic = base64.b64encode((values["B2_APPLICATION_KEY_ID"] + ":"
                                  + values["B2_APPLICATION_KEY"]).encode()).decode()
        data = request("https://api.backblazeb2.com/b2api/v4/b2_authorize_account",
                       "Basic " + basic, operation="authorization")
        try:
            storage = data["apiInfo"]["storageApi"]
            allowed = storage["allowed"]
            buckets = allowed["buckets"]
            self.bucket_id = values["B2_BUCKET_ID"]
            self.bucket_name = values["B2_BUCKET_NAME"]
            if (buckets != [{"id": self.bucket_id, "name": self.bucket_name}]
                    or allowed["namePrefix"] not in {None, "", PREFIX}):
                raise AssetError("use a B2 key restricted to the configured single bucket (optionally restricted to sha256/)")
            caps = set(allowed["capabilities"])
            required = {"listFiles","readFileRetentions"}
            self.operations = {"b2_list_file_versions"}
            self.capabilities=caps
            self.account_id=data.get("accountId")
            self.s3_endpoint=storage.get("s3ApiUrl","")
            if operation == "upload":
                required.update({"writeFiles","readFiles"})
                self.operations.update({"b2_get_upload_url","b2_start_large_file","b2_get_upload_part_url","b2_finish_large_file","b2_cancel_large_file","b2_get_file_info"})
            elif operation == "delete":
                required.add("deleteFiles")
                self.operations.add("b2_delete_file_version")
            elif operation == "inspect":
                required.add("readFiles")
                self.operations.add("b2_get_file_info")
            elif operation == "pin":
                required.update({"readFiles","readFileRetentions","writeFiles"})
                self.operations.update({"b2_get_file_info","b2_update_file_retention","b2_get_upload_url"})
            elif operation != "preview":
                raise AssetError("unknown asset operation")
            if not required <= caps:
                raise AssetError("B2 key lacks the capabilities required for this operation")
            self.api_url = b2_url(storage["apiUrl"])
            self.download_url = b2_url(storage["downloadUrl"])
            if urlsplit(self.api_url).path or urlsplit(self.api_url).query or urlsplit(self.download_url).path or urlsplit(self.download_url).query:
                raise AssetError("unexpected B2 base endpoint")
            self.token = data["authorizationToken"]
        except (KeyError, TypeError) as exc:
            raise AssetError("incomplete B2 authorization response") from None

    def call(self, operation, **body):
        if operation not in self.operations:
            raise AssetError("B2 operation is not permitted in this client mode")
        return request(self.api_url + "/b2api/v4/" + operation, self.token,
                       body=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                       operation=operation)

    def versions(self, prefix=PREFIX):
        records, cursors, ids = [], set(), set()
        cursor = {}
        while True:
            data = self.call("b2_list_file_versions", bucketId=self.bucket_id,
                             prefix=prefix, maxFileCount=1000, **cursor)
            try:
                page = data["files"]
                if not isinstance(page, list):
                    raise ValueError()
                for item in page:
                    if (not isinstance(item["fileName"], str) or not item["fileName"].startswith(prefix)
                            or item["bucketId"] != self.bucket_id or not isinstance(item["fileId"], str)
                            or not item["fileId"] or item["fileId"] in ids
                            or type(item["uploadTimestamp"]) is not int or item["uploadTimestamp"] <= 0
                            or item["action"] not in {"upload", "hide", "start"}
                            or type(item["contentLength"]) is not int or item["contentLength"] < 0):
                        raise ValueError()
                    ids.add(item["fileId"])
                    records.append(item)
                name, file_id = data["nextFileName"], data["nextFileId"]
                if len(records) > MAX_VERSIONS:
                    raise ValueError()
                if name is None and file_id is None:
                    return records
                if (not isinstance(name, str) or not name.startswith(prefix) or not isinstance(file_id, str)
                        or not file_id or (name, file_id) in cursors or len(records) >= MAX_VERSIONS):
                    raise ValueError()
                cursors.add((name, file_id))
                cursor = {"startFileName": name, "startFileId": file_id}
            except (KeyError, TypeError, ValueError):
                raise AssetError("invalid or incomplete B2 version inventory") from None

    def upload(self, key, raw, media_type, *, cache_control=CACHE_CONTROL):
        data = self.call("b2_get_upload_url", bucketId=self.bucket_id)
        try:
            if data["bucketId"] != self.bucket_id:
                raise AssetError("upload endpoint belongs to another bucket")
            result = request(data["uploadUrl"], data["authorizationToken"], body=raw, headers={
                "X-Bz-File-Name": quote(key, safe="/"), "Content-Type": media_type,
                "Content-Length": str(len(raw)), "X-Bz-Content-Sha1": hashlib.sha1(raw).hexdigest(),
                "X-Bz-Info-b2-cache-control": quote(cache_control, safe=""),
            }, operation="upload")
            if (result["fileName"] != key or result["bucketId"] != self.bucket_id
                    or result["contentLength"] != len(raw)
                    or result["contentSha1"] != hashlib.sha1(raw).hexdigest()):
                raise AssetError("B2 upload receipt differs from the supplied asset")
            return result
        except (KeyError, TypeError):
            raise AssetError("incomplete B2 upload response") from None

    def info(self,version):
        return self.call('b2_get_file_info',fileId=version)

    def retain(self,key,version,mode,until):
        if 'writeFileRetentions' not in self.capabilities:raise AssetError('retention extension requires writeFileRetentions')
        return self.call('b2_update_file_retention',fileName=key,fileId=version,fileRetention={'mode':mode,'retainUntilTimestamp':until})

    def verify(self,key,raw,media_type):
        from .s3 import response
        records=[v for v in self.versions() if v['fileName']==key]
        if not records or records[0]['action']!='upload':raise AssetError('no visible version for storage readback')
        version=records[0]['fileId']
        with response(self.s3_endpoint,self.bucket_name,key,version,self.key_id,self.key,method='GET') as received:
            content=b''.join(response_chunks(received,MAX_BYTES))
            verify_bytes(content,key.rsplit('.',1)[1],received.getheader('Content-Type',''))
            if received.getheader('x-amz-version-id')!=version or content!=raw:raise AssetError('B2 exact-version readback differs from original bytes')

    def upload_file(self,key,path,media_type,*,part_size=32*1024*1024):
        path=Path(path)
        data=self.call('b2_start_large_file',bucketId=self.bucket_id,fileName=key,contentType=media_type)
        version=data['fileId'];checksums=[]
        try:
            with path.open('rb') as stream:
                number=0
                while raw:=stream.read(part_size):
                    number+=1
                    if number>10000:raise AssetError('too many multipart upload parts')
                    endpoint=self.call('b2_get_upload_part_url',fileId=version)
                    digest=hashlib.sha1(raw).hexdigest()
                    result=request(endpoint['uploadUrl'],endpoint['authorizationToken'],body=raw,headers={
                        'Content-Length':str(len(raw)),'X-Bz-Part-Number':str(number),'X-Bz-Content-Sha1':digest},operation='multipart upload')
                    if result.get('fileId')!=version or result.get('partNumber')!=number or result.get('contentLength')!=len(raw) or result.get('contentSha1')!=digest:
                        raise AssetError('multipart upload receipt differs')
                    checksums.append(digest)
            return self.call('b2_finish_large_file',fileId=version,partSha1Array=checksums)
        except Exception:
            self.call('b2_cancel_large_file',fileId=version)
            raise


    def delete(self, item):
        result = self.call("b2_delete_file_version", fileName=item["fileName"], fileId=item["fileId"])
        if result.get("fileName") != item["fileName"] or result.get("fileId") != item["fileId"]:
            raise AssetError("B2 deletion receipt differs from the planned version")
