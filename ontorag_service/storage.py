from pathlib import Path

from .config import ROOT, STORAGE_BACKEND


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    if STORAGE_BACKEND == "blob":
        from vercel.blob.sync import put_blob

        result = put_blob(key, data, access="private", content_type=content_type)
        return result.pathname
    path = ROOT / "data" / "objects" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def get_bytes(key: str) -> bytes:
    if STORAGE_BACKEND == "blob":
        from vercel.blob.sync import get_blob

        result = get_blob(key, access="private")
        if result is None:
            raise FileNotFoundError(key)
        return result.body
    return (ROOT / "data" / "objects" / key).read_bytes()


def delete_bytes(keys: list[str]) -> None:
    if STORAGE_BACKEND == "blob":
        from vercel.blob.sync import delete_blob

        if keys:
            delete_blob(keys)
        return
    for key in keys:
        (ROOT / "data" / "objects" / key).unlink(missing_ok=True)
