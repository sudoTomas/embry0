"""Production meters every agent run: the runner holds the meter, the meter reads the proxies once they run, and
reads them once more before they stop (embry0.execution.run_meter).

Without it the agent node finds no meter and charges every run its sandbox's figure: nothing fails, the proxies'
tallies are simply never read, which looks exactly like a deployment with no metering proxy (the #369 shape). Read
from the AST of ``lifespan``, which needs Docker and a database (test_maven_cache_wiring.py does the same).
"""

from __future__ import annotations

import ast
import pathlib

import embry0.api.app as app_module


def _calls(tree: ast.AST, text: str) -> list[int]:
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node).startswith(text)]


def test_the_runner_holds_the_meter_and_the_meter_reads_the_proxies_while_they_run() -> None:
    tree = ast.parse(pathlib.Path(app_module.__file__).read_text())
    built = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "RunMeter"]
    assert built, "app.py builds no RunMeter"
    assert ast.unparse(built[0].args[0]) == "proxy_mgr", "the meter is not given the proxy manager"
    assert "RunMeterRepository(db)" in ast.unparse(built[0]), "the meter keeps no tallies"
    runners = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "AgentRunner"
    ]
    assert runners and any(
        kw.arg == "run_meter" and ast.unparse(kw.value) == "run_meter" for kw in runners[0].keywords
    ), "the agent runner is not given the meter: no run would be metered"
    proxies_start = _calls(tree, "proxy_mgr.start(")
    meter_start = _calls(tree, "run_meter.start(")
    meter_stop = _calls(tree, "run_meter.stop(")
    proxies_stop = _calls(tree, "proxy_mgr.stop(")
    assert proxies_start and meter_start and meter_start[0] > proxies_start[0], "the meter starts before the proxies"
    assert meter_stop and proxies_stop and meter_stop[0] < proxies_stop[0], (
        "the meter's last read comes after the proxies stopped: what they counted since the last read is lost"
    )
