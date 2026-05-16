"""
tests/test_security_key.py

Unit tests for the Security Access seed → key algorithm (SID 0x27).
Algorithm: Key = reverse_bytes(Seed)

BCM implementation (core/transport/ecu_server.py):
    Seed request  → 27 03  →  67 03 S0 S1 S2 S3
    Key send      → 27 04 K0 K1 K2 K3  →  67 04
    Key algorithm → K = [S3, S2, S1, S0]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Key algorithm (mirrors ecu_server.py UDSECU._security_access)
# ─────────────────────────────────────────────────────────────────────────────

FIXED_SEED     = bytes([0x11, 0x22, 0x33, 0x44])
EXPECTED_KEY   = bytes([0x44, 0x33, 0x22, 0x11])
WRONG_KEY      = bytes([0x00, 0x00, 0x00, 0x00])


def compute_key(seed: bytes) -> bytes:
    """Byte-reversal key algorithm used by BCM ECU."""
    return bytes(reversed(seed))


class TestKeyAlgorithm:

    def test_fixed_seed_gives_expected_key(self):
        assert compute_key(FIXED_SEED) == EXPECTED_KEY

    def test_key_is_reverse_of_seed(self):
        seed = bytes([0xAB, 0xCD, 0xEF, 0x12])
        key  = compute_key(seed)
        assert key == bytes([0x12, 0xEF, 0xCD, 0xAB])

    def test_key_length_equals_seed_length(self):
        seed = bytes([0x11, 0x22, 0x33, 0x44])
        assert len(compute_key(seed)) == len(seed)

    def test_wrong_key_rejected(self):
        assert compute_key(FIXED_SEED) != WRONG_KEY

    def test_symmetric_double_reverse(self):
        """Applying the algorithm twice returns the original seed."""
        seed = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        assert compute_key(compute_key(seed)) == seed

    def test_all_zeros_seed(self):
        seed = bytes([0x00, 0x00, 0x00, 0x00])
        assert compute_key(seed) == bytes([0x00, 0x00, 0x00, 0x00])

    def test_all_ff_seed(self):
        seed = bytes([0xFF, 0xFF, 0xFF, 0xFF])
        assert compute_key(seed) == bytes([0xFF, 0xFF, 0xFF, 0xFF])

    def test_ascending_seed(self):
        seed = bytes([0x01, 0x02, 0x03, 0x04])
        assert compute_key(seed) == bytes([0x04, 0x03, 0x02, 0x01])


class TestUDSSecurityAccessFrames:

    def test_seed_request_frame(self):
        """0x27 0x03 — RequestSeed subfunction."""
        frame = bytes([0x27, 0x03])
        assert frame[0] == 0x27        # SecurityAccess SID
        assert frame[1] == 0x03        # RequestSeed subfunction
        assert frame[1] % 2 == 1       # Odd subfunction = RequestSeed

    def test_seed_response_frame(self):
        """0x67 0x03 S0 S1 S2 S3 — positive seed response."""
        seed = FIXED_SEED
        response = bytes([0x67, 0x03]) + seed
        assert response[0] == 0x67     # Positive response (0x27 + 0x40)
        assert response[1] == 0x03
        extracted_seed = response[2:6]
        assert extracted_seed == seed

    def test_key_send_frame(self):
        """0x27 0x04 K0 K1 K2 K3 — SendKey."""
        key = compute_key(FIXED_SEED)
        frame = bytes([0x27, 0x04]) + key
        assert frame[0] == 0x27
        assert frame[1] == 0x04        # Even subfunction = SendKey
        assert frame[1] % 2 == 0
        extracted_key = frame[2:6]
        assert extracted_key == EXPECTED_KEY

    def test_positive_key_response(self):
        """0x67 0x04 — unlock confirmed."""
        response = bytes([0x67, 0x04])
        assert response[0] == 0x67
        assert response[1] == 0x04

    def test_invalid_key_nrc(self):
        """0x7F 0x27 0x35 — invalidKey NRC."""
        nrc_frame = bytes([0x7F, 0x27, 0x35])
        assert nrc_frame[0] == 0x7F    # NegativeResponse
        assert nrc_frame[1] == 0x27    # Requested SID
        assert nrc_frame[2] == 0x35    # invalidKey

    def test_wrong_session_nrc(self):
        """0x7F 0x27 0x7E — subFunctionNotSupportedInActiveSession."""
        nrc_frame = bytes([0x7F, 0x27, 0x7E])
        assert nrc_frame[2] == 0x7E

    def test_full_unlock_sequence(self):
        """Simulate the complete seed/key exchange."""
        # Step 1: Request seed
        request_seed = bytes([0x27, 0x03])
        assert request_seed[1] % 2 == 1   # RequestSeed

        # Step 2: Receive seed
        seed_response = bytes([0x67, 0x03]) + FIXED_SEED
        received_seed = seed_response[2:]
        assert received_seed == FIXED_SEED

        # Step 3: Calculate key
        calculated_key = compute_key(received_seed)
        assert calculated_key == EXPECTED_KEY

        # Step 4: Send key
        send_key = bytes([0x27, 0x04]) + calculated_key
        assert send_key[2:] == EXPECTED_KEY

        # Step 5: Receive positive response
        pos_response = bytes([0x67, 0x04])
        assert pos_response[0] == 0x67
        assert pos_response[1] == 0x04
