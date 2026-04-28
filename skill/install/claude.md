## Claude Code install and updates

This skill lives at `skills/asm-analysis/SKILL.md` inside the simdref
source tree, which is also published as a Claude Code plugin
marketplace. Preferred install at the Claude Code prompt:

```
/plugin marketplace add DiamonDinoia/simdref
/plugin install asm-analysis@simdref
```

To refresh later:

```
/plugin marketplace update simdref
/plugin install asm-analysis@simdref
```

Manual alternatives, either symlink from a checkout for always-current
updates or install a one-off snapshot:

```bash
git clone https://github.com/DiamonDinoia/simdref.git ~/src/simdref
mkdir -p ~/.claude/skills
ln -sf ~/src/simdref/skills/asm-analysis ~/.claude/skills/asm-analysis
# later: (cd ~/src/simdref && git pull)  # updates skill in place

# or snapshot:
mkdir -p ~/.claude/skills/asm-analysis && \
  curl -fsSL https://raw.githubusercontent.com/DiamonDinoia/simdref/main/skills/asm-analysis/SKILL.md \
  -o ~/.claude/skills/asm-analysis/SKILL.md
```

If the user has an older PyPI-shipped copy, point them at the packaged
location for the upgrade diff:

```bash
pip show -f simdref | grep SKILL.md
```
