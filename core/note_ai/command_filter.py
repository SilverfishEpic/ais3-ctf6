import re


class CommandFilter:
    """過濾不需要記錄的無意義指令"""

    TRIVIAL_PATTERNS: list[re.Pattern] = [
        re.compile(r) for r in [
            # Shell navigation & meta
            r'^cd\b', r'^ls\b', r'^dir\b', r'^clear\b', r'^cls\b', r'^exit\b',
            r'^pwd\b', r'^echo\b', r'^type\b', r'^which\b', r'^where\b',
            r'^man\b', r'^help\b', r'^alias\b', r'^unalias\b', r'^source\b',
            r'^\.\s+',  # source shorthand
            r'^history\b', r'^fc\b',
            # Editors / file managers
            r'^code\s*$', r'^code\s+\.\.?$', r'^notepad\b', r'^vim?\s*$',
            r'^nvim?\s*$', r'^nano\s*$', r'^emacs\s*$', r'^vi\b',
            r'^explorer\b', r'^open\b', r'^xdg-open\b', r'^nautilus\b',
            # File operations
            r'^mkdir\b', r'^rm\b', r'^del\b', r'^copy\b', r'^cp\b',
            r'^move\b', r'^mv\b', r'^ren\b', r'^rename\b', r'^tree\b',
            r'^touch\b', r'^cat\b', r'^head\b', r'^tail\b', r'^less\b',
            r'^more\b', r'^chmod\b', r'^chown\b', r'^ln\b',
            r'^find\b', r'^locate\b', r'^grep\b', r'^rg\b',
            # Process / job control
            r'^ps\b', r'^top\b', r'^htop\b', r'^kill\b', r'^jobs\b',
            r'^bg\b', r'^fg\b', r'^nohup\b',
            # Package managers (install/uninstall is noise)
            r'^conda\s+(activate|deactivate)\b',
            r'^pip\s+(install|uninstall)\b',
            r'^npm\s+(install|uninstall)\b',
            r'^cargo\s+(install|uninstall|build)\s*$',
            r'^apt\s+(install|remove)\b', r'^apt-get\s+(install|remove)\b',
            r'^brew\s+(install|uninstall)\b', r'^pacman\s+-S\b',
        ]
    ]

    @classmethod
    def is_trivial(cls, command: str) -> bool:
        cmd = command.strip()
        if not cmd or len(cmd) <= 2:
            return True
        return any(pattern.search(cmd) for pattern in cls.TRIVIAL_PATTERNS)
