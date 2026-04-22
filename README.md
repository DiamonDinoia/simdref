# simdref docs-assets

Binary / image assets for the README and documentation. Kept on an
**orphan branch** with no shared history so cloning `main` stays fast.

Regenerate with `scripts/gen-screenshots.py` on the `main` branch, then
copy the outputs here and push:

```bash
cp /tmp/simdref-tui.svg img/tui.svg
# cp /tmp/simdref-web.png img/web.png   # after capturing with playwright
git add img/
git commit -m "docs: refresh screenshots"
git push origin docs-assets
```
