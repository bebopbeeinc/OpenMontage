"""Minimal S3 publisher for the trivia-images pipeline.

Mirrors the shape of `tools/publishers/google_drive.py`: a lazy process-wide
singleton client (`get_client()`) plus a small `upload_bytes` helper. Kept
deliberately thin — the trivia-images web server (scripts/trivia_images/web/
server.py) is the only consumer today, pushing web-optimized JPEGs of approved
images to `assets.tt.bebopbee.com/trivia/<country>/`.

Credential resolution (boto3's default chain, best-in-order):
  1. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (+ optional `AWS_SESSION_TOKEN`)
     or `AWS_PROFILE` in the environment. We `load_dotenv()` the repo `.env`
     first, so a dedicated **service** key can live there alongside the other
     OpenMontage secrets (FAL_KEY, etc.) — see `.env.example`. This is the
     recommended way to run it as a service: it decouples from whichever
     interactive user happens to own `~/.aws`.
  2. The shared `~/.aws/credentials` `[default]` profile — what a dev Mac has.
  3. An EC2/ECS instance role — what a real cloud server should use (zero config;
     attach a role granting `s3:PutObject` on the bucket).

The bucket lives in `us-west-2` and serves its objects publicly over HTTPS, so
uploads are made `public-read` to match the existing objects (which carry an
AllUsers READ grant); the bucket has object ACLs enabled.
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Optional

# The bucket that fronts assets.tt.bebopbee.com and the region it lives in.
DEFAULT_REGION = "us-west-2"
_REPO = Path(__file__).resolve().parents[2]


class S3Publisher:
    """Thin wrapper over a boto3 S3 client with a public-read upload helper."""

    def __init__(self, region: str = DEFAULT_REGION) -> None:
        # Load repo .env BEFORE building the client so AWS_* service creds placed
        # there are on os.environ when boto3 reads its credential chain. This
        # server never calls load_dotenv() at startup, so without this a key in
        # .env would be silently ignored. Best-effort; falls through to ~/.aws
        # or an instance role when .env has no AWS_* keys.
        try:
            from dotenv import load_dotenv

            load_dotenv(_REPO / ".env")
        except Exception:
            pass

        import boto3  # imported lazily so the module is cheap to import

        # Dotted bucket names (assets.tt.bebopbee.com) can't be virtual-hosted
        # over TLS, so force path-style addressing — same thing the AWS CLI does.
        from botocore.config import Config

        self._client = boto3.client(
            "s3",
            region_name=region,
            config=Config(s3={"addressing_style": "path"}),
        )

    def upload_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        *,
        content_type: str,
        public: bool = True,
        cache_control: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Put `data` at `s3://bucket/key` and return the object's public URL.

        `public=True` sets a `public-read` ACL so the object is fetchable over
        HTTPS the moment it lands (matching the existing trivia assets).
        `metadata` becomes user metadata (`x-amz-meta-*`) on the object — used
        to stamp the source provenance (Drive file id + mtime) so a later sync
        can skip re-uploading an unchanged image (see head_metadata).
        """
        extra: dict = {"ContentType": content_type}
        if public:
            extra["ACL"] = "public-read"
        if cache_control:
            extra["CacheControl"] = cache_control
        if metadata:
            extra["Metadata"] = metadata
        self._client.put_object(Bucket=bucket, Key=key, Body=data, **extra)
        return f"https://{bucket}/{key}"

    def head_metadata(self, bucket: str, key: str) -> Optional[dict[str, str]]:
        """User metadata (`x-amz-meta-*`) on `s3://bucket/key`, or None.

        Returns the metadata dict (keys lowercased, prefix stripped) for an
        existing object, or None when it's safe to treat the object as "needs
        upload": the key doesn't exist yet (404), or we lack read permission to
        check (AccessDenied) — in which case the caller should just upload
        rather than skip. Any other error propagates.
        """
        from botocore.exceptions import ClientError

        try:
            resp = self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound", "403", "AccessDenied"):
                return None
            raise
        return resp.get("Metadata") or {}


# Module-level singleton — lazy so importing this file is cheap and doesn't
# build a client or touch credentials until something actually uploads.
_singleton: Optional[S3Publisher] = None
_singleton_lock = Lock()


def get_client() -> S3Publisher:
    """Get the process-wide S3Publisher singleton (built on first call)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = S3Publisher()
        return _singleton
