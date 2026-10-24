# Chocolatey records for `t/test_choco_verify.py`

Real records written by Chocolatey, not constructed ones: nine from the dev
machine (Chocolatey 2.5.1) and Paint.NET's two from the VM (job 263). The
suite pins each file's SHA-256, and `.gitattributes` marks this folder
`-text`, so neither git nor an editor can quietly change them.

## Redacted before commit

The same replacement was made in every file:

| Field | Files | Replaced with |
|---|---|---|
| `<user>`, the SID of the user who installed the package | all five `.registry` files | `S-1-5-21-1111111111-2222222222-3333333333-1001` |
| the user profile folder inside a path, `C:\Users\<name>\` | `GoogleChrome…registry` `InstallSource`; `gimp…registry` `InstallLocation` and `UninstallString` | `C:\Users\LabUser\` |

The dev machine and the VM had different SIDs; both became the one
placeholder. No test needs a real value. They need a well-formed SID, the same
SID wherever a snapshot is matched against a lookup, and a SID whose hive is
not loaded on the PC running the suite (the suite checks that). Where a test
needs the SID of the account running it (section 6), it substitutes that at
run time.

Nothing else was changed. No machine name appears in any file, the nuspecs
are public package metadata, and every file keeps Chocolatey's own bytes
otherwise - BOM, no final newline, and line endings, which are mixed CRLF and
LF inside the two helper nuspecs.

## Paint.NET's two files

They came from the VM as console text rather than as files, so two things are
not the VM's bytes:

- The nuspec's `<description>` was left out of the paste and holds a
  placeholder note instead. Nothing in the check reads it.
- A paste cannot carry line endings or a final newline. Both files were
  written with the UTF-8 BOM the console showed, CRLF, and no final newline,
  matching the dev machine's records of the same kind.
