import re


class CommandFilter:
    """過濾不需要記錄的無意義指令"""

    TRIVIAL_PATTERNS: list[re.Pattern] = [
        re.compile(r) for r in [
            r'^cd\b', r'^ls\b', r'^dir\b', r'^clear\b', r'^cls\b', r'^exit\b',
            r'^code\s*$', r'^code\s+\.\.?$', r'^notepad\b', r'^ni\b', r'^mkdir\b',
            r'^rm\b', r'^del\b', r'^copy\b', r'^move\b', r'^ren\b', r'^tree\b',
            r'^echo\b', r'^pwd\b', r'^type\b', r'^cat\s+$', r'^start\b',
            r'^explorer\b', r'^conda\s+(activate|deactivate)\b',
            r'^pip\s+(install|uninstall)\b', r'^npm\s+(install|uninstall)\b',
        ]
    ]

    @classmethod
    def is_trivial(cls, command: str) -> bool:
        cmd = command.strip()
        if not cmd or len(cmd) <= 2:
            return True
        return any(pattern.search(cmd) for pattern in cls.TRIVIAL_PATTERNS)
