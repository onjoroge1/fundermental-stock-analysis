"""Build-time only: fixed npm release, integrity-checked archive, self-hosted JS.

No extraction of arbitrary paths, no install scripts, no runtime CDN request.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import Request, urlopen

VERSION = "5.0.9"
ARCHIVE = f"https://registry.npmjs.org/lightweight-charts/-/lightweight-charts-{VERSION}.tgz"
MAX_BYTES = 8_000_000


def get(url):
    with urlopen(Request(url, headers={"User-Agent": "StockMachine-build/1.0"}), timeout=30) as response:
        if response.geturl() != url:
            raise ValueError("ASSET_REDIRECT_REJECTED")
        data = response.read(MAX_BYTES+1)
    if len(data) > MAX_BYTES:
        raise ValueError("ASSET_TOO_LARGE")
    return data


def unpack(metadata, archive):
    if metadata.get("name") != "lightweight-charts" or metadata.get("version") != VERSION:
        raise ValueError("ASSET_IDENTITY_MISMATCH")
    if metadata.get("dist", {}).get("tarball") != ARCHIVE:
        raise ValueError("ASSET_SOURCE_MISMATCH")
    actual = "sha512-"+base64.b64encode(hashlib.sha512(archive).digest()).decode()
    if metadata["dist"].get("integrity") != actual:
        raise ValueError("ASSET_INTEGRITY_MISMATCH")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        output = {}
        for source, destination in [("package/dist/lightweight-charts.standalone.production.js", f"lightweight-charts-{VERSION}.js"),
                                    ("package/LICENSE", "lightweight-charts-LICENSE.txt")]:
            member = tar.getmember(source)
            if not member.isfile() or member.size > MAX_BYTES:
                raise ValueError("ASSET_ENTRY_INVALID")
            output[destination] = tar.extractfile(member).read()
    return output


def main():
    metadata = json.loads(get(f"https://registry.npmjs.org/lightweight-charts/{VERSION}"))
    files = unpack(metadata, get(ARCHIVE))
    dest = Path(__file__).resolve().parents[1]/"webui"/"vendor"
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (dest/name).write_bytes(content)
    manifest = {"name": "lightweight-charts", "version": VERSION, "integrity": metadata["dist"]["integrity"],
                "source": ARCHIVE, "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    (dest/"lightweight-charts-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
