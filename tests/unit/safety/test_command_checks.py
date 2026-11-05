"""Ring-3 rules on what a shell command runs and reads (embry0.safety.command_checks).

A Docker client is refused wherever the command runs one, at any depth, and a
file named .env wherever the command reads it. The rules match the commands
and paths embry0.safety.bash_paths reads from the command's parse, not its
text, so naming docker or .env in a file name or an argument passes.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from embry0.safety.policy import default_policy_for_agent, evaluate_policy

HOME = "/home/agent"
NL = "\n"


def _module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.command_checks")
    assert spec is not None, "embry0.safety.command_checks must match commands run and files read"
    import embry0.safety.command_checks as module

    return module


def _ring3(command: str) -> Any:
    """The Ring-3 verdict alone (the name check and the content and command checks), not the paths.

    The command runs in /workspace, evaluate_policy's default directory.
    """
    return evaluate_policy(default_policy_for_agent("developer"), "Bash", {"command": command})


_RUNS_DOCKER = [
    # The reviewer's forms: how a command can come to run docker.
    "docker ps",
    "sudo docker ps",
    "env docker ps",
    "$(which docker) ps",
    "`which docker` ps",
    "/usr/bin/docker ps",
    "/snap/bin/docker ps",
    "./docker ps",
    "command docker ps",
    "true" + NL + "docker ps",
    "true; docker ps",
    "true&&docker ps",
    '"docker" ps',
    "'docker' ps",
    "exec docker ps",
    "time -p docker ps",
    "nice -n 5 docker ps",
    "timeout -s KILL 5 docker ps",
    "xargs -I{} docker {} <<< ps",
    "find . -maxdepth 0 -exec docker ps \\;",
    "find . -maxdepth 0 -execdir docker ps \\;",
    "bash -c 'docker ps'",
    'sh -lc "docker ps"',
    "bash -c -- 'docker ps'",
    "env -S 'docker ps'",
    "env -i PATH=/usr/bin docker ps",
    "sudo -u root -E docker ps",
    "sudo -- docker ps",
    "( docker ps )",
    "{ docker ps; }",
    "if docker ps; then :; fi",
    "if false; then :; elif docker ps; then :; fi",
    "! docker ps",
    "case x in x) docker ps;; esac",
    "coproc docker ps",
    "watch docker ps",
    "strace -f docker ps",
    "taskset 1 docker ps",
    "chroot / docker ps",
    "unbuffer docker ps",
    "parallel docker ::: ps",
    ">/tmp/o docker ps",
    "2>/dev/null docker ps",
    "</dev/null docker ps",
    "FOO=1 docker ps",
    'FOO="a b" docker ps',
    "X=$(true) docker ps",
    "D=docker; $D ps",
    "d\\ocker ps",
    "do''cker ps",
    "$'docker' ps",
    "docker-compose up",
    "docker compose up",
    "docker-buildx build .",
    "docker-credential-desktop list",
    "dockerd &",
    "DOCKER_HOST=tcp://x docker ps",
    "ｄｏｃｋｅｒ ps",
    "Docker ps",  # any case: a case-insensitive file system finds docker by it
    "eval docker ps",
    'eval "docker ps"',
    "su -c 'docker ps'",
    "su - root -c 'docker ps'",
    "su root -c 'docker ps'",
    "script -qc 'docker ps' /dev/null",
    "flock /tmp/l -c 'docker ps'",
    # Values, aliases, functions and other runners.
    "alias d=docker" + NL + "d ps",
    "f(){ docker ps; }; f",
    "${D:-docker} ps",
    "x=dock; ${x}er ps",
    "$'\\x64ocker' ps",
    'a=(docker ps); "${a[@]}"',
    "printf -v d docker; $d ps",
    "declare d=docker; $d ps",
    'set -- docker ps; "$@"',
    "for d in docker; do $d ps; done",
    "IFS=,; x=docker,ps; $x",
    'x=docker; eval "$x ps"',
    "x=DOCKER; ${x,,} ps",
    "stdbuf -o0 docker ps",
    "ionice -c3 docker ps",
    "setsid docker ps",
    "echo ps | xargs docker",
    "find . -exec sh -c 'docker ps' \\;",
    "git -c alias.x='!docker ps' x",
    "busybox sh -c 'docker ps'",
    "run0 docker ps",
    "pkexec docker ps",
    "dbus-launch docker ps",
    "ssh-agent docker ps",
    "hash -p /usr/bin/docker d; d ps",
    "BASH_CMDS[d]=/usr/bin/docker; d ps",
    "echo $(docker ps)",
    "cat <(docker ps)",
    "trap 'docker ps' EXIT",
    "bash <<< 'docker ps'",
    "bash -s <<EOF" + NL + "docker ps" + NL + "EOF",
    "docker\\" + NL + " ps",
    "do\\" + NL + "cker ps",
    "PS4='$(docker ps)'; set -x; :",
    "env 'BASH_FUNC_x%%=() { docker ps; }' bash -c x",
    "mapfile -C 'docker ps' -c 1 a <<< x",
]

# A docker-* program the image installs: by its bare name (PATH finds it) or under a system directory.
_RUNS_INSTALLED_DOCKER = [
    "docker-compose up",
    "env docker-credential-pass get",
    "/usr/bin/docker-compose up",
    "/usr/libexec/docker/cli-plugins/docker-buildx build .",
    "/opt/tools/docker-foo",
    "/snap/bin/docker-compose up",
    "//usr/bin/docker-compose up",
    "/bin/../usr/bin/docker-compose up",
    "DOCKER-COMPOSE up",
    # docker and dockerd themselves, wherever they are.
    "./docker ps",
    "./bin/dockerd",
    "/workspace/docker ps",
]

# The repository's own scripts named docker-*: run by a relative path or from the workspace.
_RUNS_REPOSITORY_SCRIPT = [
    "./docker-entrypoint.sh",
    "./scripts/docker-build.sh",
    "./bin/docker-test",
    "scripts/docker-build.sh",
    "/workspace/scripts/docker-build.sh",
    "/usr/../workspace/docker-x",
    "bash ./docker-entrypoint.sh",
]

_NAMES_DOCKER = [
    "cat docker-compose.yml",
    "ls docker/",
    "npm run docker:build",
    "grep -rn docker .",
    "pip install docker",
    "git add docker-compose.yml",
    "echo docker",
    "vim Dockerfile",
    "make docker-image",
    "command -v docker",
    "type docker",
    "which docker",
    "docker_compose_file=x make",
    "git commit -m 'run docker ps in CI'",
    "python3 -m docker",
]

_READS_ENV = [
    # The reviewer's forms: how a command can read .env.
    "cat .env",
    "cat ./.env",
    "cat .env 2>/dev/null",
    "cat -- .env",
    "cat '.env'",
    "cat < .env",
    "cat .env|grep x",
    "cat */.env",
    "cat .env*",
    "cat .en?",
    "cat .e[n]v",
    "cat .{env,x}",
    "cat .env.",
    "cat ../.env",
    "cat app/.env;",
    "cat -n .env | head",
    "cat \\" + NL + ".env",
    "cat .ENV",
    "tac .env",
    "cat .env~",
    # Other readers, and values.
    "source .env",
    ". .env",
    ". ./.env",
    "less .env",
    "head -c 100 .env",
    "grep KEY .env",
    "sed -n p .env",
    "awk 1 .env",
    "cp .env /tmp/x",
    "git diff .env",
    "base64 .env",
    "xxd .env",
    "dd if=.env",
    "tar cf - .env",
    "while read l; do echo $l; done < .env",
    "mapfile a < .env",
    "exec 3< .env; cat <&3",
    "cat .e*",
    "cat .[!a]nv",
    "x=.env; cat $x",
    "x=.e; cat ${x}nv",
    "cat ${X:-.env}",
    "cat .e\\" + NL + "nv",
    "read -r f <<< .env; cat $f",
    # A glob that starts with a literal . could name .env under the default options: its later parts
    # are matched case-insensitively, so a repository's own dotfile glob is caught.
    "cat .EN?",
    "cat .En?",
    "cat .[eE]N?",
    "cat .en[Vv]",
    "cat .*",
    "cat ./.*",
    "cat dir/.*",
    "cat src/.*",
    # A glob a glob option in the same command may widen to match a leading . or ignore case.
    "shopt -s dotglob; cat *env",
    "GLOBIGNORE=x; cat *env",
    "shopt -s dotglob; cat *",
    "shopt -s nocaseglob; cat .EN[V]",
    "shopt -s nocaseglob; cat .en[Vv]",
    "bash - <<< 'cat .env'",
    # A child shell that runs a startup file first: the file may set glob options.
    "BASH_ENV=f bash -c 'cat *env'",
    "ENV=f sh -c 'cat *env'",
    "bash --rcfile f -i -c 'cat *env'",
    "bash --init-file f -c 'cat *env'",
    "bash -i -c 'cat *env'",
]

_NAMES_ENV = [
    "cat .env.example",
    "cat config/app.env",
    "cat .envrc",
    "cat .env.local",
    "cat <<EOF > .env.example" + NL + "X=1" + NL + "EOF",
    "cp .env.example .env",
    "echo X=1 >> .env",
    "grep -r KEY .",
    "find . -name '*.pyc' -delete",
    "find . -type f -name '*.orig' -exec rm {} +",
    "ls -la",
    "cat *.py",
    "shopt -s dotglob; cat *.py",
    "cat .env.*",
    "cat dir/$(ls)",
    "; ".join(f'for f in src/m{i}.py; do wc -l "$f"; done' for i in range(40)),
    # Plain globs under the default options: * and ? and [ab] do not match a leading dot, so none
    # reaches .env. The false positives the first version of this rule caused.
    "cat ?env",
    "cat *env",
    "cat *",
    "cat src/*",
    "ls -la *",
    "wc -l *",
    "grep -n foo *",
    "ls dir/*",
    "du -sh */",
    "cp templates/* .claude/commands/",
    "ls ~/*",
    "cat *.env",
    "cat [.]env",
    "bash -c 'cat *env'",
]


@pytest.mark.parametrize("command", _RUNS_DOCKER)
def test_a_command_that_runs_docker_is_refused(command: str) -> None:
    v = _ring3(command)
    assert not v.allowed and "container client" in v.reason, f"{command!r} runs a Docker client; Ring 3: {v}"


@pytest.mark.parametrize("command", _NAMES_DOCKER)
def test_a_command_that_only_names_docker_is_allowed(command: str) -> None:
    v = _ring3(command)
    assert v.allowed, f"{command!r} only names docker; Ring 3 refused it: {v.reason}"


@pytest.mark.parametrize("command", _RUNS_INSTALLED_DOCKER)
def test_a_docker_program_the_image_installs_is_refused(command: str) -> None:
    v = _ring3(command)
    assert not v.allowed and "container client" in v.reason, f"{command!r} runs a Docker client; Ring 3: {v}"


@pytest.mark.parametrize("command", _RUNS_REPOSITORY_SCRIPT)
def test_a_repository_script_named_docker_something_is_allowed(command: str) -> None:
    v = _ring3(command)
    assert v.allowed, f"{command!r} runs the repository's own script; Ring 3 refused it: {v.reason}"


@pytest.mark.parametrize("command", _READS_ENV)
def test_a_command_that_reads_env_is_refused(command: str) -> None:
    v = _ring3(command)
    assert not v.allowed and "file of secrets" in v.reason, f"{command!r} reads .env; Ring 3: {v}"


@pytest.mark.parametrize("command", _NAMES_ENV)
def test_a_command_that_reads_no_env_file_is_allowed(command: str) -> None:
    v = _ring3(command)
    assert v.allowed, f"{command!r} reads no file named .env; Ring 3 refused it: {v.reason}"


@pytest.mark.parametrize("option", ["shopt -s dotglob", "shopt -s nocaseglob", "GLOBIGNORE=x", "shopt -s extglob"])
def test_a_glob_option_in_the_command_widens_a_plain_glob(option: str) -> None:
    """``cat *env`` names no .env under the default options, but a glob option in the same command may."""
    assert _ring3("cat *env").allowed, "a plain *env matches no .env under the default options"
    v = _ring3(f"{option}; cat *env")
    assert not v.allowed and "file of secrets" in v.reason, f"{option} may let *env match .env: {v}"


def test_the_gate_reads_each_command_alone() -> None:
    """The gate keeps no state between commands: a plain glob is read with the default options unless
    this command may change them. That glob options do not carry from one Bash tool call to the next
    rests on the CLI, checked in throwaway sandbox images on 2.1.316 and 2.1.292 and reproduced by the
    reviewer through the real Bash tool, not on anything this test can see."""
    assert _ring3("cat *env").allowed, "with no option in this command, *env matches no .env"


@pytest.mark.parametrize(
    ("command", "found"),
    [
        ("docker ps", "docker"),
        ("/snap/bin/docker ps", "/snap/bin/docker"),
        ("ｄｏｃｋｅｒ ps", "ｄｏｃｋｅｒ"),
        ("docker-credential-pass get", "docker-credential-pass"),
        ("dockerize x", None),
        ("mydocker ps", None),
    ],
)
def test_a_command_is_matched_by_its_name(command: str, found: str | None) -> None:
    m = _module()
    from embry0.safety.bash_paths import bash_paths

    ran = m.runs_any(bash_paths(command, cwd="/workspace", home=HOME).commands, m.DANGEROUS_COMMANDS)
    assert ran == found, f"{command!r}: runs_any found {ran!r}, want {found!r}"


def test_a_command_the_parse_cannot_read_is_refused_by_the_command_checks_too() -> None:
    """Ring 3 alone (evaluate_policy, as the sandbox adapter calls it) does not let an unreadable command
    through on the grounds that Ring 2 refuses it."""
    v = _ring3("docker ps; (")
    assert not v.allowed and "cannot be checked" in v.reason, f"an unreadable command: {v}"


def test_the_patterns_module_does_not_import_the_parser() -> None:
    """embry0.safety.patterns is imported wherever embry0.safety is (the proxy image copies only some of
    embry0/safety): the command checks live in the policy, not in it."""
    import ast
    from pathlib import Path

    import embry0.safety.patterns as patterns

    imported = {
        node.module
        for node in ast.walk(ast.parse(Path(patterns.__file__).read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imported & {"embry0.safety.bash_paths", "embry0.safety.command_checks"}, sorted(imported)


def test_a_backslash_newline_joins_its_lines_for_every_pattern() -> None:
    from embry0.safety.patterns import normalize_command

    assert normalize_command("prin\\" + NL + "tenv") == "printenv", "bash joins a backslash-newline's lines"
    assert normalize_command("/snap/bin/docker ps") == "/snap/bin/docker ps", "a bin/ inside a path stays"
    assert normalize_command("/usr/bin/ssh x") == "ssh x", "a /usr/bin/ prefix is dropped"


@pytest.mark.parametrize(
    "unit",
    [
        "sudo -x a ",
        "env -u A ",
        "x=1 ",
        "a\\ ",
        "'docker' ",
        "$(",
        "eval ",
        "`echo docker` ",
        "cat .e* ",
    ],
)
def test_the_checks_stay_linear_on_hostile_input(unit: str) -> None:
    """The regular expressions these checks replace backtracked exponentially on repeated runner
    options; the parse's work, counted in fixed units (embry0.safety.shell_work), grows linearly in
    the command, and a verdict comes back at 60 KiB."""
    for module in ("embry0.safety.bash_paths", "embry0.safety.shell_work"):
        assert importlib.util.find_spec(module) is not None, (
            f"{module} must parse the command and count the work reading it costs, in fixed units"
        )
    from embry0.safety.bash_paths import BashSyntaxError, bash_paths
    from embry0.safety.shell_work import Work

    def spent(n: int) -> int:
        work = Work()
        try:
            bash_paths((unit * n)[: 60 * 1024], cwd="/workspace", home=HOME, work=work)
        except BashSyntaxError:
            pass  # refused at a limit: the count stops there
        return sum(work.spent.values())

    small, large = spent(1000), spent(4000)
    assert large <= 4 * small + 1000, f"{unit!r}: 1000 repetitions cost {small} units and 4000 cost {large}: not linear"
    verdict = _ring3((unit * (60 * 1024 // len(unit)))[: 60 * 1024])
    assert verdict.allowed or verdict.reason, f"{unit!r} at 60 KiB: {verdict}"


def test_a_glob_a_glob_option_widened_is_shown_as_written() -> None:
    v = _ring3("shopt -s dotglob; cat *env")
    assert not v.allowed and "'/workspace/*env'" in v.reason, f"the refusal must name the glob as written: {v.reason}"
