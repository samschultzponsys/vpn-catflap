import base64

from catflap import keys

# RFC 7748 section 6.1 test vector (Alice)
ALICE_PRIV = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
ALICE_PUB = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")


def test_pure_python_x25519_matches_rfc7748():
    assert keys._x25519(ALICE_PRIV, keys._BASEPOINT) == ALICE_PUB


def test_public_from_private():
    assert keys.public_from_private(base64.b64encode(ALICE_PRIV).decode()) == \
        base64.b64encode(ALICE_PUB).decode()


def test_generate_keypair_consistent():
    priv, pub = keys.generate_keypair()
    assert keys.is_valid_key(priv) and keys.is_valid_key(pub)
    assert keys.public_from_private(priv) == pub
    assert keys._x25519(base64.b64decode(priv), keys._BASEPOINT) == base64.b64decode(pub)


def test_invalid_key():
    assert not keys.is_valid_key("nope")
    assert not keys.is_valid_key(base64.b64encode(b"short").decode())
