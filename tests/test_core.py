import importlib.util
import io
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "x_news_alert.py"


def load_module():
    spec = importlib.util.spec_from_file_location("x_news_alert", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



# ── Normalization & routing ────────────────────────────────────

def test_normalizes_x_urls_and_route_targets():
    xna = load_module()

    assert xna.normalize_username("https://x.com/alice/status/123?ref=home") == "alice"
    assert xna.normalize_username("@bob/") == "bob"
    assert xna.normalize_list_id("https://twitter.com/i/lists/987?x=1") == "987"
    assert xna.parse_route(["discord:12345"]) == ("discord", "discord_channel_12345")



# ── Target management ──────────────────────────────────────────

def test_add_blogger_creates_portable_json_config(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)

    username = xna.add_blogger(paths, "https://x.com/alice", ["telegram:-100123"])

    data = json.loads(paths.bloggers.read_text(encoding="utf-8"))
    assert username == "alice"
    assert data["bloggers"] == [
        {
            "username": "alice",
            "display_name": "alice",
            "enabled": True,
            "platform": "telegram",
            "chat_id": "-100123",
        }
    ]



# ── Tweet state — dedup & send log ─────────────────────────────

def test_dedup_skips_read_ids_but_retries_failed_sends(tmp_path):
    xna = load_module()
    target = tmp_path / "state"
    target.mkdir()
    (target / "read-ids.json").write_text(json.dumps(["id1", "id3"]), encoding="utf-8")
    (target / "send-log.json").write_text(
        json.dumps({"tweets": [{"id": "id3", "sent": False}]}),
        encoding="utf-8",
    )
    raw = {
        "data": [
            {"id": "id1", "text": "old"},
            {"id": "id2", "text": "new"},
            {"id": "id3", "text": "retry"},
        ]
    }

    new_tweets = xna.dedup_tweets(target, raw)

    assert [tweet["id"] for tweet in new_tweets] == ["id2", "id3"]


def test_dedup_uses_latest_send_status_for_retry_decisions(tmp_path):
    xna = load_module()
    target = tmp_path / "state"
    target.mkdir()
    (target / "read-ids.json").write_text(json.dumps(["id1"]), encoding="utf-8")
    (target / "send-log.json").write_text(
        json.dumps({"tweets": [{"id": "id1", "sent": False}, {"id": "id1", "sent": True}]}),
        encoding="utf-8",
    )

    new_tweets = xna.dedup_tweets(target, {"data": [{"id": "id1", "text": "sent later"}]})

    assert new_tweets == []



# ── Message building ───────────────────────────────────────────

def test_build_message_blogger_includes_analysis_market_and_url():
    xna = load_module()
    tweet = {"id": "42", "text": "$AAPL new product", "author": {"name": "Alice", "username": "alice"}}
    analysis = {
        "chinese_summary": "AAPL product update",
        "symbols": ["AAPL"],
        "logic_score": 8,
        "sleaze_score": 2,
        "logic_detail": "cites specific delivery numbers",
        "sleaze_detail": "no conflict detected",
        "prediction_check": "watch revenue",
        "trend": "bullish",
    }

    message = xna.build_message_blogger(tweet, analysis, "AAPL close $150", "@alice")

    assert "Alice (@alice)" in message
    assert "8🔵/10" in message
    assert "📈" in message
    assert "AAPL product update" in message
    assert "cites specific delivery numbers" in message
    assert "watch revenue" in message
    assert "AAPL close $150" in message
    assert "$AAPL new product" in message
    assert "x.com/i/web/status/42" in message



# ── Run loop — process & dispatch ──────────────────────────────

def test_run_all_uses_config_default_platform_and_chat_even_when_cron_disabled(tmp_path, monkeypatch):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    config = xna.read_json(paths.config, {})
    config["cron_enabled"] = False
    config["default_platform"] = "telegram"
    config["platforms"]["telegram"]["default_chat_id"] = "-100ok"
    xna.write_json(paths.config, config)
    xna.add_blogger(paths, "https://x.com/alice")
    sends = []

    monkeypatch.setattr(
        xna,
        "fetch_tweets",
        lambda mode, identifier, **kwargs: {"data": [{"id": "t1", "text": "$AAPL news"}]},
    )
    monkeypatch.setattr(xna, "fetch_market", lambda symbols, **kwargs: "")
    monkeypatch.setattr(
        xna,
        "send_message",
        lambda paths_arg, chat_id, message, platform="auto": sends.append((chat_id, platform, message)),
    )

    xna.run_once(paths, "all", "", io.StringIO())

    assert sends
    assert sends[0][0] == "-100ok"
    assert sends[0][1] == "telegram"


def test_run_blogger_accepts_at_prefixed_identifier(tmp_path, monkeypatch):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    xna.add_blogger(paths, "alice")
    processed = []

    def fake_process_target(*args, **kwargs):
        processed.append(args[2])

    monkeypatch.setattr(xna, "process_target", fake_process_target)

    xna.run_once(paths, "blogger", "@alice", io.StringIO())

    assert processed == ["alice"]


def test_run_once_continues_after_one_target_fails(tmp_path, monkeypatch):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    xna.add_blogger(paths, "bad")
    xna.add_blogger(paths, "good")
    processed = []

    def fake_process_target(*args, **kwargs):
        identifier = args[2]
        if identifier == "bad":
            raise xna.AlertError("fetch failed")
        processed.append(identifier)

    monkeypatch.setattr(xna, "process_target", fake_process_target)
    out = io.StringIO()

    xna.run_once(paths, "all", "", out)

    assert processed == ["good"]
    assert "bad: fetch failed" in out.getvalue()


def test_process_target_uses_stable_state_key_not_display_label(tmp_path, monkeypatch):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)

    monkeypatch.setattr(
        xna,
        "fetch_tweets",
        lambda mode, identifier, **kwargs: {"data": [{"id": "t1", "text": "$AAPL news"}]},
    )
    monkeypatch.setattr(xna, "fetch_market", lambda symbols, **kwargs: "")
    monkeypatch.setattr(xna, "send_message", lambda *args, **kwargs: None)

    xna.process_target(paths, "blogger", "alice", "Alice Display", "-100ok", "telegram", "@Fancy", io.StringIO())

    assert (paths.state / "@alice" / "read-ids.json").exists()
    assert not (paths.state / "@Fancy" / "read-ids.json").exists()


