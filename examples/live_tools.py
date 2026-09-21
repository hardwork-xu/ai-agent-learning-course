"""Optional paid API run. Prompt for a key without storing it in shell history."""

import getpass
import os

from agentlab.__main__ import main


if __name__ == "__main__":
    if not os.environ.get("AGENT_MODEL"):
        os.environ["AGENT_MODEL"] = input("Model ID from the provider console: ").strip()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = getpass.getpass("API key (hidden, held in this process only): ")
    raise SystemExit(main(["demo", "tools", "--live"]))
