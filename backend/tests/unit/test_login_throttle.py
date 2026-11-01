from ontobot.security.auth import MAX_CLIENT_FAILURES, MAX_FAILURES, LoginThrottle


def test_one_username_is_locked_after_max_failures() -> None:
    t = LoginThrottle()
    for _ in range(MAX_FAILURES):
        assert not t.locked("kim.user", "10.0.0.1")
        t.failed("kim.user", "10.0.0.1")
    assert t.locked("KIM.USER", "10.0.0.2")  # 아이디는 대소문자·주소와 무관하게 막힌다
    assert not t.locked("lee.user", "10.0.0.1")  # 같은 주소의 다른 사람은 아직 막히지 않는다


def test_spraying_many_usernames_from_one_address_locks_that_address() -> None:
    t = LoginThrottle()
    for i in range(MAX_CLIENT_FAILURES):
        t.failed(f"user{i}", "10.0.0.9")
    assert t.locked("someone.else", "10.0.0.9")
    assert not t.locked("someone.else", "10.0.0.8")


def test_success_clears_the_username_but_not_the_address() -> None:
    t = LoginThrottle()
    for i in range(MAX_CLIENT_FAILURES - 1):
        t.failed(f"user{i}", "10.0.0.9")
    for _ in range(MAX_FAILURES - 1):
        t.failed("me", "10.0.0.9")
    t.succeeded("me", "10.0.0.9")
    t.failed("me", "10.0.0.9")
    assert t.locked("anyone", "10.0.0.9")  # 제 계정 로그인으로 주소의 셈을 비울 수 없다
    assert not t.locked("me", "10.0.0.1")
