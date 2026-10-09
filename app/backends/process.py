"""Bounded CLI execution with stdin input and reliable child cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..errors import AIError, from_text

ROOT = Path(__file__).resolve().parents[2]


async def _stop(proc) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await proc.wait()


async def _spawn(args, env, limit=2**22):
    try:
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env or None,
            cwd=ROOT,
            limit=limit,
        )
    except OSError as exc:
        raise AIError(
            f"Cannot start {args[0]}; install the selected AI Backend"
        ) from exc


async def run_process(
    args: list[str], stdin: bytes, env: dict, timeout: float = 300
) -> bytes:
    proc = await _spawn(args, env)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=stdin), timeout)
    except asyncio.TimeoutError as exc:
        await _stop(proc)
        raise AIError("AI request timed out") from exc
    except BaseException:
        await _stop(proc)
        raise
    if proc.returncode != 0:
        raise from_text(
            stderr.decode("utf-8", "replace")
            + stdout.decode("utf-8", "replace")[-2000:],
            f"AI Backend exited {proc.returncode}; check its login and model",
        )
    return stdout


async def stream_process(
    args: list[str], stdin: bytes, env: dict, timeout: float = 300
):
    proc = await _spawn(args, env)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async def feed():
        proc.stdin.write(stdin)
        await proc.stdin.drain()
        proc.stdin.close()

    writer = asyncio.create_task(feed())
    errors = asyncio.create_task(proc.stderr.read())
    try:
        while True:
            raw = await asyncio.wait_for(
                proc.stdout.readline(), max(0, deadline - loop.time())
            )
            if not raw:
                break
            yield raw.decode("utf-8", "replace")
        await asyncio.wait_for(proc.wait(), max(0, deadline - loop.time()))
        if proc.returncode != 0:
            err = await errors
            raise from_text(
                err.decode("utf-8", "replace"),
                f"AI Backend exited {proc.returncode}; check its login and model",
            )
    except asyncio.TimeoutError as exc:
        raise AIError("AI stream timed out") from exc
    finally:
        for task in (writer, errors):
            if not task.done():
                task.cancel()
        await asyncio.gather(writer, errors, return_exceptions=True)
        await _stop(proc)
