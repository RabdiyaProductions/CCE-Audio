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

## Timeline editor (P21 step 2)

The project supports saved multi-track timelines with non-destructive placement data (`start_ms`, `duration_ms`, optional `bus_name`).

- API: `/api/timeline`, `/api/timeline/<project_id>`, `/api/timeline/<timeline_id>`, `/api/timeline/render`
- Render output assets are stored as `kind="timeline_mix"` and exported as WAV.
- Timeline render reuses the mixer pipeline and applies bus gain/pan settings when a track references a bus.
