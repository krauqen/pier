from enum import Enum


class AgentName(str, Enum):
    ORACLE = "oracle"
    NOP = "nop"
    INTERACTIVE_LAB = "interactive-lab"
    INTERACTIVE_SSH = "interactive-ssh"
    CLAUDE_CODE = "claude-code"
    CLAUDE_REMOTE = "claude-remote"
    CODEX = "codex"
    CURSOR_CLI = "cursor-cli"
    GEMINI_CLI = "gemini-cli"
    MINI_SWE_AGENT = "mini-swe-agent"
    SWE_AGENT = "swe-agent"
    OPENCODE = "opencode"
    TAIGA = "taiga"

    @classmethod
    def values(cls) -> set[str]:
        return {member.value for member in cls}
