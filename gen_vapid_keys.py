"""
Generate a VAPID key pair for Web Push and print the .env lines to add.

Usage:
    python gen_vapid_keys.py

Paste the output into your .env (keep VAPID_PRIVATE_KEY secret — never commit
it). Until both keys are set, the app falls back to the in-app notification feed
and web push stays disabled.
"""
import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main() -> None:
    v = Vapid01()
    v.generate_keys()

    public_point = v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private_raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")

    print("# Add these to your .env (keep the private key secret):")
    print(f"VAPID_PUBLIC_KEY={_b64url(public_point)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(private_raw)}")
    print("VAPID_SUBJECT=mailto:you@example.com")


if __name__ == "__main__":
    main()
