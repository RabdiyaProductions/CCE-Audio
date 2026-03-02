# CCE-Audio

## Packaging for Codex boot compatibility

Use `package_release.sh` to build a release ZIP without nesting the final folder inside the archive.

```bash
./package_release.sh
```

The script expects your final prepared contents in:

- `CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT/`

It creates a flat archive in:

- `dist/CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT_YYYYMMDD.zip`

## Source-control policy for binaries

Binary assets (release bundles and test audio) are generated at build time and are **not** checked into source control.

- Ignore patterns in `.gitignore` prevent committing archives/media.
- Build artifacts go to `dist/` (ignored by Git).
