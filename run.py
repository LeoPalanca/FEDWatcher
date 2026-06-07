#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
DB_PATH = ROOT / "fedwatcher.db"
FRONTEND_DIR = ROOT / "fedwatcher"
API_HOST = "127.0.0.1"
API_PORT = 8000
WEB_HOST = "127.0.0.1"
WEB_PORT = 8080
REFRESH_SECONDS = 24 * 60 * 60
DEFAULT_LOOKBACK_DAYS = 90

BLUE = "\033[38;5;25m"
BLUE_SOFT = "\033[38;5;67m"
INK = "\033[38;5;236m"
MUTED = "\033[38;5;243m"
GREEN = "\033[38;5;29m"
YELLOW = "\033[38;5;136m"
RED = "\033[38;5;124m"
BOLD = "\033[1m"
RESET = "\033[0m"

DEFAULT_WEIGHTS = {
    "statement": {
        "forward_guidance": 0.45,
        "inflation": 0.25,
        "labor_market": 0.15,
        "general": 0.15,
    },
}

WEIGHT_PRESETS = {
    "1": (
        "FedWatcher default",
        DEFAULT_WEIGHTS,
    ),
    "2": (
        "Forward-guidance heavy",
        {
            "statement": {
                "forward_guidance": 0.55,
                "inflation": 0.20,
                "labor_market": 0.10,
                "general": 0.15,
            },
        },
    ),
    "3": (
        "Balanced",
        {
            "statement": {
                "forward_guidance": 0.25,
                "inflation": 0.25,
                "labor_market": 0.25,
                "general": 0.25,
            },
        },
    ),
}


def color(text: str, code: str) -> str:
    if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
        return text
    return f"{code}{text}{RESET}"


def info(message: str) -> None:
    print(color("* ", BLUE) + message)


def ok(message: str) -> None:
    print(color("[ok] ", GREEN) + message)


def warn(message: str) -> None:
    print(color("! ", YELLOW) + message)


def fail(message: str) -> None:
    print(color("Error: ", RED) + message, file=sys.stderr)
    raise SystemExit(1)


def header() -> None:
    print()
    print(color("Fed", INK) + color("Watcher", BLUE) + color(".", BLUE))
    print(color("Local setup and launcher", MUTED))
    print(color("-" * 34, BLUE_SOFT))
    sys.stdout.flush()


def prompt_choice(prompt: str, choices: Iterable[str], default: str) -> str:
    valid = set(choices)
    suffix = f" [{default}] " if default else " "
    while True:
        try:
            value = input(color(prompt, BLUE) + suffix).strip()
        except EOFError:
            print()
            return default
        if not value and default:
            return default
        if value in valid:
            return value
        warn(f"Choose one of: {', '.join(sorted(valid))}")


