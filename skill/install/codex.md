## Codex install and updates

Preferred (marketplace one-liner, from a Codex CLI prompt):

```
codex plugin marketplace add DiamonDinoia/simdref
/plugins
```

Pick `asm-analysis` and enable it.

Manual install — Codex discovers skills from `.agents/skills/` (repo)
or `~/.agents/skills/` (user). See the
[Codex skills docs](https://developers.openai.com/codex/skills).

```bash
git clone https://github.com/DiamonDinoia/simdref.git ~/src/simdref
mkdir -p ~/.agents/skills
ln -sf ~/src/simdref/codex-skills/asm-analysis/skills/asm-analysis \
       ~/.agents/skills/asm-analysis
```

Refresh by updating the checkout: `(cd ~/src/simdref && git pull)`.
