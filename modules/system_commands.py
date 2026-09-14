import subprocess


def run_shell(cmd: str, env=None):
    if not cmd or cmd.strip() == "":
        return ""

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERR: {e}"


def substitute_vars(cmd: str, env: dict):
    if not env:
        return cmd

    out = cmd
    for key, val in env.items():
        out = out.replace(f"${key}", val)
    return out


def split_value_and_attributes(output: str):
    if not output:
        return "", {}

    lines = output.split("\n")
    value = lines[0]
    attrs = {"lines": lines[1:]} if len(lines) > 1 else {}
    return value, attrs
