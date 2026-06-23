"""
AES decryption for Impartus HLS segments.

The Impartus server returns a 18-byte response for the AES key:
  bytes 0-1  : header (must be 0x02 0x01)
  bytes 2-17 : the actual 16-byte AES-128 key, REVERSED

Segments are AES-128-CBC encrypted with IV = 16 zero bytes.
Padding is PKCS7 (always at least one block added).

The wrap/unwrap is the same scheme used by the Impartus web player.
"""

from __future__ import annotations


def aes_key_from_response(blob: bytes) -> bytes:
    """Extract the 16-byte AES-128 key from Impartus's 18-byte wrapped response."""
    if len(blob) < 18 or blob[0] != 0x02 or blob[1] != 0x01:
        raise ValueError(f"Unexpected key response: {blob.hex()}")
    return blob[2:][::-1]


def aes_cbc_decrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-CBC with IV = 16 zero bytes; PKCS7 unpads the last block."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    # Always pad to a 16-byte boundary (add a full padding block if already aligned).
    pad_len = 16 - (len(data) % 16)
    if pad_len == 0:
        pad_len = 16
    padded = data + bytes([pad_len]) * pad_len

    dec = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).decryptor()
    out = dec.update(padded) + dec.finalize()

    # Strip PKCS7 padding.
    p = out[-1]
    if 1 <= p <= 16 and out[-p:] == bytes([p]) * p:
        out = out[:-p]
    return out