# CCE-Audio

## Packaging for Codex boot compatibility

Use `package_release.sh` to build a release ZIP without nesting the final folder inside the archive.

```bash
./package_release.sh
```

The script expects your final prepared contents in:

- `CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT/`

It then copies that directory's contents into a temporary `build/` directory and creates:

- `CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT_YYYYMMDD.zip`
