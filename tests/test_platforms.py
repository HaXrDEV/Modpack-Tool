from modpack_tool import platforms


def test_murmur2_known_answers():
    # Values from the original implementation (same as CurseForge and mmc-export).
    assert platforms.murmur2(b"hello world") == 2824650221
    assert platforms.murmur2(b"The quick brown fox\njumps over\tthe lazy dog") == 3751777527
    assert platforms.murmur2(b"") == 1540447798


def test_whitespace_is_ignored():
    assert platforms.murmur2(b"a b\tc\r\nd") == platforms.murmur2(b"abcd")


def test_allowed_channels():
    assert platforms.allowed_channels("release") == {"release", "beta"}
    assert platforms.allowed_channels("alpha") == {"release", "beta", "alpha"}
