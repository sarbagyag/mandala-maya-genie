import os
from langchain_core.language_models import BaseChatModel


def get_llm() -> BaseChatModel:
    """Factory function to get the LLM based on LLM_PROVIDER env var."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            temperature=0.3,
            max_tokens=1024,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.3,
            max_tokens=1024,
        )
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=0.3,
            max_output_tokens=1024,
        )
    elif provider == "claude-code":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.getenv("CLAUDE_CODE_MODEL", "claude-sonnet-4-6"),
            base_url=os.getenv("CLAUDE_CODE_BASE_URL", "https://claude.mandalafoods.co/v1"),
            api_key=os.getenv("CLAUDE_CODE_API_KEY", "dummy"),
            temperature=0.3,
            max_tokens=1024,
        )
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")
