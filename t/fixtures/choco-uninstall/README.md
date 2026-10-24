# Chocolatey uninstall captures for `t/test_uninstall_choco.py`

Real output from Chocolatey 2.7.3 on the VM (Phase 2 step 9, 2026-10-03),
run in an admin Command Prompt as the lab account, with the flags the agent
uses and stdin closed:

```
"C:\ProgramData\chocolatey\bin\choco.exe" uninstall <id> -y --fail-on-autouninstaller --no-progress < NUL > file.txt 2>&1
echo %ERRORLEVEL% >> file.txt
```

So every `.txt` ends with the exit code `cmd` printed, signed (`-1`, not
`4294967295`), followed by a space. `c02-uninstall.txt` also has a
`%DATE% %TIME%` line before and after the run. The suite reads the last line
that is a bare number as the exit code. `c04-cleanup.txt` is the cleanup
command instead: `-y -n --skip-autouninstaller --no-progress`.

| File | What ran |
|---|---|
| `c01-unknown-id.txt` | an id that does not exist anywhere |
| `c01-real-id-not-installed.txt` | a real id that is not installed |
| `c02-uninstall.txt` | PuTTY (an MSI, no uninstall script), a normal uninstall |
| `c03-uninstall.txt` | PuTTY, after Settings > Apps had removed it |
| `c04-cleanup.txt` | Paint.NET, after Settings > Apps had removed it |
| `c05a-owner-first.txt` | vlc.install while the vlc wrapper was installed |
| `c05b-wrapper.txt` | the vlc wrapper, first |
| `c05b-owner.txt` | vlc.install, after its wrapper |
| `c07a-flag.txt` | vlc.install with its `uninstall.exe` deleted |
| `c07b-no-flag.txt` | the same, without `--fail-on-autouninstaller` |
| `putty.install.0.85.0.registry` | Chocolatey's `.registry` for that PuTTY |

`c07a-flag.txt` and `c07b-no-flag.txt` are byte-identical, which is the
finding: VLC's own uninstall script failed first, so the flag never came into
play. The suite asserts they are still identical.

Exit codes 1605, 1614, 3010, 1641 and 350 were never seen, so the suite makes
up their output from `c02-uninstall.txt`, and says so where it does. Nothing
in this folder is made up.

## Redacted before commit

`<user>` in `putty.install.0.85.0.registry`, the VM account's SID, became
`S-1-5-21-1111111111-2222222222-3333333333-1001`, the same placeholder as in
`t/fixtures/choco`. No other file holds a SID, a user name or a machine name.
Nothing else was changed: Chocolatey's BOM, its CRLF line endings and the
missing final newline are its own. The suite pins every file's SHA-256, and
`.gitattributes` marks `t/fixtures/**` as `-text`.

The vlc and vlc.install nuspecs and vlc.install's `.registry` are the 3.0.23
ones already in `t/fixtures/choco`. Step 9 ran 3.0.24, which Windows listed
under the same key name, `VLC media player`. PuTTY's nuspec was not captured,
so the suite writes a minimal one.
