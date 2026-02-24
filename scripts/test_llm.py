#!/usr/bin/env python3
"""Smoke test for the configured LLM provider."""

import os
import sys
from pathlib import Path

# Load .env from project root
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "cognitive-core"))

from llm.client import get_llm

def main():
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    print(f"Provider : {provider}")

    if provider == "claude-code":
        print(f"Base URL : {os.getenv('CLAUDE_CODE_BASE_URL')}")
        print(f"Model    : {os.getenv('CLAUDE_CODE_MODEL', 'claude-sonnet-4-6')}")

    print("Initialising LLM client...")
    llm = get_llm()

    print("Sending test message...")
    from langchain_core.messages import HumanMessage
    response = llm.invoke([HumanMessage(content="Reply with exactly: OK")])

    print(f"Response : {response.content}")
    print("Smoke test passed.")

if __name__ == "__main__":
    main()
