# CCE-Audio

## Packaging release ZIPs for Codex boot

Use `scripts/package_release.sh` to create a ZIP that contains the **contents** of
`CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT` at the archive root (no extra nested
folder).

```bash
./scripts/package_release.sh
```

Optional arguments:

```bash
./scripts/package_release.sh <source_dir> <output_zip>
```