def prompt_text(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = " [keep existing] " if default else " "
    reader = getpass.getpass if secret else input
    try:
        value = reader(color(prompt, BLUE) + suffix).strip()
    except EOFError:
        print()
        return default
    return default if not value else value


def prompt_float(prompt: str, default: float) -> float:
    while True:
        try:
            raw = input(color(prompt, BLUE) + f" [{default:.2f}]\n> ").strip()
        except EOFError:
            print()
            return default
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            warn("Enter a number between 0 and 1.")
            continue
        if 0 <= value <= 1:
            return value
        warn("Enter a number between 0 and 1.")


def prompt_int(prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        try:
            raw = input(color(prompt, BLUE) + f" [{default}] ").strip()
        except EOFError:
            print()
            return default
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            warn(f"Enter an integer greater than or equal to {minimum}.")
            continue
        if value >= minimum:
            return value
        warn(f"Enter an integer greater than or equal to {minimum}.")


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env(values: dict[str, str]) -> None:
    ordered_keys = [
        "FRED_API_KEY",
        "OPENROUTER_API_KEY",
        "API_TOKEN",
        "FED_BASE_URL",
        "FED_CALENDAR_PATH",
        "DATABASE_URL",
        "FEDWATCHER_DB_PATH",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DB_HOST",
        "DB_USER",
        "DB_PASSWORD",
        "DB_NAME",
        "DB_PORT",
    ]
    lines = [
        "# FedWatcher local environment",
        "# Created by python run.py setup",
        "",
    ]
    seen = set()
    for key in ordered_keys:
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
    for key in sorted(k for k in values if k not in seen):
        lines.append(f"{key}={values[key]}")
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def ensure_env(interactive: bool) -> dict[str, str]:
    values = load_env()
    if not values and ENV_EXAMPLE_PATH.exists():
        values = load_env(ENV_EXAMPLE_PATH)
        info("Using .env.example as the starting point.")

    values.setdefault("FED_BASE_URL", "https://www.federalreserve.gov")
    values.setdefault("FED_CALENDAR_PATH", "/monetarypolicy/fomccalendars.htm")
    values.setdefault("DATABASE_URL", "sqlite:///fedwatcher.db")
    values.setdefault("FEDWATCHER_DB_PATH", "fedwatcher.db")
    values.setdefault("API_TOKEN", "")

    if interactive:
        print()
        print(color("API keys", BOLD))
        values["OPENROUTER_API_KEY"] = prompt_text(
            "OpenRouter API key",
            default=values.get("OPENROUTER_API_KEY", ""),
            secret=True,
        )
        values["FRED_API_KEY"] = prompt_text(
            "FRED API key",
            default=values.get("FRED_API_KEY", ""),
            secret=True,
        )
        source_choice = prompt_choice(
            "Fed source: 1 official Federal Reserve, 2 local FakeFed",
            choices=("1", "2"),
            default="1",
        )
        values["FED_BASE_URL"] = (
            "https://fakefed.ellep.it"
            if source_choice == "2"
            else "https://www.federalreserve.gov"
        )
    else:
        values.setdefault("OPENROUTER_API_KEY", "")
        values.setdefault("FRED_API_KEY", "")

    write_env(values)
    ok(f"Environment written to {ENV_PATH.relative_to(ROOT)}")
    return values


def install_requirements() -> None:
    pip = [sys.executable, "-m", "pip"]
    subprocess.run(pip + ["install", "--upgrade", "pip"], cwd=ROOT, check=True)
    subprocess.run(pip + ["install", "-r", "requirements.txt"], cwd=ROOT, check=True)
    ok("Python requirements installed.")


def init_db() -> None:
    if not SCHEMA_PATH.exists():
        fail("db/schema.sql is missing.")
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(sql)
        conn.commit()
    ok(f"SQLite database ready at {DB_PATH.relative_to(ROOT)}")


def set_weights(preset: dict[str, dict[str, float]]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM weights WHERE doc_type NOT IN ('statement')")
        for doc_type, dimensions in preset.items():
            total = round(sum(dimensions.values()), 8)
            if total != 1:
                fail(f"{doc_type} weights sum to {total}, expected 1.0")
            for dimension, weight in dimensions.items():
                conn.execute(
                    """
                    INSERT INTO weights (doc_type, dimension, weight, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(doc_type, dimension)
                    DO UPDATE SET
                        weight = excluded.weight,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (doc_type, dimension, weight),
                )
        conn.commit()
    ok("Analyst section weights saved.")


def choose_weights(interactive: bool, preset_name: str | None = None) -> None:
    init_db()
    if preset_name:
        lookup = {
            "default": WEIGHT_PRESETS["1"][1],
            "forward-guidance-heavy": WEIGHT_PRESETS["2"][1],
            "balanced": WEIGHT_PRESETS["3"][1],
        }
        if preset_name not in lookup:
            fail("Unknown weight preset. Use default, forward-guidance-heavy, or balanced.")
        set_weights(lookup[preset_name])
        show_weights()
        return

    if not interactive:
        set_weights(DEFAULT_WEIGHTS)
        return

    print()
    print(color("Analyst section weights", BOLD))
    for key, (label, weights) in WEIGHT_PRESETS.items():
        statement = weights["statement"]
        summary = ", ".join(f"{k} {v:.2f}" for k, v in statement.items())
        print(f"{key}) {label}: {summary}")
    print("4) Custom weights")
    choice = prompt_choice("Choose weight preset", choices=("1", "2", "3", "4"), default="1")
    if choice == "4":
        set_weights(prompt_custom_weights())
    else:
        set_weights(WEIGHT_PRESETS[choice][1])
    show_weights()


def prompt_custom_weights() -> dict[str, dict[str, float]]:
    weights: dict[str, dict[str, float]] = {}
    defaults = DEFAULT_WEIGHTS["statement"]
    print()
    print(color("STATEMENT weights", BLUE))
    print(color("Values must sum to 1.00. Press Enter to keep the shown default.", MUTED))
    while True:
        current: dict[str, float] = {}
        for dimension, default in defaults.items():
            current[dimension] = prompt_float(f"  {dimension}", default)
        total = round(sum(current.values()), 8)
        if total == 1:
            weights["statement"] = current
            break
        warn(f"Statement weights sum to {total:.2f}, expected 1.00. Re-enter this group.")
    return weights


def show_weights() -> None:
    if not DB_PATH.exists():
        warn("No database yet. Run python run.py setup first.")
        return
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT doc_type, dimension, weight
            FROM weights
            ORDER BY doc_type, dimension
            """
        ).fetchall()
    if not rows:
        warn("No weights configured.")
        return
    current = None
    total = 0.0
    for doc_type, dimension, weight in rows:
        if current and current != doc_type:
            print(color(f"  total {total:.2f}", MUTED))
            total = 0.0
        if current != doc_type:
            current = doc_type
            print(color(doc_type.upper(), BLUE))
        print(f"  {dimension:<18} {weight:.2f}")
        total += float(weight)
    print(color(f"  total {total:.2f}", MUTED))


def setup(args: argparse.Namespace) -> None:
    header()
    if args.install is None and sys.stdin.isatty():
        install = prompt_choice("Install/update Python requirements?", ("y", "n"), "y") == "y"
    else:
        install = bool(args.install)
    if install:
        install_requirements()
    env_values = ensure_env(interactive=not args.non_interactive)
    choose_weights(interactive=not args.non_interactive, preset_name=args.weights)
    print()
    if not args.non_interactive:
        post_setup_actions(env_values)
        ok("Local setup complete.")
    else:
        ok("Local setup complete.")
        print(f"Run {color('python run.py dev', BLUE)} to start the local dashboard.")


def post_setup_actions(env_values: dict[str, str]) -> None:
    print()
    analyst_limit = prompt_int(
        "Maximum statements AnalystAgent may process per run",
        default=2,
        minimum=1,
    )
    lookback_days = prompt_int(
        "How many days back should MonitorAgent fetch?",
        default=DEFAULT_LOOKBACK_DAYS,
        minimum=1,
    )
    if prompt_choice("Download/update FRED macro data now?", ("y", "n"), "y") == "y":
        run_macro_download(env_values=env_values)
    if prompt_choice("Start 24h Monitor -> Analyst -> Strategist loop now?", ("y", "n"), "y") == "y":
        start_dashboard = prompt_choice("Start the localhost dashboard now?", ("y", "n"), "y") == "y"
        if start_dashboard:
            start_pipeline_thread(
                env_values=env_values,
                analyst_limit=analyst_limit,
                lookback_days=lookback_days,
            )
            dev(argparse.Namespace(api_port=API_PORT, web_port=WEB_PORT, reload=False))
        else:
            run_pipeline_loop(
                env_values=env_values,
                once=False,
                analyst_limit=analyst_limit,
                lookback_days=lookback_days,
            )
        return
    if prompt_choice("Run LLM analysis on unprocessed statements now?", ("y", "n"), "n") == "y":
        run_statement_analysis(limit=analyst_limit, env_values=env_values)
    if prompt_choice("Start the localhost dashboard now?", ("y", "n"), "y") == "y":
        dev(argparse.Namespace(api_port=API_PORT, web_port=WEB_PORT, reload=False))
    else:
        print(f"Run {color('python run.py dev', BLUE)} to start the local dashboard.")


def start_pipeline_thread(
    env_values: dict[str, str],
    analyst_limit: int,
    lookback_days: int,
) -> threading.Thread:
    def target() -> None:
        try:
            run_pipeline_loop(
                env_values=env_values,
                once=False,
                analyst_limit=analyst_limit,
                lookback_days=lookback_days,
            )
        except SystemExit as exc:
            warn(f"Pipeline stopped with exit code {exc.code}.")

    thread = threading.Thread(target=target, name="fedwatcher-pipeline", daemon=True)
    thread.start()
    ok("24h Monitor -> Analyst -> Strategist loop started in the background.")
    return thread


def run_command_step(label: str, command: list[str], env_values: dict[str, str] | None = None) -> None:
    print()
    print(color(label, BOLD))
    info(" ".join(command))
    env = os.environ.copy()
    env.update(env_values or load_env())
    try:
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        fail(f"{label} failed with exit code {exc.returncode}.")


def run_macro_download(env_values: dict[str, str] | None = None) -> None:
    env = env_values or load_env()
    if not env.get("FRED_API_KEY"):
        warn("FRED_API_KEY is empty. Skipping FRED macro download.")
        return
    init_db()
    run_command_step(
        "FRED macro download",
        [sys.executable, "-m", "agents.monitor_fred", "--db", str(DB_PATH)],
        env_values=env,
    )


def run_pipeline_cycle(
    env_values: dict[str, str] | None = None,
    analyst_limit: int = 2,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> None:
    env = env_values or load_env()
    init_db()
    run_command_step(
        "MonitorAgent: Fed documents",
        [
            sys.executable,
            "-m",
            "agents.monitor_fed",
            "--db",
            str(DB_PATH),
            "--mode",
            "recent",
            "--lookback-days",
            str(lookback_days),
        ],
        env_values=env,
    )
    if not env.get("OPENROUTER_API_KEY"):
        warn("OPENROUTER_API_KEY is empty. Skipping AnalystAgent LLM step.")
    else:
        run_command_step(
            "AnalystAgent: statement LLM scoring",
            [
                sys.executable,
                "agents/w_agent.py",
                "--db",
                str(DB_PATH),
                "--limit",
                str(analyst_limit),
            ],
            env_values=env,
        )
    run_command_step(
        "StrategistAgent: signal update",
        [sys.executable, "agents/strategist.py", "--db", str(DB_PATH)],
        env_values=env,
    )


def run_pipeline_loop(
    env_values: dict[str, str] | None = None,
    once: bool = False,
    analyst_limit: int = 2,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    refresh_seconds: int = REFRESH_SECONDS,
) -> None:
    print()
    print(color("FedWatcher agent pipeline", BOLD))
    print("Order: MonitorAgent -> AnalystAgent -> StrategistAgent")
    print(f"MonitorAgent lookback: {lookback_days} days")
    if once:
        run_pipeline_cycle(
            env_values=env_values,
            analyst_limit=analyst_limit,
            lookback_days=lookback_days,
        )
        return
    print(f"Refresh: every {refresh_seconds // 3600}h. Press Ctrl+C to stop.")
    try:
        while True:
            started = time.strftime("%Y-%m-%d %H:%M:%S")
            print()
            print(color(f"Pipeline cycle started {started}", BLUE))
            run_pipeline_cycle(
                env_values=env_values,
                analyst_limit=analyst_limit,
                lookback_days=lookback_days,
            )
            print(color(f"Next cycle in {refresh_seconds // 3600}h.", MUTED))
            time.sleep(refresh_seconds)
    except KeyboardInterrupt:
        print()
        warn("Stopped 24h agent refresh loop.")


def run_statement_analysis(limit: int, env_values: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    env.update(env_values or load_env())
    if not env.get("OPENROUTER_API_KEY"):
        warn("OPENROUTER_API_KEY is empty. Skipping LLM analysis.")
        return
    info(f"Running statement analysis with limit={limit}. This may call the OpenRouter API.")
    run_command_step(
        "AnalystAgent: statement LLM scoring",
        [sys.executable, "agents/w_agent.py", "--db", str(DB_PATH), "--limit", str(limit)],
        env_values=env,
    )


class ProxyStaticHandler(SimpleHTTPRequestHandler):
    api_port = API_PORT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self.proxy_api()
            return
        super().do_GET()

    def proxy_api(self) -> None:
        target = f"http://{API_HOST}:{self.api_port}{self.path}"
        try:
            with urllib.request.urlopen(target, timeout=20) as response:
                body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = f'{{"detail":"Local API proxy failed: {exc}"}}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(color("web ", BLUE_SOFT) + fmt % args)


def wait_for_api(api_port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://{API_HOST}:{api_port}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return response.status == 200
        except Exception:
            time.sleep(0.25)
    return False


def dev(args: argparse.Namespace) -> None:
    if not ENV_PATH.exists() or not DB_PATH.exists():
        warn("Missing .env or fedwatcher.db. Running setup first.")
        setup(argparse.Namespace(install=False, non_interactive=True, weights=None))

    uvicorn = shutil.which("uvicorn")
    if not uvicorn:
        fail("uvicorn is not installed. Run python run.py setup and choose requirements install.")

    env = os.environ.copy()
    env.update(load_env())
    api_cmd = [uvicorn, "app.main:app", "--host", API_HOST, "--port", str(args.api_port)]
    if args.reload:
        api_cmd.append("--reload")
    api = subprocess.Popen(api_cmd, cwd=ROOT, env=env)

    try:
        ProxyStaticHandler.api_port = args.api_port
        api_ready = wait_for_api(args.api_port)
        if not api_ready and api.poll() is not None:
            fail(f"API server exited before it was ready. Check port {args.api_port} and dependencies.")
        server = ThreadingHTTPServer((WEB_HOST, args.web_port), ProxyStaticHandler)
        print()
        print(color("Local dashboard", BOLD))
        print(f"  Open this URL: {color(f'http://{WEB_HOST}:{args.web_port}', BLUE)}")
        if api_ready:
            print(color(f"  API proxy: /api/* -> http://{API_HOST}:{args.api_port}", MUTED))
        else:
            warn("API did not answer health check yet; dashboard API panels may stay empty.")
        print(color("Press Ctrl+C to stop.", MUTED))
        server.serve_forever()
    except PermissionError as exc:
        fail(f"Could not bind local web server on {WEB_HOST}:{args.web_port}: {exc}")
    except KeyboardInterrupt:
        print()
        warn("Stopping local services.")
    finally:
        if api.poll() is None:
            api.send_signal(signal.SIGINT)
        try:
            api.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="FedWatcher local setup and launcher.")
    subparsers = parser.add_subparsers(dest="command")

    setup_parser = subparsers.add_parser("setup", help="Configure .env, database, and weights.")
    setup_parser.add_argument("--install", action="store_true", default=None, help="Install requirements without prompting.")
    setup_parser.add_argument("--no-install", action="store_false", dest="install", help="Skip requirements install.")
    setup_parser.add_argument("--non-interactive", action="store_true", help="Use defaults and do not prompt.")
    setup_parser.add_argument(
        "--weights",
        choices=("default", "forward-guidance-heavy", "balanced"),
        help="Weight preset to apply.",
    )

    dev_parser = subparsers.add_parser("dev", help="Start FastAPI plus a local static dashboard proxy.")
    dev_parser.add_argument("--api-port", type=int, default=API_PORT, help=f"FastAPI port. Default: {API_PORT}")
    dev_parser.add_argument("--web-port", type=int, default=WEB_PORT, help=f"Dashboard port. Default: {WEB_PORT}")
    dev_parser.add_argument("--reload", action="store_true", help="Run uvicorn with file-watch reload.")
    analyze_parser = subparsers.add_parser("analyze", help="Run LLM analysis on unprocessed statements.")
    analyze_parser.add_argument("--limit", type=int, default=5, help="Maximum number of statements to analyze.")
    subparsers.add_parser("macro", help="Download/update FRED macro data.")
    pipeline_parser = subparsers.add_parser(
        "pipeline",
        help="Run Monitor -> Analyst -> Strategist once or every 24h.",
    )
    pipeline_parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    pipeline_parser.add_argument("--limit", type=int, default=2, help="Maximum statements for AnalystAgent per cycle.")
    pipeline_parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"MonitorAgent recent-mode lookback window. Default: {DEFAULT_LOOKBACK_DAYS}.",
    )
    pipeline_parser.add_argument(
        "--refresh-hours",
        type=float,
        default=24.0,
        help="Refresh interval for loop mode. Default: 24.",
    )
    subparsers.add_parser("weights", help="Show current analyst section weights.")

    args = parser.parse_args()
    if args.command == "dev":
        header()
        dev(args)
    elif args.command == "analyze":
        header()
        run_statement_analysis(limit=args.limit)
    elif args.command == "macro":
        header()
        run_macro_download()
    elif args.command == "pipeline":
        header()
        if args.limit < 1:
            fail("--limit must be at least 1.")
        if args.lookback_days < 1:
            fail("--lookback-days must be at least 1.")
        if args.refresh_hours <= 0:
            fail("--refresh-hours must be greater than 0.")
        run_pipeline_loop(
            once=args.once,
            analyst_limit=args.limit,
            lookback_days=args.lookback_days,
            refresh_seconds=int(args.refresh_hours * 3600),
        )
    elif args.command == "weights":
        header()
        show_weights()
    else:
        if args.command is None:
            args.install = None
            args.non_interactive = False
            args.weights = None
        setup(args)


if __name__ == "__main__":
    main()
