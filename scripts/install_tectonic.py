"""Install a checksum-pinned official compiler locally, without sudo."""
import hashlib
import io
import json
import platform
import tarfile
from pathlib import Path

import httpx

VERSION = "0.17.0"
URL = "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz"
SHA256 = "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7"


def main():
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise SystemExit("This pinned installer supports Linux x86_64. Install a verified compiler for your platform and set WORKAI_TECTONIC.")
    response = httpx.get(URL, follow_redirects=True, timeout=120)
    response.raise_for_status()
    archive = response.content
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise SystemExit("Official compiler archive checksum mismatch")
    root = Path(__file__).resolve().parent.parent
    directory = root / ".tools"
    directory.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        members = [member for member in bundle.getmembers() if member.isfile() and Path(member.name).name == "tectonic"]
        if len(members) != 1:
            raise SystemExit("Compiler archive layout changed")
        content = bundle.extractfile(members[0]).read()
    executable = directory / "tectonic"
    executable.write_bytes(content)
    executable.chmod(0o700)
    (directory / "tectonic-release.json").write_text(json.dumps({"release": VERSION, "url": URL, "digest": "sha256:" + SHA256}, indent=2) + "\n")
    print(f"Installed verified Tectonic {VERSION} at {executable}")


if __name__ == "__main__":
    main()
