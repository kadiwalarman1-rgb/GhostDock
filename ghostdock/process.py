"""Small, injectable subprocess boundary. Never invokes a shell."""

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    code: int
    stdout: str = ""
    stderr: str = ""


class OperationError(RuntimeError):
    pass


def command(args, timeout=30):
    environment = dict(os.environ, AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout, env=environment
        )
        return Result(result.returncode, result.stdout, result.stderr)
    except FileNotFoundError as exc:
        raise OperationError(f"required executable not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OperationError(f"{args[0]} timed out after {timeout}s; outcome may be unknown") from exc


def require_success(result, operation):
    if result.code:
        detail = result.stderr.strip()[:600] or result.stdout.strip()[:600]
        raise OperationError(f"{operation} failed (exit {result.code}): {detail}")
    return result.stdout