def test_process_target_writes_raw_and_analysis_debug_cache(tmp_path, monkeypatch):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    raw = {"data": [{"id": "t1", "text": "$AAPL news"}]}
    analyses = [
        {
            "id": "t1",
            "chinese_summary": "AAPL news",
            "symbols": ["AAPL"],
            "logic_score": 7,
            "sleaze_score": 1,
        }
    ]

    monkeypatch.setattr(xna, "fetch_tweets", lambda mode, identifier, **kwargs: raw)
    monkeypatch.setattr(xna, "analyze_tweets", lambda tweets, config, **kwargs: analyses)
    monkeypatch.setattr(xna, "fetch_market", lambda symbols, **kwargs: "")
    monkeypatch.setattr(xna, "send_message", lambda *args, **kwargs: None)

    xna.process_target(paths, "blogger", "alice", "Alice", "-100ok", "telegram", "@Alice", io.StringIO())

    target_dir = paths.state / "@alice"
    assert json.loads((target_dir / "raw.json").read_text(encoding="utf-8")) == raw
    assert json.loads((target_dir / "analysis-cache.json").read_text(encoding="utf-8")) == analyses



# ── HTTP & delivery ────────────────────────────────────────────

def test_post_json_accepts_empty_success_response(monkeypatch):
    xna = load_module()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b""

    monkeypatch.setattr(xna.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    assert xna.post_json("https://example.test", {"ok": True}, {}, 1) is None


def test_send_with_retry_retries_transient_alert_errors():
    xna = load_module()
    calls = []

    def flaky_send():
        calls.append("try")
        if len(calls) < 3:
            raise xna.AlertError("temporary")

    xna.send_with_retry(flaky_send, "telegram", sleeper=lambda seconds: None)

    assert calls == ["try", "try", "try"]



# ── Fetch & market ─────────────────────────────────────────────

def test_fetch_tweets_refreshes_xurl_token_once_on_auth_error(tmp_path):
    xna = load_module()
    refresh_script = tmp_path / "refresh_x_token.py"
    refresh_script.write_text("print('refresh')\n", encoding="utf-8")
    calls = []

    def runner(args, timeout):
        calls.append(args)
        if args[0] == "xurl" and len([call for call in calls if call[0] == "xurl"]) == 1:
            raise xna.AlertError("xurl: Unauthorized")
        if args[0] == "xurl":
            return '{"data":[{"id":"t1"}]}'
        if args[-1] == str(refresh_script):
            return "refreshed"
        raise AssertionError(args)

    raw = xna.fetch_tweets("list", "123", runner=runner, refresh_script=str(refresh_script))

    assert raw == {"data": [{"id": "t1"}]}
    assert calls[0][0] == "xurl"
    assert calls[1][-1] == str(refresh_script)
    assert calls[2][0] == "xurl"


def test_fetch_tweets_reports_refresh_script_when_auth_error_cannot_refresh(tmp_path):
    xna = load_module()
    missing_script = tmp_path / "missing.py"

    def runner(args, timeout):
        raise xna.AlertError("authentication required")

    try:
        xna.fetch_tweets("list", "123", runner=runner, refresh_script=str(missing_script))
    except xna.AlertError as exc:
        assert "refresh script not found" in str(exc)
    else:
        raise AssertionError("expected AlertError")


def test_fetch_market_includes_five_day_kline(monkeypatch):
    xna = load_module()
    commands = []

    monkeypatch.setattr(xna.shutil, "which", lambda command: "longbridge" if command == "longbridge" else None)

    def runner(args, timeout):
        commands.append(args)
        if args[1] == "quote":
            return json.dumps({"last_done": "150", "change_rate": "1.23", "prev_close": "148"})
        if args[1] == "kline":
            return json.dumps({"data": {"candles": [{"close": 145}, {"close": 146}, {"close": 147}, {"close": 149}, {"close": 150}]}})
        raise AssertionError(args)

    output = xna.fetch_market(["AAPL"], runner=runner)

    assert "AAPL" in output
    assert "150" in output
    assert "145->146->147->149->150" in output
    assert any(command[1] == "kline" for command in commands)



# ── Lock ───────────────────────────────────────────────────────

def test_run_lock_prevents_second_active_runner(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)

    with xna.RunLock(paths.lock) as first:
        with xna.RunLock(paths.lock) as second:
            assert first is True
            assert second is False



# ── Schedule ───────────────────────────────────────────────────

def test_schedule_defaults_are_normalized_from_legacy_config(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    config = xna.read_json(paths.config, {})
    config.pop("schedule", None)
    config["cron_enabled"] = True
    config["cron_schedule"] = "*/15 * * * *"
    config["polling_interval_minutes"] = 15

    schedule = xna.schedule_config(config)

    assert schedule["enabled"] is True
    assert schedule["mode"] == "external"
    assert schedule["cron"] == "*/15 * * * *"
    assert schedule["polling_interval_minutes"] == 15
    assert schedule["timezone"] == "Asia/Shanghai"


def test_schedule_command_points_to_single_run_entrypoint(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)

    command = xna.schedule_command(paths)

    assert "x_news_alert.py" in command
    assert "--base-dir" in command
    assert str(tmp_path) in command
    assert "run all" in command


def test_schedule_doctor_reports_missing_targets_and_default_chat(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)

    report = xna.schedule_doctor(paths)

    assert report["ok"] is False
    assert any(item["check"] == "targets" and item["status"] == "fail" for item in report["checks"])
    assert any(item["check"] == "default_chat" and item["status"] == "fail" for item in report["checks"])


def test_schedule_doctor_passes_with_target_and_default_chat(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    xna.add_blogger(paths, "alice")
    xna.set_platform(paths, "telegram")
    xna.set_default_chat(paths, "telegram", "-100ok")

    report = xna.schedule_doctor(paths, env={"TWITTER_COOKIE": "auth_token=abc; ct0=def"})

    assert report["ok"] is True


def test_schedule_doctor_requires_twitter_cookie_for_bloggers(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    xna.add_blogger(paths, "alice")
    xna.set_platform(paths, "telegram")
    xna.set_default_chat(paths, "telegram", "-100ok")

    report = xna.schedule_doctor(paths, env={})

    assert report["ok"] is False
    assert any(item["check"] == "twitter_cookie" and item["status"] == "fail" for item in report["checks"])


def test_schedule_doctor_requires_xurl_auth_when_lists_are_enabled(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    xna.add_list(paths, "123456789")
    xna.set_platform(paths, "telegram")
    xna.set_default_chat(paths, "telegram", "-100ok")

    report = xna.schedule_doctor(paths, home=tmp_path / "home", env={})

    assert report["ok"] is False
    assert any(item["check"] == "xurl_auth" and item["status"] == "fail" for item in report["checks"])


def test_schedule_doctor_accepts_xurl_auth_file_for_list_targets(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".xurl").write_text(
        "bearer_token = test-token\nclient_id = cid\nclient_secret = secret\naccess_token = access\nrefresh_token = refresh\n",
        encoding="utf-8",
    )
    xna.ensure_files(paths)
    xna.add_list(paths, "123456789")
    xna.set_platform(paths, "telegram")
    xna.set_default_chat(paths, "telegram", "-100ok")

    report = xna.schedule_doctor(paths, home=home, env={})

    assert not any(item["check"] == "xurl_auth" and item["status"] == "fail" for item in report["checks"])
    assert not any(item["check"] == "xurl_client_credentials" and item["status"] == "fail" for item in report["checks"])


def test_schedule_doctor_requires_xurl_client_credentials_for_lists(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".xurl").write_text("bearer_token = test-token\n", encoding="utf-8")
    xna.ensure_files(paths)
    xna.add_list(paths, "123456789")
    xna.set_platform(paths, "telegram")
    xna.set_default_chat(paths, "telegram", "-100ok")

    report = xna.schedule_doctor(paths, home=home, env={})

    assert report["ok"] is False
    assert any(item["check"] == "xurl_client_credentials" and item["status"] == "fail" for item in report["checks"])


def test_schedule_enable_and_disable_update_nested_and_legacy_flags(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)

    xna.set_schedule(paths, enabled=True, mode="poll", cron="*/10 * * * *", interval=10)
    config = xna.read_json(paths.config, {})
    assert config["schedule"]["enabled"] is True
    assert config["schedule"]["mode"] == "poll"
    assert config["schedule"]["cron"] == "*/10 * * * *"
    assert config["schedule"]["polling_interval_minutes"] == 10
    assert config["cron_enabled"] is True
    assert config["polling_interval_minutes"] == 10

    xna.set_schedule(paths, enabled=False)
    config = xna.read_json(paths.config, {})
    assert config["schedule"]["enabled"] is False
    assert config["cron_enabled"] is False


def test_schedule_cli_show_and_doctor(tmp_path):
    xna = load_module()
    stdout = io.StringIO()
    stderr = io.StringIO()

    show_code = xna.main(["--base-dir", str(tmp_path), "schedule", "show"], stdout=stdout, stderr=stderr)
    doctor_out = io.StringIO()
    doctor_code = xna.main(["--base-dir", str(tmp_path), "schedule", "doctor"], stdout=doctor_out, stderr=stderr)

    assert show_code == 0
    assert json.loads(stdout.getvalue())["mode"] == "external"
    assert doctor_code == 1
    assert json.loads(doctor_out.getvalue())["ok"] is False



# ── Auth & init ────────────────────────────────────────────────

def test_cli_set_default_chat_accepts_negative_telegram_chat_id(tmp_path):
    xna = load_module()
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = xna.main(
        ["--base-dir", str(tmp_path), "set-default-chat", "telegram", "-100123456789"],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    config = json.loads((tmp_path / "alert-config.json").read_text(encoding="utf-8"))
    assert config["platforms"]["telegram"]["default_chat_id"] == "-100123456789"


def test_cli_status_initializes_files_and_returns_zero(tmp_path):
    xna = load_module()
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = xna.main(["--base-dir", str(tmp_path), "status"], stdout=stdout, stderr=stderr)

    assert code == 0
    assert json.loads((tmp_path / "alert-config.json").read_text(encoding="utf-8"))[
        "default_platform"
    ] == "feishu"
    assert "Config" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_init_command_writes_config_target_and_schedule(tmp_path):
    xna = load_module()
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = xna.main(
        [
            "--base-dir",
            str(tmp_path),
            "init",
            "--platform",
            "telegram",
            "--chat-id=-100123456789",
            "--llm-provider",
            "deepseek",
            "--llm-api-base",
            "https://api.deepseek.com/v1",
            "--llm-api-key-env",
            "DEEPSEEK_API_KEY",
            "--llm-model",
            "deepseek-chat",
            "--twitter-cookie",
            "auth_token=abc; ct0=def",
            "--blogger",
            "https://x.com/alice",
            "--enable-schedule",
            "--schedule-mode",
            "poll",
            "--interval",
            "15",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    config = json.loads((tmp_path / "alert-config.json").read_text(encoding="utf-8"))
    bloggers = json.loads((tmp_path / "alert-bloggers.json").read_text(encoding="utf-8"))
    assert config["default_platform"] == "telegram"
    assert config["platforms"]["telegram"]["default_chat_id"] == "-100123456789"
    assert config["llm_provider"] == "deepseek"
    assert config["llm_api_base"] == "https://api.deepseek.com/v1"
    assert config["llm_api_key_env"] == "DEEPSEEK_API_KEY"
    assert config["llm_model"] == "deepseek-chat"
    assert config["twitter_cli"]["cookie_provided"] is True
    assert config["schedule"]["enabled"] is True
    assert config["schedule"]["mode"] == "poll"
    assert config["schedule"]["polling_interval_minutes"] == 15
    assert bloggers["bloggers"][0]["username"] == "alice"
    assert "initialized" in stdout.getvalue()


def test_init_requires_twitter_cookie(tmp_path):
    xna = load_module()
    stderr = io.StringIO()

    code = xna.main(
        ["--base-dir", str(tmp_path), "init", "--platform", "telegram", "--chat-id=-100123456789"],
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 1
    assert "twitter cookie" in stderr.getvalue().lower()


def test_init_requires_xurl_client_credentials_when_list_is_configured(tmp_path):
    xna = load_module()
    stderr = io.StringIO()

    code = xna.main(
        [
            "--base-dir",
            str(tmp_path),
            "init",
            "--platform",
            "telegram",
            "--chat-id=-100123456789",
            "--twitter-cookie",
            "auth_token=abc; ct0=def",
            "--list",
            "123456789",
        ],
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 1
    assert "xurl client id" in stderr.getvalue().lower()


def test_init_records_xurl_credentials_without_storing_secret_values(tmp_path):
    xna = load_module()
    stdout = io.StringIO()

    code = xna.main(
        [
            "--base-dir",
            str(tmp_path),
            "init",
            "--platform",
            "telegram",
            "--chat-id=-100123456789",
            "--twitter-cookie",
            "auth_token=abc; ct0=def",
            "--list",
            "123456789",
            "--xurl-client-id",
            "client-id-secret-value",
            "--xurl-client-secret",
            "client-secret-value",
            "--xurl-access-token",
            "access-token-value",
            "--xurl-refresh-token",
            "refresh-token-value",
        ],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    config_text = (tmp_path / "alert-config.json").read_text(encoding="utf-8")
    config = json.loads(config_text)
    assert config["xurl_auth"]["client_id_provided"] is True
    assert config["xurl_auth"]["client_secret_provided"] is True
    assert config["xurl_auth"]["access_token_provided"] is True
    assert config["xurl_auth"]["refresh_token_provided"] is True
    assert "client-secret-value" not in config_text
    assert "refresh-token-value" not in config_text


def test_configure_twitter_cookie_runs_twitter_cli_when_available():
    xna = load_module()
    config = {}
    calls = []

    xna.configure_twitter_cookie(
        config,
        cookie="auth_token=abc; ct0=def",
        cookie_env="TWITTER_COOKIE",
        runner=lambda args, timeout: calls.append(args) or "",
        command_exists=lambda command: command == "twitter",
    )

    assert calls == [["twitter", "auth", "set", "cookie", "--value", "auth_token=abc; ct0=def"]]
    assert config["twitter_cli"]["cookie_configured"] is True
    assert config["twitter_cli"]["cookie_provided"] is True
    assert "auth_token" not in json.dumps(config)


def test_interactive_init_wizard_writes_config(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    answers = iter(
        [
            "kimi",
            "https://api.moonshot.cn/v1",
            "KIMI_API_KEY",
            "moonshot-v1-8k",
            "telegram",
            "-100123456789",
            "auth_token=abc; ct0=def",
            "",
            "",
            "",
            "",
            "https://x.com/alice",
            "y",
        ]
    )

    xna.run_init_wizard(paths, input_func=lambda prompt: next(answers), out=io.StringIO())

    config = json.loads(paths.config.read_text(encoding="utf-8"))
    bloggers = json.loads(paths.bloggers.read_text(encoding="utf-8"))
    assert config["llm_provider"] == "kimi"
    assert config["default_platform"] == "telegram"
    assert config["platforms"]["telegram"]["default_chat_id"] == "-100123456789"
    assert config["schedule"]["enabled"] is True
    assert bloggers["bloggers"][0]["username"] == "alice"



# ── CLI compatibility & launchers ──────────────────────────────

def test_cli_compatibility_subcommands_for_old_script_wrappers(tmp_path, monkeypatch):
    xna = load_module()
    xna.ensure_files(xna.AlertPaths(tmp_path))
    monkeypatch.setattr(xna, "fetch_tweets", lambda mode, identifier, **kwargs: {"data": [{"id": "t1"}]})
    monkeypatch.setattr(xna, "fetch_market", lambda symbols: "AAPL close $150")
    monkeypatch.setattr(xna, "send_message", lambda *args, **kwargs: None)

    fetch_out = io.StringIO()
    market_out = io.StringIO()
    send_out = io.StringIO()

    assert xna.main(["--base-dir", str(tmp_path), "fetch", "blogger", "alice"], stdout=fetch_out, stderr=io.StringIO()) == 0
    assert xna.main(["--base-dir", str(tmp_path), "market", "AAPL"], stdout=market_out, stderr=io.StringIO()) == 0
    assert xna.main(["--base-dir", str(tmp_path), "send", "-100ok", "hello", "telegram"], stdout=send_out, stderr=io.StringIO()) == 0

    assert json.loads(fetch_out.getvalue()) == {"data": [{"id": "t1"}]}
    assert "AAPL close $150" in market_out.getvalue()
    assert "sent" in send_out.getvalue()


def test_v3_scripts_dir_contains_only_main_entry():
    script_dir = MODULE_PATH.parent

    actual = {p.name for p in script_dir.iterdir() if p.is_file() and not p.name.startswith("alert")}
    assert actual == {"x_news_alert.py", "config_loader.py", "xurl-token-check.py"}, f"unexpected files in scripts/: {actual - {'x_news_alert.py', 'config_loader.py', 'xurl-token-check.py'}}"



# ── format_time_ago ────────────────────────────────────────────

def test_format_time_ago_returns_correct_units():
    import time as _time
    import calendar as _cal
    xna = load_module()
    now_epoch = _cal.timegm(_time.gmtime())

    def iso(offset_seconds):
        return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now_epoch - offset_seconds))

    assert "秒前" in xna.format_time_ago(iso(30))
    assert "分钟前" in xna.format_time_ago(iso(300))
    assert "小时前" in xna.format_time_ago(iso(7200))
    assert "天前" in xna.format_time_ago(iso(90000))


def test_format_time_ago_returns_empty_for_future_or_invalid():
    import time as _time
    import calendar as _cal
    xna = load_module()
    now_epoch = _cal.timegm(_time.gmtime())
    future = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now_epoch + 3600))
    assert xna.format_time_ago(future) == ""
    assert xna.format_time_ago("") == ""
    assert xna.format_time_ago("not-a-date") == ""



# ── Message builders — blogger & list ──────────────────────────

def test_build_message_list_entry_compact_format():
    xna = load_module()
    tweet = {
        "id": "99",
        "text": "GPU supply is tight this quarter.",
        "author": {"name": "Herman Jin", "username": "ShanghaoJin"},
        "created_at": "2020-01-01T00:00:00Z",
    }
    analysis = {"chinese_summary": "Herman Jin分析GPU供应紧张"}

    entry = xna.build_message_list_entry(tweet, analysis)

    assert "Herman Jin (@ShanghaoJin)" in entry
    assert "Herman Jin分析GPU供应紧张" in entry
    assert "GPU supply is tight" in entry
    assert "x.com/i/web/status/99" in entry
    assert "观点可信度" not in entry
    assert "🔍" not in entry



# ── Digest pagination ──────────────────────────────────────────

def test_split_digest_paginates_by_limit():
    xna = load_module()
    entries = ["A" * 100, "B" * 100, "C" * 100]

    chunks_no_limit = xna._split_digest(entries, 0)
    assert len(chunks_no_limit) == 1

    chunks_small = xna._split_digest(entries, 150)
    assert all(len(c) <= 150 for c in chunks_small)
    assert len(chunks_small) == 3

    chunks_medium = xna._split_digest(entries, 250)
    assert len(chunks_medium) == 2



# ── List mode integration ──────────────────────────────────────

def test_process_target_list_mode_sends_digest_and_marks_all_read(tmp_path, monkeypatch):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    raw = {"data": [{"id": "L1", "text": "tweet one"}, {"id": "L2", "text": "tweet two"}]}
    sends = []

    monkeypatch.setattr(xna, "fetch_tweets", lambda mode, identifier, **kw: raw)
    monkeypatch.setattr(xna, "analyze_tweets", lambda tweets, config, **kw: [
        {"id": t["id"], "chinese_summary": f"summary {t['id']}", "symbols": []} for t in tweets
    ])
    monkeypatch.setattr(xna, "send_message", lambda p, chat, msg, platform="auto": sends.append(msg))

    xna.process_target(paths, "list", "123", "MyList", "-100ok", "telegram", "List MyList", io.StringIO())

    assert len(sends) == 1
    assert "summary L1" in sends[0]
    assert "summary L2" in sends[0]
    assert (paths.state / "list-123" / "read-ids.json").exists()
    ids = json.loads((paths.state / "list-123" / "read-ids.json").read_text(encoding="utf-8"))
    assert "L1" in ids and "L2" in ids



# ── LLM analysis — warn & coerce ───────────────────────────────

def test_analyze_tweets_warns_on_llm_chunk_failure(monkeypatch):
    xna = load_module()
    config = {"llm_api_key_env": "FAKE_KEY", "llm_api_key": "sk-fake"}
    warnings = []

    monkeypatch.setattr(xna, "post_json", lambda *a, **kw: (_ for _ in ()).throw(xna.AlertError("rate limited")))

    tweets = [{"id": "t1", "text": "hello", "author": {}}]
    results = xna.analyze_tweets(tweets, config, warn=lambda msg: warnings.append(msg))

    assert len(results) == 1
    assert results[0]["id"] == "t1"
    assert len(warnings) == 1
    assert "rate limited" in warnings[0]


def test_coerce_analysis_fixes_type_mismatches():
    xna = load_module()

    result = xna._coerce_analysis(
        {"id": "t1", "chinese_summary": "ok", "symbols": None, "logic_score": "7", "sleaze_score": 11},
        "t1",
    )

    assert result["symbols"] == []
    assert result["logic_score"] == 7
    assert result["sleaze_score"] == 10
    assert result["sourcing_note"] == ""


# ── Routing fallback ───────────────────────────────────────────

def test_resolve_target_platform_falls_back_to_default_when_auto_and_empty(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    xna.set_platform(paths, "telegram")
    config = xna.read_json(paths.config, {})

    resolved = xna.resolve_target_platform(config, chat_id="", platform="auto")

    assert resolved == "telegram"


def test_resolve_target_platform_uses_chat_id_prefix_when_platform_auto(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    config = xna.read_json(paths.config, {})

    assert xna.resolve_target_platform(config, chat_id="-100123", platform="auto") == "telegram"
    assert xna.resolve_target_platform(config, chat_id="oc_abc", platform="auto") == "feishu"


# ── List mode — send failure ───────────────────────────────────

def test_process_target_list_mode_send_failure_marks_all_tweets_failed(tmp_path, monkeypatch):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    raw = {"data": [{"id": "F1", "text": "a"}, {"id": "F2", "text": "b"}]}

    monkeypatch.setattr(xna, "fetch_tweets", lambda mode, identifier, **kw: raw)
    monkeypatch.setattr(xna, "analyze_tweets", lambda tweets, config, **kw: [
        {"id": t["id"], "chinese_summary": "", "symbols": []} for t in tweets
    ])
    monkeypatch.setattr(
        xna, "send_message",
        lambda p, chat, msg, platform="auto": (_ for _ in ()).throw(xna.AlertError("network error")),
    )

    out = io.StringIO()
    xna.process_target(paths, "list", "123", "L", "-100ok", "telegram", "List L", out)

    assert "send failed" in out.getvalue()
    assert not (paths.state / "list-123" / "read-ids.json").exists()
    log = json.loads((paths.state / "list-123" / "send-log.json").read_text(encoding="utf-8"))
    failed_ids = {e["id"] for e in log["tweets"] if e["sent"] is False}
    assert "F1" in failed_ids and "F2" in failed_ids


# ── reset-state command ────────────────────────────────────────

def test_reset_state_removes_read_ids_file(tmp_path):
    xna = load_module()
    paths = xna.AlertPaths(tmp_path)
    xna.ensure_files(paths)
    xna.add_blogger(paths, "alice")
    state_dir = paths.state / "@alice"
    state_dir.mkdir(parents=True, exist_ok=True)
    ids_file = state_dir / "read-ids.json"
    ids_file.write_text('["t1","t2"]', encoding="utf-8")

    xna.reset_state(paths, "blogger", "alice")

    assert not ids_file.exists()


def test_reset_state_cli_prints_path(tmp_path):
    xna = load_module()
    stdout = io.StringIO()

    code = xna.main(
        ["--base-dir", str(tmp_path), "reset-state", "blogger", "alice"],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    assert "read-ids.json" in stdout.getvalue()


def test_reset_state_is_idempotent_when_no_file_exists(tmp_path):
    xna = load_module()

    ids_file = xna.reset_state(xna.AlertPaths(tmp_path), "blogger", "alice")

    assert not ids_file.exists()
