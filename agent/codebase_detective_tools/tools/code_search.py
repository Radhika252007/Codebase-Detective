import subprocess
from pathlib import Path
from langchain_core.tools import tool
from config import PROJECT_ROOT, MAX_SEARCH_RESULTS


@tool
def search_code(pattern: str, file_glob: str = "*.js") -> str:
    """
    Search the target project's source code for a text pattern (plain text
    or simple regex), e.g. 'findMany', 'include:', 'for \\(' or a function
    name. Returns matching FILE PATHS AND LINE NUMBERS ONLY, not full file
    contents -- call read_file afterwards on anything that looks relevant.
    file_glob narrows which files are searched, e.g. '*.js', '*.ts',
    '*.prisma'. Use this when you have an idea of what you're looking for
    but not which file it's in.
    """
    root = Path(PROJECT_ROOT).resolve()
    try:
        result = subprocess.run(
            [
                "grep", "-rn", "--include", file_glob,
                "--exclude-dir=node_modules", "--exclude-dir=.git",
                "--exclude-dir=dist", "--exclude-dir=build",
                "-E", pattern, str(root),
            ],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return f"ERROR running search: {e}"

    # grep exits 1 for "no matches", which is not an error condition here.
    if result.returncode not in (0, 1):
        return f"ERROR: {result.stderr.strip()}"

    lines = result.stdout.strip().splitlines()
    if not lines:
        return "No matches found."

    shown = lines[:MAX_SEARCH_RESULTS]
    relative = [line.replace(str(root) + "/", "") for line in shown]

    out = "\n".join(relative)
    if len(lines) > MAX_SEARCH_RESULTS:
        out += f"\n... [{len(lines) - MAX_SEARCH_RESULTS} more matches not shown -- narrow your pattern or file_glob]"
    return out
