from touchstone.security.rate_limit import RateLimiter


def test_allows_up_to_burst():
    rl = RateLimiter(per_minute=60)
    # Bucket starts full at 60.
    for _ in range(60):
        assert rl.allow("k") is True
    assert rl.allow("k") is False


def test_independent_keys():
    rl = RateLimiter(per_minute=2)
    assert rl.allow("a")
    assert rl.allow("a")
    assert rl.allow("a") is False
    assert rl.allow("b")  # different key still has budget
