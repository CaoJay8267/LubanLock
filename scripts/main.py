"""DotAgent 用户入口脚本。

用法：
    python scripts/main.py                 # 交互式菜单
    python scripts/main.py audit NAME      # 非交互，等价于 dotagent audit NAME
    python scripts/main.py --help

交互菜单提供：重置 Agent 编排、添加 Skill、审计、可视化、部署。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotagent.cli import main as cli_main


def _agents() -> list[str]:
    directory = REPO_ROOT / "resource" / "agent"
    if not directory.is_dir():
        return []
    return sorted(
        path.name
        for path in directory.iterdir()
        if path.is_dir() and (path / ".agents").is_dir()
    )


def _choose_agent() -> str | None:
    names = _agents()
    if names:
        print("\n可用 Agent：")
        for index, name in enumerate(names, start=1):
            print(f"  {index}. {name}")
        print("  0. 手动输入路径")
        choice = input("选择 Agent（序号或名称）: ").strip()
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(names):
                return names[index - 1]
            return None
        if choice:
            return choice
    return input("Agent 名称或路径: ").strip() or None


def _run(argv: list[str]) -> None:
    code = cli_main(argv)
    print(f"\n退出码：{code}")


def _menu() -> None:
    while True:
        print(
            "\n=== DotAgent 工具菜单 ===\n"
            "  1. 重置 Agent 编排（清理 build/dist/reports/lock 并重新构建）\n"
            "  2. 添加 Skill\n"
            "  3. 审计\n"
            "  4. 可视化\n"
            "  5. 部署（打包 + 投影）\n"
            "  0. 退出"
        )
        choice = input("请选择: ").strip()
        if choice == "0":
            return
        if choice == "1":
            agent = _choose_agent()
            if not agent:
                continue
            confirm = input(f"确认清理 {agent} 的生成产物？[y/N] ").strip().lower()
            if confirm == "y":
                _run(["reset", agent, "--yes", "--build"])
        elif choice == "2":
            agent = _choose_agent()
            if not agent:
                continue
            name = input("Skill 名称（kebab-case）: ").strip()
            description = input("Skill 描述: ").strip()
            argv = ["skill", "add", name, "--agent", agent]
            if description:
                argv.extend(["--description", description])
            _run(argv)
        elif choice == "3":
            agent = _choose_agent()
            if agent:
                _run(["audit", agent, "--fail-on", "never"])
        elif choice == "4":
            agent = _choose_agent()
            if agent:
                _run(["visualize", agent])
        elif choice == "5":
            agent = _choose_agent()
            if agent:
                _run(["deploy", agent])
        else:
            print("无效选项")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        try:
            _menu()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出")
        return 0
    parser = argparse.ArgumentParser(
        description="DotAgent 用户脚本（无参数时进入交互菜单）"
    )
    parser.add_argument("command", nargs="?", help="要转发的 dotagent 子命令")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="子命令参数")
    parsed = parser.parse_args(arguments)
    return cli_main([parsed.command, *parsed.args])


if __name__ == "__main__":
    raise SystemExit(main())
