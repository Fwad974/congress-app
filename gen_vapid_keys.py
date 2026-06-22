"""
Generate a VAPID key pair for Web Push and print the .env lines to add.

Usage:
    python gen_vapid_keys.py           # friendly output to paste into .env
    python gen_vapid_keys.py --bare    # only the two key lines (for sourcing)

Keep VAPID_PRIVATE_KEY secret — never commit it. Until both keys are set, the
app falls back to the in-app notification feed and web push stays disabled.

In Docker this runs automatically on first boot (see entrypoint.sh): keys are
generated once and persisted to a volume so the public key stays stable across
restarts.
"""
import base64
import sys

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> None:
    bare = "--bare" in sys.argv or "--env" in sys.argv

    v = Vapid01()
    v.generate_keys()

    public_point = v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private_raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")

    if not bare:
        print("# Add these to your .env (keep the private key secret):")
    print(f"VAPID_PUBLIC_KEY={_b64url(public_point)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(private_raw)}")
    if not bare:
        print("VAPID_SUBJECT=mailto:you@example.com")


if __name__ == "__main__":
    main()
