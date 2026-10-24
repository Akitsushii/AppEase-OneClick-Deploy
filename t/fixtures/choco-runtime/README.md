# A shared runtime among a package's keys, for `t/test_uninstall_choco_runtime.py`

`paint.net.5.1.12.registry` is **real**: Chocolatey's own `.registry` from
`C:\ProgramData\chocolatey\.chocolatey\paint.net.5.1.12\` on a lab PC, copied
on 2026-10-09 after uninstall job 77 there was refused as `shared_runtime`.

It holds two keys. Paint.NET's own Windows Installer product,
`{C6A29A65-A91B-4F4A-A1B5-B904AC61255D}` (Registry64, `installerType="Msi"`),
and `Microsoft Edge WebView2 Runtime` under `Microsoft EdgeWebView`
(Registry32, `installerType="Unknown"`, `SystemComponent` and `NoRemove`
true), which came with paint.net onto a PC that did not have it. The VM's
`.registry` for the same version (`t/fixtures/choco/paint.net.5.1.12.registry`)
holds only Paint.NET's key: Windows 11 already had WebView2 there.

The server's installations row for it on that PC named Paint.NET and that
key, state `installed` - the install record was right, and the bug was the
agent's uninstall alone.

## Redacted before commit

The lab account's SID in `<user>` became
`S-1-5-21-1111111111-2222222222-3333333333-1001`, the same placeholder as in
`t/fixtures/choco` and `t/fixtures/choco-uninstall`. Nothing else was
changed: Chocolatey's BOM, its CRLF line endings and the missing final
newline are its own. The suite pins the file's SHA-256, and
`.gitattributes` marks `t/fixtures/**` as `-text`.
